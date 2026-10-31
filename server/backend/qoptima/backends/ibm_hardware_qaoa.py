from __future__ import annotations

"""IBM-style QAOA hardware executor boundary for QOptima.

This module is deliberately not routed from the UI in v0.4.  It exists so the
hardware-scale QPO formulation and the provider implementation share a concrete
contract.  Paid execution requires an explicit environment gate plus an explicit
method argument, preventing a planner/UI action from creating billable work.
"""

import os
from typing import Any
import numpy as np
from scipy.optimize import minimize

from .base import BackendResult
from .local_simulator import _ising_operator


class IBMHardwareQAOA:
    name = "ibm_hardware_qaoa_reference"

    def available(self) -> bool:
        try:
            import qiskit_ibm_runtime  # noqa: F401
            return True
        except Exception:
            return False

    def solve(
        self,
        qubo,
        *,
        shots: int = 1000,
        final_shots: int = 10000,
        qaoa_reps: int = 1,
        maxiter: int = 28,
        seed: int = 41,
        min_num_qubits: int | None = None,
        max_execution_time: int | None = None,
        explicit_paid_execution: bool = False,
    ) -> BackendResult:
        gate = os.getenv("QOPTIMA_IBM_HARDWARE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
        if not gate or not explicit_paid_execution:
            raise RuntimeError(
                "Real IBM QPU execution is locked. Set QOPTIMA_IBM_HARDWARE_ENABLED=true and pass "
                "explicit_paid_execution=True from a separately reviewed execution route."
            )
        if not self.available():
            raise RuntimeError("qiskit-ibm-runtime is not installed in the Oryntra Control runtime")

        from qiskit.circuit.library import QAOAAnsatz
        from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
        from qiskit_ibm_runtime import QiskitRuntimeService, EstimatorV2 as Estimator, SamplerV2 as Sampler

        cost_hamiltonian = _ising_operator(qubo)
        ansatz = QAOAAnsatz(cost_operator=cost_hamiltonian, reps=max(1, int(qaoa_reps)))
        ansatz.measure_all()

        service = QiskitRuntimeService()
        minimum = max(int(qubo.num_variables), int(min_num_qubits or qubo.num_variables))
        backend = service.least_busy(operational=True, simulator=False, min_num_qubits=minimum)
        pm = generate_preset_pass_manager(optimization_level=3, backend=backend)
        isa_circuit = pm.run(ansatz)
        isa_hamiltonian = cost_hamiltonian.apply_layout(isa_circuit.layout)

        estimator = Estimator(mode=backend)
        estimator.options.default_shots = int(shots)
        estimator.options.dynamical_decoupling.enable = True
        estimator.options.dynamical_decoupling.sequence_type = "XY4"
        estimator.options.twirling.enable_gates = True
        estimator.options.twirling.num_randomizations = "auto"
        if max_execution_time is not None:
            estimator.options.max_execution_time = int(max_execution_time)

        rng = np.random.default_rng(int(seed))
        initial = rng.uniform(-np.pi, np.pi, size=ansatz.num_parameters)
        objective_values: list[float] = []

        def objective(params):
            pub = (isa_circuit, isa_hamiltonian, np.asarray(params, dtype=float))
            result = estimator.run([pub]).result()[0]
            value = float(np.asarray(result.data.evs).reshape(-1)[0])
            objective_values.append(value)
            return value

        opt = minimize(objective, initial, method="COBYLA", options={"maxiter": max(1, int(maxiter))})
        optimized = isa_circuit.assign_parameters(opt.x)

        sampler = Sampler(mode=backend)
        sampler.options.default_shots = int(final_shots)
        sampler.options.dynamical_decoupling.enable = True
        sampler.options.dynamical_decoupling.sequence_type = "XY4"
        sampler.options.twirling.enable_gates = True
        sampler.options.twirling.num_randomizations = "auto"
        if max_execution_time is not None:
            sampler.options.max_execution_time = int(max_execution_time)
        job = sampler.run([(optimized,)], shots=int(final_shots))
        counts = job.result()[0].data.meas.get_counts()
        bitstring = max(counts.items(), key=lambda kv: kv[1])[0]
        bits = [int(ch) for ch in str(bitstring)[::-1]]
        bits = bits[: qubo.num_variables]

        usage = None
        try:
            usage = job.usage()
        except Exception:
            usage = None
        return BackendResult(
            bits=bits,
            energy=float(qubo.energy(bits)),
            backend=f"{self.name}:{backend.name}",
            execution_seconds=float(usage or 0.0),
            metadata={
                "quantum_circuit": True,
                "real_qpu": True,
                "backend": backend.name,
                "qaoa_reps": int(qaoa_reps),
                "optimizer": "COBYLA",
                "optimizer_evaluations": int(getattr(opt, "nfev", 0) or 0),
                "objective_history": objective_values,
                "sampler_shots": int(final_shots),
                "qpu_usage_seconds_final_sampler": usage,
            },
        )
