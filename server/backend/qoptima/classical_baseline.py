from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from .problem import QOptimaPortfolioProblem


def _normalize_with_caps(weights: np.ndarray, target: float, cap: float) -> np.ndarray:
    w = np.clip(np.asarray(weights, dtype=float), 0.0, cap)
    for _ in range(20):
        total = float(w.sum())
        if abs(total - target) <= 1e-10:
            break
        if total <= 1e-12:
            room = np.maximum(cap - w, 0.0)
            if room.sum() <= 1e-12:
                break
            w += min(target, room.sum()) * room / room.sum()
            continue
        if total > target:
            w *= target / total
        else:
            room = np.maximum(cap - w, 0.0)
            if room.sum() <= 1e-12:
                break
            add = min(target - total, room.sum())
            w += add * room / room.sum()
        w = np.clip(w, 0.0, cap)
    return w


def solve_classical(problem: QOptimaPortfolioProblem) -> dict:
    current = _normalize_with_caps(problem.current_weights, problem.target_gross, problem.max_name_weight)
    bounds = [(0.0, float(problem.max_name_weight)) for _ in range(problem.n)]
    constraints = [{"type": "eq", "fun": lambda w: float(np.sum(w) - problem.target_gross)}]
    result = minimize(
        lambda w: -problem.objective(w),
        current,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 800, "ftol": 1e-12, "disp": False},
    )
    candidate = np.asarray(result.x if result.success else current, dtype=float)
    candidate = _normalize_with_caps(candidate, problem.target_gross, problem.max_name_weight)
    return {
        "weights": candidate,
        "success": bool(result.success),
        "message": str(result.message),
        "iterations": int(getattr(result, "nit", 0) or 0),
        "metrics": problem.metrics(candidate),
    }
