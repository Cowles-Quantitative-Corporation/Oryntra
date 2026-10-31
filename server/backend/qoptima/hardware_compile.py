from __future__ import annotations

"""Offline QAOA circuit compilation diagnostics.

No function in this module submits a job.  If a live IBM backend can be read via
Runtime credentials, Qiskit transpiles against that backend configuration.  When
that is unavailable, the result is explicitly a generic proxy rather than a
hardware claim.
"""

from typing import Any
import math
import numpy as np

from .provider_metadata import get_runtime_backend


def _proxy_pairs(n: int, density: float) -> list[tuple[int, int]]:
    possible = n * (n - 1) // 2
    target = min(possible, max(n - 1 if n > 1 else 0, int(round(possible * max(0.0, min(1.0, density))))))
    pairs: list[tuple[int, int]] = []
    # Deterministic ring/local interactions first, then longer range.
    for distance in range(1, n):
        for i in range(n - distance):
            pairs.append((i, i + distance))
            if len(pairs) >= target:
                return pairs
    return pairs


def compile_qaoa_proxy(
    logical_qubits: int,
    *,
    qaoa_reps: int = 1,
    interaction_density: float = 0.25,
    backend_name: str | None = None,
    optimization_level: int = 2,
) -> dict[str, Any]:
    n = int(logical_qubits)
    if n <= 0:
        return {"available": False, "reason": "no_qubits"}
    try:
        from qiskit import QuantumCircuit, transpile
    except Exception as exc:
        return {
            "available": False,
            "reason": "qiskit_unavailable",
            "error": str(exc),
            "warning": "No routed-depth claim is made without an actual Qiskit transpilation.",
        }

    reps = max(1, int(qaoa_reps))
    pairs = _proxy_pairs(n, interaction_density)
    qc = QuantumCircuit(n)
    qc.h(range(n))
    for p in range(reps):
        gamma = 0.41 + 0.07 * p
        beta = 0.29 + 0.05 * p
        for i, j in pairs:
            qc.rzz(2.0 * gamma, i, j)
        for i in range(n):
            qc.rz(gamma, i)
            qc.rx(2.0 * beta, i)

    backend = get_runtime_backend(backend_name) if backend_name else None
    source = "ibm_runtime_backend" if backend is not None else "unrouted_logical_circuit"
    try:
        compiled = transpile(qc, backend=backend, optimization_level=max(0, min(3, int(optimization_level)))) if backend is not None else qc
    except Exception as exc:
        return {
            "available": False,
            "reason": "transpile_failed",
            "error": str(exc),
            "backend_name": backend_name,
            "logical_depth": int(qc.depth()),
            "logical_two_qubit_gates": int(sum(v for k, v in qc.count_ops().items() if k in {"cx", "cz", "ecr", "rzz"})),
        }

    counts = {str(k): int(v) for k, v in compiled.count_ops().items()}
    twoq = sum(v for k, v in counts.items() if k in {"cx", "cz", "ecr", "rzz", "iswap", "swap"})
    swap_count = int(counts.get("swap", 0))
    out: dict[str, Any] = {
        "available": True,
        "compilation_source": source,
        "backend_name": backend_name if backend is not None else None,
        "logical_qubits": n,
        "qaoa_reps": reps,
        "interaction_density": float(interaction_density),
        "logical_interaction_terms": len(pairs),
        "logical_depth": int(qc.depth()),
        "routed_depth": int(compiled.depth()),
        "routed_two_qubit_gates": int(twoq),
        "swap_gates": swap_count,
        "operation_counts": counts,
        "optimization_level": int(optimization_level),
        "warning": None if backend is not None else "Circuit was not routed to a provider backend; depth/gate counts are logical only and must not be treated as hardware performance.",
    }
    if backend is not None:
        try:
            props = backend.properties()
            errs = []
            if props is not None:
                for gate in getattr(props, "gates", []) or []:
                    if str(getattr(gate, "gate", "")).lower() in {"ecr", "cx", "cz"}:
                        for param in getattr(gate, "parameters", []) or []:
                            if str(getattr(param, "name", "")) == "gate_error":
                                errs.append(float(param.value))
            if errs:
                out["two_qubit_error_assumption"] = {
                    "source": "backend_properties_snapshot",
                    "median": float(sorted(errs)[len(errs)//2]),
                    "min": float(min(errs)),
                    "max": float(max(errs)),
                    "count": len(errs),
                }
        except Exception as exc:
            out["backend_property_warning"] = str(exc)
    return out



def compile_financial_qubo_qaoa(
    qubo,
    *,
    qaoa_reps: int = 1,
    backend_name: str | None = None,
    optimization_level: int = 2,
) -> dict[str, Any]:
    """Compile a QAOA cost/mixer circuit from the *actual* financial QUBO.

    This is an offline diagnostic only.  It never submits a runtime job.  The
    circuit uses every non-zero linear/quadratic coefficient from the supplied
    QUBO so routed depth and two-qubit counts describe the real encoded
    interaction graph rather than a density proxy.
    """
    n = int(getattr(qubo, "num_variables", 0))
    if n <= 0:
        return {"available": False, "reason": "no_qubits", "compilation_source": "actual_financial_qubo"}
    try:
        from qiskit import QuantumCircuit, transpile
    except Exception as exc:
        return {
            "available": False,
            "reason": "qiskit_unavailable",
            "error": str(exc),
            "compilation_source": "actual_financial_qubo",
            "warning": "Actual financial-QUBO routing was not measured because Qiskit is unavailable.",
        }

    reps = max(1, int(qaoa_reps))
    linear = np.asarray(getattr(qubo, "linear"), dtype=float).reshape(-1)
    quadratic = dict(getattr(qubo, "quadratic", {}) or {})
    if len(linear) != n:
        return {"available": False, "reason": "qubo_linear_size_mismatch", "compilation_source": "actual_financial_qubo"}

    qc = QuantumCircuit(n)
    qc.h(range(n))
    for layer in range(reps):
        # Fixed non-zero angles are sufficient for routing/depth diagnostics; the
        # topology is invariant to the eventual optimizer's gamma/beta values.
        gamma = 0.41 + 0.07 * layer
        beta = 0.29 + 0.05 * layer
        for i, coeff in enumerate(linear):
            if abs(float(coeff)) > 1e-15:
                qc.rz(float(gamma * coeff), i)
        for (i, j), coeff in sorted(quadratic.items()):
            if abs(float(coeff)) > 1e-15:
                qc.rzz(float(gamma * coeff), int(i), int(j))
        for i in range(n):
            qc.rx(2.0 * beta, i)

    backend = get_runtime_backend(backend_name) if backend_name else None
    source = "actual_financial_qubo_ibm_backend" if backend is not None else "actual_financial_qubo_unrouted"
    try:
        compiled = transpile(
            qc,
            backend=backend,
            optimization_level=max(0, min(3, int(optimization_level))),
        ) if backend is not None else qc
    except Exception as exc:
        return {
            "available": False,
            "reason": "transpile_failed",
            "error": str(exc),
            "compilation_source": source,
            "backend_name": backend_name,
            "logical_depth": int(qc.depth()),
            "logical_two_qubit_gates": int(sum(v for k, v in qc.count_ops().items() if str(k) in {"cx", "cz", "ecr", "rzz"})),
            "financial_qubo_terms": {"linear": int(np.count_nonzero(np.abs(linear) > 1e-15)), "quadratic": int(len(quadratic))},
        }

    counts = {str(k): int(v) for k, v in compiled.count_ops().items()}
    twoq_names = {"cx", "cz", "ecr", "rzz", "iswap", "swap"}
    twoq = int(sum(v for k, v in counts.items() if k in twoq_names))
    possible_pairs = n * max(0, n - 1) // 2
    density = (len(quadratic) / possible_pairs) if possible_pairs else 0.0
    out: dict[str, Any] = {
        "available": True,
        "compilation_source": source,
        "actual_financial_qubo": True,
        "backend_name": backend_name if backend is not None else None,
        "logical_qubits": n,
        "qaoa_reps": reps,
        "financial_qubo_terms": {
            "linear": int(np.count_nonzero(np.abs(linear) > 1e-15)),
            "quadratic": int(len(quadratic)),
            "possible_pairs": int(possible_pairs),
            "interaction_density": float(density),
        },
        "logical_depth": int(qc.depth()),
        "routed_depth": int(compiled.depth()),
        "routed_two_qubit_gates": twoq,
        "swap_gates": int(counts.get("swap", 0)),
        "operation_counts": counts,
        "optimization_level": int(optimization_level),
        "warning": None if backend is not None else "Actual financial QUBO was compiled logically but not routed to a provider backend; hardware routing claims remain unavailable.",
    }
    if backend is not None:
        try:
            props = backend.properties()
            errs = []
            if props is not None:
                for gate in getattr(props, "gates", []) or []:
                    if str(getattr(gate, "gate", "")).lower() in {"ecr", "cx", "cz"}:
                        for param in getattr(gate, "parameters", []) or []:
                            if str(getattr(param, "name", "")) == "gate_error":
                                errs.append(float(param.value))
            if errs:
                out["two_qubit_error_assumption"] = {
                    "source": "backend_properties_snapshot",
                    "median": float(sorted(errs)[len(errs)//2]),
                    "min": float(min(errs)),
                    "max": float(max(errs)),
                    "count": len(errs),
                }
        except Exception as exc:
            out["backend_property_warning"] = str(exc)
    return out
