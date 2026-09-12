"""Calendar-year CAPM alpha with Newey-West uncertainty and explicit gates."""
from __future__ import annotations

import numpy as np
import pandas as pd


def regression_alpha(strategy: pd.Series, benchmark: pd.Series, risk_free: pd.Series) -> dict:
    frame = pd.concat([strategy.rename("s"), benchmark.rename("b"), risk_free.rename("rf")], axis=1)
    if len(frame) < 63 or not np.isfinite(frame.to_numpy()).all():
        return {"status": "insufficient_or_missing_observations"}
    x = frame.b.to_numpy() - frame.rf.to_numpy()
    y = frame.s.to_numpy() - frame.rf.to_numpy()
    if np.var(x) < 1e-12:
        return {"status": "degenerate_benchmark"}
    design = np.column_stack([np.ones(len(x)), x])
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    residual = y - design @ coefficients
    scores = design * residual[:, None]
    lag = min(len(x) - 1, int(np.floor(4 * (len(x) / 100) ** (2 / 9))))
    meat = scores.T @ scores
    for k in range(1, lag + 1):
        cross = scores[k:].T @ scores[:-k]
        meat += (1 - k / (lag + 1)) * (cross + cross.T)
    bread = np.linalg.inv(design.T @ design)
    covariance = bread @ meat @ bread * len(x) / (len(x) - 2)
    se = float(np.sqrt(max(0, covariance[0, 0])))
    alpha = float(coefficients[0]) * 252 * 100
    error = se * 252 * 100
    return {"status": "available", "observations": len(frame), "alpha_pct": alpha,
            "beta": float(coefficients[1]), "alpha_ci95_pct": [alpha - 1.96 * error, alpha + 1.96 * error],
            "alpha_t_hac": float(coefficients[0] / se) if se > 1e-14 else None,
            "hac_lags": lag, "strategy_return_pct": float(((1 + frame.s).prod() - 1) * 100),
            "benchmark_return_pct": float(((1 + frame.b).prod() - 1) * 100)}


def alpha_scorecard(strategy: pd.Series, benchmark: pd.Series | None,
                    risk_free: pd.Series | None, win_rate: float | None = None) -> dict:
    if benchmark is None or risk_free is None:
        return {"status": "not_measurable", "reason": "Supply both daily 50/50 SPY/QQQ returns and an explicit daily risk-free return series"}
    benchmark, risk_free = benchmark.reindex(strategy.index), risk_free.reindex(strategy.index)
    if not np.isfinite(np.column_stack([strategy, benchmark, risk_free])).all():
        return {"status": "not_measurable", "reason": "Missing benchmark, strategy or risk-free sessions; no silent imputation"}
    annual = []
    for year, sample in strategy.groupby(strategy.index.year):
        # Both calendar boundaries and session count matter; warmup years cannot pass.
        complete = len(sample) >= 240 and sample.index.min() <= pd.Timestamp(year, 1, 7) and sample.index.max() >= pd.Timestamp(year, 12, 24)
        row = regression_alpha(sample, benchmark.reindex(sample.index), risk_free.reindex(sample.index))
        annual.append({"year": int(year), "complete": bool(complete), **row})
    eligible = [r for r in annual if r["complete"] and r["status"] == "available"]
    alphas = [r["alpha_pct"] for r in eligible]
    mean = float(np.mean(alphas)) if alphas else None
    negative = sum(a < 0 for a in alphas)
    below_one = sum(a < 1 for a in alphas)
    middle = sum(1 <= a < 2.5 for a in alphas)
    criteria = {"exactly_ten_complete_years": len(eligible) == 10,
                "mean_alpha_at_least_5_pct": mean is not None and mean >= 5,
                "at_most_one_negative": negative <= 1,
                "at_most_two_total_below_1_pct": below_one <= 2,
                "remaining_years_at_least_2_5_pct": middle == 0,
                "win_rate_at_least_55_pct": win_rate is not None and win_rate >= 55}
    return {"status": "available", "benchmark": "daily 50/50 SPY/QQQ total-return proxy",
            "definition": "252 times daily excess-return regression intercept; percentage points per year",
            "uncertainty": "Newey-West HAC with Bartlett weights; confidence intervals do not adjust for model search",
            "annual": annual, "pooled": regression_alpha(strategy, benchmark, risk_free),
            "mean_annual_alpha_pct": mean, "complete_years": len(eligible),
            "negative_years": negative, "years_below_1_pct": below_one,
            "criteria": criteria, "passes": all(criteria.values()),
            "win_rate_pct": win_rate}
