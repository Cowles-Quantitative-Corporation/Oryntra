from __future__ import annotations

"""Optional QRS-directed QAOA research path.

For a positive QRS threshold t, the squared feasibility relation is

    R(w)^2 - t^2 * w' Sigma_alpha w >= 0,

with R(w) the same-horizon excess return.  Squaring is unsafe when R<=0, so
QOptima rejects every decoded candidate with non-positive R.  QAOA is
approximate: failure to observe a feasible sample is never reported as proof of
infeasibility.
"""

from dataclasses import dataclass
from typing import Any
import numpy as np

from .encoding.allocation_qubo import AllocationQUBO
from .problem import QOptimaPortfolioProblem
from .matched_solvers import compare_matched_solvers


@dataclass(frozen=True)
class QRSThresholdQUBO:
    threshold: float
    qubo: AllocationQUBO
    excess_return_vector: np.ndarray
    covariance: np.ndarray


def build_qrs_threshold_qubo(
    problem: QOptimaPortfolioProblem,
    *,
    threshold: float,
    bits_per_asset: int = 2,
    gross_penalty: float = 80.0,
    turnover_penalty: float | None = None,
    positive_return_bias: float = 0.05,
) -> QRSThresholdQUBO:
    bits = max(1, min(5, int(bits_per_asset)))
    n = problem.n
    m = n * bits
    max_code = float(2**bits - 1)
    step = float(problem.max_name_weight) / max_code
    S = np.zeros((n, m), dtype=float)
    for i in range(n):
        for b in range(bits):
            S[i, i * bits + b] = step * float(2**b)

    excess = np.asarray(problem.expected_returns, dtype=float) - float(problem.risk_free_rate)
    cov = np.asarray(problem.optimization_covariance, dtype=float)
    current = np.asarray(problem.current_weights, dtype=float)
    tau = float(problem.turnover_penalty if turnover_penalty is None else turnover_penalty)
    rho = float(gross_penalty)
    t = max(0.0, float(threshold))

    # Minimize: t^2 V - R^2 - eps*R + turnover + gross penalty.
    # The -eps*R term only breaks the sign symmetry; decoded R must still be >0.
    rbits = S.T @ excess
    M = (t * t) * (S.T @ cov @ S) - np.outer(rbits, rbits) + tau * (S.T @ S)
    gross_vec = S.sum(axis=0)
    M += rho * np.outer(gross_vec, gross_vec)
    linear = (
        -float(positive_return_bias) * rbits
        - 2.0 * tau * (S.T @ current)
        - 2.0 * rho * float(problem.target_gross) * gross_vec
        + np.diag(M)
    )
    quadratic: dict[tuple[int, int], float] = {}
    for i in range(m):
        for j in range(i + 1, m):
            q = float(2.0 * M[i, j])
            if abs(q) > 1e-15:
                quadratic[(i, j)] = q
    constant = float(
        tau * (current @ current)
        + rho * float(problem.target_gross) ** 2
        - float(problem.objective_constant_offset)
    )
    qubo = AllocationQUBO(
        symbols=problem.symbols,
        bits_per_asset=bits,
        weight_matrix=S,
        linear=np.asarray(linear, dtype=float),
        quadratic=quadratic,
        constant=constant,
        target_gross=float(problem.target_gross),
        gross_penalty=rho,
        risk_aversion=t * t,
        turnover_penalty=tau,
        max_name_weight=float(problem.max_name_weight),
    )
    return QRSThresholdQUBO(t, qubo, excess, cov)


def score_qrs_candidate(problem: QOptimaPortfolioProblem, weights: np.ndarray, *, threshold: float) -> dict[str, Any]:
    w = np.asarray(weights, dtype=float)
    excess = float((np.asarray(problem.expected_returns, dtype=float) - float(problem.risk_free_rate)) @ w)
    variance = max(0.0, float(w @ np.asarray(problem.optimization_covariance, dtype=float) @ w))
    vol = float(np.sqrt(variance))
    qrs = excess / vol if vol > 1e-12 else None
    return {
        "excess_return": excess,
        "positive_excess_return": bool(excess > 0.0),
        "variance": variance,
        "qrs": qrs,
        "threshold": float(threshold),
        "meets_threshold": bool(excess > 0.0 and qrs is not None and qrs >= float(threshold)),
    }


def qrs_threshold_search(
    problem: QOptimaPortfolioProblem,
    solver,
    *,
    bits_per_asset: int = 2,
    low: float = 0.0,
    high: float = 3.0,
    iterations: int = 6,
    shots: int = 2048,
    seed: int = 41,
) -> dict[str, Any]:
    """Approximate outer threshold search; non-observation is never proof infeasible."""
    archive: list[dict[str, Any]] = []
    lo, hi = max(0.0, float(low)), max(float(low), float(high))
    best = None
    for k in range(max(1, int(iterations))):
        t = 0.5 * (lo + hi)
        built = build_qrs_threshold_qubo(problem, threshold=t, bits_per_asset=bits_per_asset)
        result = solver.solve(built.qubo, shots=shots, seed=seed + k)
        weights = built.qubo.weights(result.bits)
        score = score_qrs_candidate(problem, weights, threshold=t)
        matched = compare_matched_solvers(
            built.qubo,
            qaoa_result={"energy": float(result.energy), "backend": result.backend, "wall_seconds": float(result.execution_seconds)},
            seed=seed + k, exact_max_variables=18, milp_max_variables=36,
        )
        row = {
            "threshold": t,
            "bits": list(result.bits),
            "energy": float(result.energy),
            "weights": weights.tolist(),
            "score": score,
            "backend": result.backend,
            "matched_classical": matched,
        }
        archive.append(row)
        if score["meets_threshold"]:
            best = row if best is None or float(score["qrs"]) > float(best["score"]["qrs"]) else best
            lo = t
        else:
            # QAOA is approximate. Moving the search bound is a heuristic only.
            hi = t
    return {
        "best_observed": best,
        "archive": archive,
        "note": "Approximate QAOA threshold search. An unsuccessful threshold is NOT proof of infeasibility.",
        "positive_return_requirement": "decoded candidates with R(w)<=0 are never threshold-feasible",
    }
