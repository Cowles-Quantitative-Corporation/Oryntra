from __future__ import annotations

from typing import Any
import numpy as np


def paired_block_bootstrap_gate(
    v1a_net_returns,
    canonical_net_returns,
    *,
    block_length: int = 21,
    replications: int = 2000,
    alpha: float = 0.10,
    effect_size: float = 0.0,
    seed: int = 41,
) -> dict[str, Any]:
    v = np.asarray(v1a_net_returns, dtype=float).reshape(-1)
    c = np.asarray(canonical_net_returns, dtype=float).reshape(-1)
    if len(v) != len(c):
        raise ValueError("paired Gate B series must have identical length")
    d = v - c
    n = len(d)
    b = max(1, int(block_length))
    if n < max(3 * b, 30):
        return {"status": "INCONCLUSIVE_INSUFFICIENT_OOS", "sessions": n}
    rng = np.random.default_rng(int(seed))
    reps = max(200, int(replications))
    means = np.empty(reps, dtype=float)
    starts = max(1, n - b + 1)
    for r in range(reps):
        idx: list[int] = []
        while len(idx) < n:
            s = int(rng.integers(0, starts))
            idx.extend(range(s, min(n, s + b)))
        means[r] = float(np.mean(d[np.asarray(idx[:n], dtype=int)]))
    gap = float(np.mean(d))
    se = float(np.std(means, ddof=1))
    lower = float(np.quantile(means, float(alpha)))
    configured = bool(float(effect_size) > 0.0)
    effect_pass = bool(configured and gap >= float(effect_size))
    uncertainty_pass = bool(lower > 0.0)
    if not configured:
        status = "DEVELOPMENT_ONLY_EFFECT_SIZE_NOT_FROZEN"
    else:
        status = "PASS" if effect_pass and uncertainty_pass else "PROMISING_BUT_INCONCLUSIVE" if effect_pass else "FAIL"
    return {
        "status": status,
        "promotion_hurdle_configured": configured,
        "mean_net_return_difference": gap,
        "effect_size_threshold": float(effect_size),
        "bootstrap_standard_error": se,
        "one_sided_lower_bound": lower,
        "alpha": float(alpha),
        "block_length": b,
        "replications": reps,
        "seed": int(seed),
        "effect_size_pass": effect_pass,
        "uncertainty_pass": uncertainty_pass,
        "procedure_version": "GATE_B_BOOTSTRAP_V1",
    }
