from __future__ import annotations

"""Memory-safe gate-native QAOA helpers for QOptima.

This module intentionally avoids scipy.sparse, SparsePauliOp, QAOAAnsatz and
matrix factorization.  QUBO costs are diagonal in the computational basis, so
QOptima can implement the QAOA cost unitary directly with RZ/RZZ gates and
measure the exact statevector expectation against a compact energy table.
"""

import gc
import math
import os
from typing import Any

import numpy as np


def system_memory_gb() -> float | None:
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        if pages > 0 and page_size > 0:
            return float(pages * page_size) / (1024.0 ** 3)
    except (ValueError, OSError, AttributeError):
        pass
    return None


def local_memory_plan(
    qubits: int,
    *,
    ram_gb: float | None = None,
    memory_fraction: float = 0.48,
    working_multiplier: float = 12.0,
) -> dict[str, Any]:
    """Conservative statevector memory estimate.

    A complex128 statevector is 16 * 2**n bytes.  Qiskit needs temporary work
    buffers and QOptima also keeps probabilities / a diagonal energy table, so
    we budget a large multiple of raw statevector size rather than pretending
    the raw vector is the whole memory footprint.
    """
    n = max(0, int(qubits))
    physical = system_memory_gb() if ram_gb is None else float(ram_gb)
    raw = 16 * (2 ** n)
    energy = 8 * (2 ** n)
    probability = 8 * (2 ** n)
    estimated_peak = int(max(raw + energy + probability, raw * float(working_multiplier)) + 256 * 1024**2)
    budget = None if physical is None else int(max(1.0, physical) * (1024.0 ** 3) * float(memory_fraction))
    return {
        "qubits": n,
        "system_memory_gb": physical,
        "raw_statevector_bytes": int(raw),
        "energy_table_bytes": int(energy),
        "probability_bytes": int(probability),
        "estimated_peak_bytes": int(estimated_peak),
        "memory_budget_bytes": budget,
        "safe": bool(budget is None or estimated_peak <= budget),
        "memory_fraction": float(memory_fraction),
        "working_multiplier": float(working_multiplier),
    }


def safe_statevector_qubit_cap(
    *,
    requested_cap: int = 24,
    ram_gb: float | None = None,
    memory_fraction: float = 0.48,
    hard_cap: int = 24,
) -> int:
    requested = max(8, min(int(requested_cap), int(hard_cap)))
    for n in range(requested, 7, -1):
        if local_memory_plan(n, ram_gb=ram_gb, memory_fraction=memory_fraction)["safe"]:
            return n
    return 8


def qubo_ising_coefficients(qubo) -> tuple[float, np.ndarray, dict[tuple[int, int], float]]:
    """Map E(x) to constant + sum z_i Z_i + sum zz_ij Z_i Z_j."""
    n = int(qubo.num_variables)
    z = np.zeros(n, dtype=float)
    zz: dict[tuple[int, int], float] = {}
    constant = float(qubo.constant)
    for i, value in enumerate(np.asarray(qubo.linear, dtype=float)):
        a = float(value)
        constant += a / 2.0
        z[i] += -a / 2.0
    for (i, j), value in qubo.quadratic.items():
        b = float(value)
        constant += b / 4.0
        z[int(i)] += -b / 4.0
        z[int(j)] += -b / 4.0
        zz[(int(i), int(j))] = zz.get((int(i), int(j)), 0.0) + b / 4.0
    return constant, z, zz


def build_gate_qaoa_circuit(qubo, params: np.ndarray, reps: int):
    """Construct a standard QAOA circuit directly from the QUBO.

    Cost layers use only RZ/RZZ gates. Mixer layers use RX gates.  No sparse
    Hamiltonian matrix is constructed at any point.
    """
    from qiskit import QuantumCircuit

    n = int(qubo.num_variables)
    p = max(1, int(reps))
    values = np.asarray(params, dtype=float).reshape(-1)
    if values.size != 2 * p:
        raise ValueError(f"QAOA p={p} requires {2*p} parameters, got {values.size}")
    gammas = values[:p]
    betas = values[p:]
    _constant, z, zz = qubo_ising_coefficients(qubo)

    qc = QuantumCircuit(n)
    qc.h(range(n))
    two_pi = 2.0 * math.pi
    for layer in range(p):
        gamma = float(gammas[layer])
        beta = float(betas[layer])
        for i, coeff in enumerate(z):
            if abs(float(coeff)) > 1e-15:
                qc.rz(math.remainder(2.0 * gamma * float(coeff), two_pi), i)
        for (i, j), coeff in zz.items():
            if abs(float(coeff)) > 1e-15:
                qc.rzz(math.remainder(2.0 * gamma * float(coeff), two_pi), i, j)
        mixer_angle = math.remainder(2.0 * beta, two_pi)
        for i in range(n):
            qc.rx(mixer_angle, i)
    return qc


def build_energy_table(qubo) -> np.ndarray:
    """Return E(x) for every computational basis state without sparse matrices.

    The recursive construction is O(n*2**n) and is built once per QUBO.  It is
    much safer than materializing a 2**n by 2**n Hamiltonian.
    """
    n = int(qubo.num_variables)
    incoming: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for (i, j), value in qubo.quadratic.items():
        a, b = sorted((int(i), int(j)))
        incoming[b].append((a, float(value)))

    energies = np.array([float(qubo.constant)], dtype=np.float64)
    linear = np.asarray(qubo.linear, dtype=float)
    for k in range(n):
        size = energies.size
        if incoming[k]:
            idx = np.arange(size, dtype=np.uint64)
            add = np.full(size, float(linear[k]), dtype=np.float64)
            for i, coeff in incoming[k]:
                add += float(coeff) * ((idx >> np.uint64(i)) & np.uint64(1))
            second = energies + add
            del idx, add
        else:
            second = energies + float(linear[k])
        energies = np.concatenate((energies, second))
        del second
    return energies


def statevector_expectation(qubo, params: np.ndarray, reps: int, energy_table: np.ndarray):
    """Evaluate <E> for a gate-native QAOA statevector."""
    from qiskit.quantum_info import Statevector

    circuit = build_gate_qaoa_circuit(qubo, params, reps)
    state = Statevector.from_instruction(circuit)
    data = np.asarray(state.data)
    probabilities = np.square(data.real) + np.square(data.imag)
    expectation = float(np.dot(probabilities, energy_table))
    return expectation, circuit, state, probabilities


def sample_indices(probabilities: np.ndarray, *, shots: int, rng: np.random.Generator) -> np.ndarray:
    shots = max(1, int(shots))
    probs = np.asarray(probabilities, dtype=float)
    total = float(probs.sum())
    if not np.isfinite(total) or total <= 0:
        raise RuntimeError("QAOA statevector produced invalid probabilities")
    if abs(total - 1.0) > 1e-10:
        probs = probs / total
    return np.asarray(rng.choice(probs.size, size=shots, replace=True, p=probs), dtype=np.uint64)


def energies_for_indices(qubo, indices: np.ndarray) -> np.ndarray:
    idx = np.asarray(indices, dtype=np.uint64).reshape(-1)
    values = np.full(idx.shape, float(qubo.constant), dtype=np.float64)
    linear = np.asarray(qubo.linear, dtype=float)
    for i, coeff in enumerate(linear):
        if abs(float(coeff)) > 1e-15:
            values += float(coeff) * ((idx >> np.uint64(i)) & np.uint64(1))
    for (i, j), coeff in qubo.quadratic.items():
        if abs(float(coeff)) <= 1e-15:
            continue
        bi = (idx >> np.uint64(i)) & np.uint64(1)
        bj = (idx >> np.uint64(j)) & np.uint64(1)
        values += float(coeff) * (bi & bj)
    return values


def bits_from_index(index: int, n: int) -> list[int]:
    value = int(index)
    return [int((value >> i) & 1) for i in range(int(n))]


def cleanup_quantum_objects(*objects: Any) -> None:
    # Explicit GC keeps repeated statevector evaluations from retaining large
    # temporary arrays in long Deep Research jobs on unified-memory Macs.
    del objects
    gc.collect()
