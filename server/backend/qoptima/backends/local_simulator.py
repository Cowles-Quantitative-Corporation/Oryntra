from __future__ import annotations

import itertools
import math
import time
import numpy as np

from .base import BackendResult
from ..encoding.qubo import MoveQUBO


class ExactQUBOSimulator:
    name = "local_exact_qubo"

    def __init__(self, max_variables: int = 22):
        self.max_variables = int(max_variables)

    def available(self) -> bool:
        return True

    def solve(self, qubo: MoveQUBO, *, shots: int = 2048, seed: int = 41) -> BackendResult:
        if qubo.num_variables > self.max_variables:
            raise ValueError(f"Exact local simulator limit is {self.max_variables} binary variables")
        started = time.perf_counter()
        best_bits: tuple[int, ...] | None = None
        best_energy = float("inf")
        for bits in itertools.product((0, 1), repeat=qubo.num_variables):
            energy = qubo.energy(bits)
            if energy < best_energy:
                best_energy = energy
                best_bits = bits
        elapsed = time.perf_counter() - started
        return BackendResult(
            bits=list(best_bits or (0,) * qubo.num_variables),
            energy=float(best_energy),
            backend=self.name,
            execution_seconds=float(elapsed),
            metadata={"states_examined": int(2 ** qubo.num_variables), "quantum_circuit": False},
        )


def qiskit_status() -> dict:
    try:
        import qiskit
        from qiskit import QuantumCircuit  # noqa: F401
        from qiskit.quantum_info import Statevector  # noqa: F401
        return {"available": True, "version": getattr(qiskit, "__version__", None), "error": None}
    except Exception as exc:
        return {"available": False, "version": None, "error": f"{type(exc).__name__}: {exc}"}


def qiskit_available() -> bool:
    return bool(qiskit_status()["available"])


def _ising_operator(qubo: MoveQUBO):
    from qiskit.quantum_info import SparsePauliOp

    n = qubo.num_variables
    z = np.zeros(n, dtype=float)
    zz: dict[tuple[int, int], float] = {}
    constant = float(qubo.constant)
    for i, value in enumerate(qubo.linear):
        constant += float(value) / 2.0
        z[i] += -float(value) / 2.0
    for (i, j), value in qubo.quadratic.items():
        q = float(value)
        constant += q / 4.0
        z[i] += -q / 4.0
        z[j] += -q / 4.0
        zz[(i, j)] = zz.get((i, j), 0.0) + q / 4.0

    terms: list[tuple[str, complex]] = []
    if abs(constant) > 1e-15:
        terms.append(("I" * n, complex(constant)))
    for i, coeff in enumerate(z):
        if abs(coeff) <= 1e-15:
            continue
        chars = ["I"] * n
        chars[n - 1 - i] = "Z"
        terms.append(("".join(chars), complex(coeff)))
    for (i, j), coeff in zz.items():
        if abs(coeff) <= 1e-15:
            continue
        chars = ["I"] * n
        chars[n - 1 - i] = "Z"
        chars[n - 1 - j] = "Z"
        terms.append(("".join(chars), complex(coeff)))
    return SparsePauliOp.from_list(terms or [("I" * n, 0.0)])


class QiskitStatevectorQAOA:
    """Small gate-native QAOA validation backend.

    This intentionally avoids SparsePauliOp expectation evaluation and scipy
    optimizers.  It is a quick correctness backend; Deep Research uses
    QiskitDeepQAOA with a calibrated workload.
    """

    name = "qiskit_gate_native_qaoa_validation"

    def available(self) -> bool:
        return qiskit_available()

    def solve(self, qubo: MoveQUBO, *, shots: int = 2048, seed: int = 41) -> BackendResult:
        if not self.available():
            raise RuntimeError("Qiskit is not installed. Use local_exact_qubo or install requirements-qoptima.txt")
        if qubo.num_variables > 20:
            raise ValueError("Quick Qiskit validation is capped at 20 variables; use Deep Research shadow mode for larger local tests")

        from .gate_qaoa import (
            build_energy_table,
            statevector_expectation,
            sample_indices,
            energies_for_indices,
            bits_from_index,
        )

        started = time.perf_counter()
        rng = np.random.default_rng(int(seed))
        energy_table = build_energy_table(qubo)
        best_value = float("inf")
        best_params = None
        evaluations = 0

        # Lightweight random + local perturbation search.  No scipy optimizer,
        # no sparse matrix factorization.
        center = rng.uniform(-math.pi, math.pi, size=2)
        for k in range(24):
            scale = max(0.08, 1.2 * (0.90 ** k))
            params = center + rng.normal(0.0, scale, size=2)
            value, circuit, state, probs = statevector_expectation(qubo, params, 1, energy_table)
            evaluations += 1
            if value < best_value:
                best_value = float(value)
                best_params = np.asarray(params, dtype=float).copy()
                center = best_params.copy()
            del circuit, state, probs

        if best_params is None:
            best_params = center
        expectation, circuit, state, probs = statevector_expectation(qubo, best_params, 1, energy_table)
        sampled = sample_indices(probs, shots=max(256, int(shots)), rng=rng)
        sample_energy = energies_for_indices(qubo, sampled)
        pos = int(np.argmin(sample_energy))
        idx = int(sampled[pos])
        bits = bits_from_index(idx, qubo.num_variables)
        elapsed = time.perf_counter() - started
        return BackendResult(
            bits=bits,
            energy=float(sample_energy[pos]),
            backend=self.name,
            execution_seconds=float(elapsed),
            metadata={
                "quantum_circuit": True,
                "qaoa_gate_native": True,
                "qaoa_reps": 1,
                "optimizer": "random-local-search",
                "optimizer_iterations": int(evaluations),
                "final_expectation": float(expectation),
                "most_useful_observed_probability": float(probs[idx]),
                "circuit_depth": int(circuit.depth() or 0),
                "superlu_free": True,
                "sparse_matrix_materialization": False,
                "scipy_optimizer_used": False,
            },
        )


def select_local_backend(name: str = "auto"):
    clean = str(name or "auto").strip().lower()
    if clean in {"auto", "qiskit", "qiskit_statevector"} and qiskit_available():
        return QiskitStatevectorQAOA()
    if clean in {"qiskit", "qiskit_statevector"}:
        raise RuntimeError("Qiskit backend requested but Qiskit is not installed")
    if clean not in {"auto", "exact", "local_exact", "local_exact_qubo"}:
        raise ValueError("Choose auto, qiskit, or exact as the QOptima simulator backend")
    return ExactQUBOSimulator()
