from __future__ import annotations

from typing import Any


def estimate_future_qpu_usage(
    *,
    variables: int,
    circuit_depth: int | None,
    shots: int,
    price_per_second: float,
    circuit_evaluations: int = 1,
    final_validation_circuits: int | None = None,
) -> dict[str, Any]:
    """Planning estimate using IBM's published no-mitigation baseline formula.

    IBM documents approximately: 2 + 0.00035 * num_executions seconds. Actual
    usage can differ with circuit duration, rep-delay, mitigation, transpilation,
    observable grouping and sub-job splitting.
    """
    variables = max(1, int(variables))
    depth = max(1, int(circuit_depth or max(8, variables * 5)))
    shots = max(1, int(shots))
    evals = max(1, int(circuit_evaluations))
    validation_circuits = max(1, int(final_validation_circuits or min(6, evals)))
    pps = max(0.0, float(price_per_second))

    full_executions = evals * shots
    full_seconds = 2.0 + 0.00035 * full_executions
    validation_executions = validation_circuits * shots
    validation_seconds = 2.0 + 0.00035 * validation_executions
    return {
        "estimated_qpu_seconds": float(full_seconds),
        "estimated_cost_usd": float(full_seconds * pps),
        "hardware_validation_seconds": float(validation_seconds),
        "hardware_validation_cost_usd": float(validation_seconds * pps),
        "price_per_second_assumption": float(pps),
        "estimate_type": "ibm_published_baseline_formula_planning_only",
        "shots": int(shots),
        "variables": int(variables),
        "circuit_depth": int(depth),
        "circuit_evaluations": int(evals),
        "final_validation_circuits": int(validation_circuits),
        "warning": "Local statevector wall time does not map 1:1 to IBM QPU seconds.",
    }
