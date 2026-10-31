from __future__ import annotations

"""QAOA for an arbitrary *diagonal* discrete cost Hamiltonian.

Unlike the ordinary QOptima QUBO path, this backend does not assume the cost is
quadratic.  The caller supplies the finite phase-energy table over the encoded
bit strings.  For legacy Full-QRS this is literal -QRS; V1A-QRS may supply its
frozen monotone low-energy linear phase normalization.  QAOA then applies

    U_C(gamma)|x> = exp(-i gamma C(x)) |x>

followed by the standard transverse-field RX mixer.

This is mathematically exact for the finite encoded objective.  It is also
exponential in the number of binary variables because an arbitrary diagonal
objective contains 2**n basis-state costs.  The backend is therefore intended
for the small QScan-selected Full-QRS research problem, not as a claim of a
scalable quantum advantage.
"""

from dataclasses import dataclass
import math
import time
from typing import Any

import numpy as np

from .base import BackendResult


@dataclass(frozen=True)
class ExactDiagonalPlan:
    qubits: int
    basis_states: int
    energy_table_bytes: int
    statevector_bytes: int
    approximate_work_bytes: int


def diagonal_plan(qubits: int) -> ExactDiagonalPlan:
    n = max(0, int(qubits))
    states = 2 ** n
    # Two complex work vectors + probability + energy table + phase vector.
    work = states * (16 * 3 + 8 * 2)
    return ExactDiagonalPlan(
        qubits=n,
        basis_states=states,
        energy_table_bytes=states * 8,
        statevector_bytes=states * 16,
        approximate_work_bytes=work,
    )


def _energy_table(objective: Any) -> np.ndarray:
    if hasattr(objective, "energy_table") and callable(objective.energy_table):
        table = np.asarray(objective.energy_table(), dtype=np.float64).reshape(-1)
    elif hasattr(objective, "energy_table_data"):
        table = np.asarray(objective.energy_table_data, dtype=np.float64).reshape(-1)
    else:
        n = int(objective.num_variables)
        table = np.empty(2 ** n, dtype=np.float64)
        for idx in range(2 ** n):
            bits = [(idx >> i) & 1 for i in range(n)]
            table[idx] = float(objective.energy(bits))
    n = int(objective.num_variables)
    if len(table) != 2 ** n:
        raise ValueError(f"diagonal objective expected {2**n} energies, got {len(table)}")
    if not np.isfinite(table).all():
        raise ValueError("diagonal QAOA energy table must be finite; encode hard infeasibility as a dominated finite phase energy")
    return table


def _apply_rx_mixer(state: np.ndarray, beta: float, n: int) -> None:
    """In-place product RX(2 beta) mixer with Qiskit-compatible little endian bits."""
    c = math.cos(float(beta))
    s = -1j * math.sin(float(beta))
    for q in range(n):
        step = 1 << q
        block = step << 1
        view = state.reshape(-1, block)
        a = view[:, :step].copy()
        b = view[:, step:].copy()
        view[:, :step] = c * a + s * b
        view[:, step:] = s * a + c * b


def exact_diagonal_qaoa_state(
    energy_table: np.ndarray,
    params: np.ndarray,
    reps: int,
) -> np.ndarray:
    table = np.asarray(energy_table, dtype=np.float64).reshape(-1)
    states = int(table.size)
    n = int(round(math.log2(states))) if states > 0 else 0
    if 2 ** n != states:
        raise ValueError("energy table length must be a power of two")
    p = max(1, int(reps))
    values = np.asarray(params, dtype=float).reshape(-1)
    if values.size != 2 * p:
        raise ValueError(f"QAOA p={p} requires {2*p} parameters, got {values.size}")
    gammas = values[:p]
    betas = values[p:]
    state = np.full(states, 1.0 / math.sqrt(states), dtype=np.complex128)
    # Remove a constant offset from the phase.  It is only a global phase and
    # avoiding it improves numerical behavior without changing QAOA.
    centered = table - float(table.min())
    for layer in range(p):
        state *= np.exp(-1j * float(gammas[layer]) * centered)
        _apply_rx_mixer(state, float(betas[layer]), n)
    return state


def diagonal_expectation(energy_table: np.ndarray, params: np.ndarray, reps: int) -> tuple[float, np.ndarray]:
    state = exact_diagonal_qaoa_state(energy_table, params, reps)
    probabilities = np.square(state.real) + np.square(state.imag)
    return float(np.dot(probabilities, np.asarray(energy_table, dtype=float))), probabilities


class ExactDiagonalQAOA:
    """Pure-numpy QAOA over an arbitrary exact diagonal objective.

    This backend exists specifically so Full Canonical QRS can be the literal
    phase-separator cost on small encoded portfolio problems instead of being
    replaced by an active-set QUBO surrogate.
    """

    name = "exact_diagonal_full_qrs_qaoa"

    def __init__(
        self,
        *,
        max_variables: int = 16,
        reps: int = 2,
        optimizer_evaluations: int = 96,
    ):
        self.max_variables = max(2, int(max_variables))
        self.reps = max(1, min(5, int(reps)))
        self.optimizer_evaluations = max(12, int(optimizer_evaluations))

    def available(self) -> bool:
        return True

    @staticmethod
    def _wrap(params: np.ndarray) -> np.ndarray:
        return ((np.asarray(params, dtype=float) + math.pi) % (2.0 * math.pi)) - math.pi

    @classmethod
    def _project_params(cls, params: np.ndarray, reps: int, gamma_bound: float) -> np.ndarray:
        values = np.asarray(params, dtype=float).copy()
        p = int(reps)
        values[:p] = np.clip(values[:p], -float(gamma_bound), float(gamma_bound))
        values[p:] = cls._wrap(values[p:])
        return values

    def solve(self, objective: Any, *, shots: int = 4096, seed: int = 41) -> BackendResult:
        n = int(objective.num_variables)
        if n > self.max_variables:
            raise ValueError(
                f"Exact Full-QRS diagonal QAOA requires a 2^n objective table and is capped at {self.max_variables} variables; got {n}. "
                "Reduce QScan assets/bits or raise the cap knowingly."
            )
        started = time.perf_counter()
        table = _energy_table(objective)
        plan = diagonal_plan(n)
        rng = np.random.default_rng(int(seed))
        p = self.reps
        objective_meta = {}
        try:
            objective_meta = dict(objective.metadata() or {})
        except Exception:
            objective_meta = {}
        phase_meta = dict(objective_meta.get("phase_normalization") or {})
        if phase_meta.get("policy") == "V1A_QAOA_LOW_ENERGY_LINEAR_SCALE_V1_6":
            cap = max(1.0, float(phase_meta.get("high_energy_cap") or 1.0))
            gamma_bound = math.pi / cap
        else:
            gamma_bound = math.pi

        # SPSA-like derivative-free optimization against the *exact* QRS-derived
        # diagonal Hamiltonian.  QAOA remains approximate; only the cost operator
        # is exact on the finite binary domain.
        params = np.concatenate([
            rng.uniform(-gamma_bound, gamma_bound, size=p),
            rng.uniform(-math.pi, math.pi, size=p),
        ])
        best_params = params.copy()
        best_expectation, _ = diagonal_expectation(table, best_params, p)
        evaluations = 1
        a0, c0 = 0.22, 0.16
        k = 0
        while evaluations + 2 <= self.optimizer_evaluations:
            ak = a0 / ((k + 1) ** 0.602)
            ck = c0 / ((k + 1) ** 0.101)
            delta = rng.choice(np.array([-1.0, 1.0]), size=params.size)
            plus = self._project_params(params + ck * delta, p, gamma_bound)
            minus = self._project_params(params - ck * delta, p, gamma_bound)
            f_plus, _ = diagonal_expectation(table, plus, p)
            f_minus, _ = diagonal_expectation(table, minus, p)
            evaluations += 2
            if f_plus < best_expectation:
                best_expectation, best_params = float(f_plus), plus.copy()
            if f_minus < best_expectation:
                best_expectation, best_params = float(f_minus), minus.copy()
            grad = ((f_plus - f_minus) / max(2.0 * ck, 1e-12)) * delta
            params = self._project_params(params - ak * grad, p, gamma_bound)
            k += 1

        final_expectation, probabilities = diagonal_expectation(table, best_params, p)
        sample_count = max(256, int(shots))
        sampled = rng.choice(len(table), size=sample_count, replace=True, p=probabilities / probabilities.sum())
        sampled_energies = table[sampled]
        pos = int(np.argmin(sampled_energies))
        best_index = int(sampled[pos])
        best_bits = [int((best_index >> i) & 1) for i in range(n)]
        elapsed = time.perf_counter() - started
        exact_global_energy = float(table.min())
        return BackendResult(
            bits=best_bits,
            energy=float(table[best_index]),
            backend=self.name,
            execution_seconds=float(elapsed),
            metadata={
                "quantum_circuit": True,
                "circuit_representation": "implicit_exact_statevector_QAOA_unitary",
                "statevector_simulation": True,
                "real_qpu": False,
                "qaoa": True,
                "phase_separator": "exact_arbitrary_diagonal_cost",
                "full_qrs_cost_hamiltonian_exact_on_encoded_domain": True,
                "qaoa_solution_is_approximate": True,
                "qaoa_reps": int(p),
                "optimizer": "SPSA-style derivative-free",
                "optimizer_evaluations": int(evaluations),
                "gamma_angle_bound": float(gamma_bound),
                "phase_wrap_guard": "gamma_bound * energy_cap <= pi" if gamma_bound < math.pi else "legacy_angle_domain",
                "final_expectation": float(final_expectation),
                "observed_state_probability": float(probabilities[best_index]),
                "shots": int(sample_count),
                "basis_states": int(plan.basis_states),
                "energy_table_bytes": int(plan.energy_table_bytes),
                "statevector_bytes": int(plan.statevector_bytes),
                "exact_global_energy_known_from_oracle_table_for_validation": exact_global_energy,
                "observed_global_optimum": bool(abs(float(table[best_index]) - exact_global_energy) <= 1e-12),
                "scalability_warning": (
                    "Arbitrary Full-QRS diagonal synthesis is exponential in binary variables. "
                    "This mode makes the literal Full Canonical QRS the QAOA cost for small research problems; "
                    "it is not a scalable quantum-advantage claim."
                ),
            },
        )
