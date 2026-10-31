from __future__ import annotations

import math
import time
from typing import Any, Callable

import numpy as np

from .base import BackendResult
from .gate_qaoa import (
    bits_from_index,
    build_energy_table,
    build_gate_qaoa_circuit,
    local_memory_plan,
    safe_statevector_qubit_cap,
    sample_indices,
    statevector_expectation,
    energies_for_indices,
    system_memory_gb,
)
from .local_simulator import qiskit_available


class _WorkloadComplete(RuntimeError):
    pass


def adaptive_statevector_qubit_cap(*, requested_cap: int = 24, memory_fraction: float = 0.48) -> dict[str, Any]:
    """Return a conservative local statevector plan for the current Mac.

    The old Deep path only compared raw statevector bytes with RAM.  This version
    accounts for Qiskit work buffers, probabilities and QOptima's energy table,
    and hard-caps local full-statevector work at 24 qubits.  Larger hardware
    formulations must run as a local shadow problem or on a real QPU.
    """
    ram_gb = system_memory_gb()
    safe = safe_statevector_qubit_cap(
        requested_cap=requested_cap,
        ram_gb=ram_gb,
        memory_fraction=memory_fraction,
        hard_cap=24,
    )
    plan = local_memory_plan(safe, ram_gb=ram_gb, memory_fraction=memory_fraction)
    return {
        "system_memory_gb": ram_gb,
        "requested_qubit_cap": max(8, min(int(requested_cap), 24)),
        "safe_qubit_cap": int(safe),
        "raw_statevector_gb_at_cap": float(plan["raw_statevector_bytes"] / (1024.0 ** 3)),
        "estimated_peak_gb_at_cap": float(plan["estimated_peak_bytes"] / (1024.0 ** 3)),
        "memory_budget_gb": None if plan["memory_budget_bytes"] is None else float(plan["memory_budget_bytes"] / (1024.0 ** 3)),
        "memory_fraction": float(memory_fraction),
        "superlu_free": True,
    }


class QiskitDeepQAOA:
    """Gate-native local QAOA research workload with no sparse linear algebra.

    The circuit is genuine QAOA: H preparation, diagonal QUBO cost layers built
    from RZ/RZZ gates, and RX mixer layers.  The classical angle loop uses a
    lightweight SPSA-style update so SciPy sparse/SuperLU can never enter the
    quantum hot path.

    There is no hardcoded wall-clock minimum.  QOptima benchmarks a real circuit
    evaluation on the current Mac and converts the selected effort target into a
    fixed number of useful QAOA evaluations.
    """

    name = "qiskit_gate_native_qaoa_shadow"

    def __init__(
        self,
        *,
        target_work_seconds: float = 80.0,
        max_reps: int = 3,
        maxiter_per_restart: int = 48,
        requested_qubit_cap: int = 24,
        minimum_evaluations: int = 2,
        maximum_evaluations: int = 5000,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.target_work_seconds = max(1.0, float(target_work_seconds))
        self.max_reps = max(1, min(int(max_reps), 5))
        self.maxiter_per_restart = max(4, min(int(maxiter_per_restart), 250))
        self.minimum_evaluations = max(2, int(minimum_evaluations))
        self.maximum_evaluations = max(self.minimum_evaluations, int(maximum_evaluations))
        self.memory_plan = adaptive_statevector_qubit_cap(requested_cap=requested_qubit_cap)
        self.progress_callback = progress_callback

    def available(self) -> bool:
        return qiskit_available()

    def _progress(self, **payload: Any) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(payload)
        except Exception:
            pass

    def _evaluate(self, qubo, params: np.ndarray, reps: int, energy_table: np.ndarray) -> tuple[float, int]:
        value, circuit, state, probabilities = statevector_expectation(qubo, params, reps, energy_table)
        depth = int(circuit.depth() or 0)
        # Release the largest temporary arrays immediately between evaluations.
        del circuit, state, probabilities
        return float(value), depth

    def _calibrate(self, qubo, energy_table: np.ndarray, n: int, rng: np.random.Generator) -> dict[str, Any]:
        # Benchmark one real p=1 circuit.  Running a p=3 calibration circuit at
        # 20-24 qubits can itself consume minutes, so estimate deeper layers from
        # the measured p=1 cost and the average requested depth instead.
        reps = 1
        params = rng.uniform(-math.pi, math.pi, size=2)
        t0 = time.perf_counter()
        value, depth = self._evaluate(qubo, params, reps, energy_table)
        measured_p1 = max(time.perf_counter() - t0, 1e-4)
        average_reps = float(np.mean(np.arange(1, self.max_reps + 1)))
        estimated_eval = measured_p1 * average_reps
        planned = int(round(self.target_work_seconds / max(estimated_eval, 1e-4)))
        planned = max(self.minimum_evaluations, min(self.maximum_evaluations, planned))
        if planned % 2:
            planned += 1
        return {
            "samples": [{
                "qaoa_reps": 1,
                "evaluation_seconds": float(measured_p1),
                "circuit_depth": int(depth),
                "expectation": float(value),
            }],
            "p1_measured_seconds": float(measured_p1),
            "average_reps_planned": float(average_reps),
            "estimated_seconds_per_circuit_evaluation": float(estimated_eval),
            "planned_circuit_evaluations": int(planned),
            "projected_local_compute_seconds": float(planned * estimated_eval),
            "target_local_effort_seconds": float(self.target_work_seconds),
            "qubits": int(n),
            "optimizer": "SPSA-style derivative-free angle search",
            "superlu_free": True,
            "calibration_note": "Measured p=1 once; deeper QAOA cost estimated by layer count to avoid an expensive calibration floor.",
        }

    @staticmethod
    def _wrap_params(params: np.ndarray, reps: int) -> np.ndarray:
        values = np.asarray(params, dtype=float).copy()
        # Gamma and beta are periodic; keeping them bounded avoids huge gate angles.
        values[:reps] = ((values[:reps] + math.pi) % (2.0 * math.pi)) - math.pi
        values[reps:] = ((values[reps:] + math.pi) % (2.0 * math.pi)) - math.pi
        return values

    def solve(self, qubo, *, shots: int = 8192, seed: int = 41) -> BackendResult:
        if not self.available():
            raise RuntimeError(
                "QOptima cannot import Qiskit in Oryntra Control's private runtime. "
                "Install core qiskit>=2.1 in that runtime before Deep Research."
            )

        n = int(qubo.num_variables)
        safe_cap = int(self.memory_plan["safe_qubit_cap"])
        if n > safe_cap:
            raise ValueError(
                f"Local QAOA shadow needs {n} qubits but this Mac's guarded cap is {safe_cap}. "
                "QOptima should reduce the local shadow before execution; the full hardware problem remains unchanged."
            )
        memory = local_memory_plan(n, ram_gb=self.memory_plan.get("system_memory_gb"))
        if not memory["safe"]:
            raise MemoryError(
                f"QOptima preflight blocked an unsafe {n}-qubit local statevector job. "
                "Use the automatically reduced local shadow or real QPU hardware."
            )

        started = time.perf_counter()
        rng = np.random.default_rng(int(seed))
        self._progress(
            stage="qpo_memory_preflight",
            message=(f"Local shadow preflight: {n} qubits · estimated peak "
                     f"{memory['estimated_peak_bytes']/(1024**3):.2f} GB · SuperLU-free gate path"),
            qubits=n,
            memory_plan=memory,
            fraction=0.0,
        )

        table_started = time.perf_counter()
        energy_table = build_energy_table(qubo)
        table_seconds = time.perf_counter() - table_started
        self._progress(
            stage="qpo_energy_table",
            message=f"Built diagonal QUBO energy table for {2**n:,} basis states in {table_seconds:.2f}s",
            qubits=n,
            basis_states=int(2 ** n),
            energy_table_seconds=float(table_seconds),
            fraction=0.0,
        )

        calibration = self._calibrate(qubo, energy_table, n, rng)
        planned_evaluations = int(calibration["planned_circuit_evaluations"])
        self._progress(
            stage="qpo_calibration",
            message=(f"Calibrated {n}-qubit gate-native QAOA at ~"
                     f"{calibration['estimated_seconds_per_circuit_evaluation']:.3f}s/eval; "
                     f"planning {planned_evaluations} useful circuit evaluations"),
            calibration=calibration,
            evaluations=0,
            planned_evaluations=planned_evaluations,
            fraction=0.0,
        )

        best_params: np.ndarray | None = None
        best_reps = 1
        best_expectation = float("inf")
        best_depth = 0
        evaluations = 0
        restarts = 0
        history: list[dict[str, Any]] = []
        last_progress = 0.0
        reps_cycle = list(range(1, self.max_reps + 1))

        # SPSA-style settings.  These are deliberately conservative for noisy-QPU
        # compatibility and avoid any scipy optimizer / sparse linear solver.
        a0 = 0.18
        c0 = 0.12
        alpha = 0.602
        gamma_exp = 0.101

        while evaluations + 2 <= planned_evaluations:
            reps = reps_cycle[restarts % len(reps_cycle)]
            params = rng.uniform(-math.pi, math.pi, size=2 * reps)
            local_best = float("inf")
            local_best_params = params.copy()
            restart_evals = 0

            for k in range(self.maxiter_per_restart):
                if evaluations + 2 > planned_evaluations:
                    break
                ak = a0 / ((k + 1) ** alpha)
                ck = c0 / ((k + 1) ** gamma_exp)
                delta = rng.choice(np.array([-1.0, 1.0]), size=params.size)
                plus = self._wrap_params(params + ck * delta, reps)
                minus = self._wrap_params(params - ck * delta, reps)

                f_plus, depth_plus = self._evaluate(qubo, plus, reps, energy_table)
                f_minus, depth_minus = self._evaluate(qubo, minus, reps, energy_table)
                evaluations += 2
                restart_evals += 2

                if f_plus < local_best:
                    local_best, local_best_params = f_plus, plus.copy()
                if f_minus < local_best:
                    local_best, local_best_params = f_minus, minus.copy()
                if f_plus < best_expectation:
                    best_expectation, best_params, best_reps, best_depth = f_plus, plus.copy(), reps, depth_plus
                if f_minus < best_expectation:
                    best_expectation, best_params, best_reps, best_depth = f_minus, minus.copy(), reps, depth_minus

                grad_scale = (f_plus - f_minus) / max(2.0 * ck, 1e-9)
                grad = grad_scale * delta
                params = self._wrap_params(params - ak * grad, reps)

                now = time.perf_counter()
                if now - last_progress >= 2.5 or evaluations >= planned_evaluations:
                    last_progress = now
                    self._progress(
                        stage="qpo_deep_qiskit",
                        message=(f"Gate-native QAOA: {evaluations}/{planned_evaluations} evaluations · "
                                 f"p={reps} · restart {restarts+1}"),
                        elapsed_seconds=float(now - started),
                        evaluations=int(evaluations),
                        planned_evaluations=int(planned_evaluations),
                        fraction=float(evaluations / max(1, planned_evaluations)),
                        restarts=int(restarts + 1),
                        qaoa_reps=int(reps),
                        best_expectation=float(best_expectation),
                        superlu_free=True,
                    )

            history.append({
                "restart": int(restarts + 1),
                "qaoa_reps": int(reps),
                "evaluations": int(restart_evals),
                "best_expectation": float(local_best),
            })
            restarts += 1
            if restart_evals == 0:
                break

        if best_params is None:
            best_reps = 1
            best_params = rng.uniform(-math.pi, math.pi, size=2)
            best_expectation, best_depth = self._evaluate(qubo, best_params, best_reps, energy_table)
            evaluations += 1

        # Final high-shot measurement of the best circuit.  We select the lowest
        # QUBO-energy bitstring actually observed, which matches how a QPU result
        # would be decoded instead of reading an impossible exact oracle answer.
        final_expectation, final_circuit, final_state, probabilities = statevector_expectation(
            qubo, best_params, best_reps, energy_table
        )
        sampled = sample_indices(probabilities, shots=max(256, int(shots)), rng=rng)
        sampled_energies = energies_for_indices(qubo, sampled)
        best_sample_pos = int(np.argmin(sampled_energies))
        best_index = int(sampled[best_sample_pos])
        best_bits = bits_from_index(best_index, n)
        best_energy = float(sampled_energies[best_sample_pos])
        observed_best_count = int(np.sum(sampled == np.uint64(best_index)))
        final_probability = float(probabilities[best_index])
        final_depth = int(final_circuit.depth() or best_depth)
        elapsed = time.perf_counter() - started

        return BackendResult(
            bits=best_bits,
            energy=best_energy,
            backend=self.name,
            execution_seconds=float(elapsed),
            metadata={
                "quantum_circuit": True,
                "qaoa_gate_native": True,
                "qaoa_reps": int(best_reps),
                "optimizer": "SPSA-style derivative-free",
                "optimizer_success": True,
                "circuit_evaluations": int(evaluations),
                "planned_circuit_evaluations": int(planned_evaluations),
                "restarts": int(restarts),
                "best_expectation": float(best_expectation),
                "final_expectation": float(final_expectation),
                "most_useful_observed_probability": float(final_probability),
                "best_observed_count": int(observed_best_count),
                "final_shots": int(max(256, int(shots))),
                "circuit_depth": int(final_depth),
                "mac_wall_seconds": float(elapsed),
                "calibration": calibration,
                "memory_plan": memory,
                "energy_table_seconds": float(table_seconds),
                "history": history[-20:],
                "superlu_free": True,
                "sparse_matrix_materialization": False,
                "scipy_optimizer_used": False,
                "local_shadow_only": True,
            },
        )
