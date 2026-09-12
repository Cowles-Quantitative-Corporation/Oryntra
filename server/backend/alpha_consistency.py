"""Owner's September 2026 alpha gates; units are percentage points, not returns."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .alpha_evaluation import regression_alpha

POLICY_ID = "beta-alpha-consistency-2026-09-v1"
BETA_SESSIONS = 126


def policy_contract() -> dict:
    return {"id": POLICY_ID, "target_annual_alpha_pct": 5.0,
            "two_year_months": 24, "maximum_negative_months": 2,
            "ten_year_years": 10, "maximum_negative_years": 1,
            "weak_year_threshold_alpha_pct": 2.0, "weak_year_weight": 2,
            "beta_calibration_sessions": BETA_SESSIONS,
            "monthly_definition": "100 * sum(strategy-rf - prior_month_beta*(benchmark-rf)); unannualized contribution",
            "annual_definition": "25200 * OLS excess-return intercept with Newey-West HAC uncertainty",
            "win_rate": "diagnostic only; superseded as an acceptance gate by owner's pasted specification"}


def _valid_index(series: pd.Series) -> bool:
    return (isinstance(series.index, pd.DatetimeIndex) and not series.empty
            and series.index.tz is None and series.index.is_monotonic_increasing
            and not series.index.has_duplicates and series.index.equals(series.index.normalize()))


def consistency_scorecard(strategy: pd.Series, benchmark: pd.Series | None,
                          risk_free: pd.Series | None, *, prior_strategy: pd.Series | None = None) -> dict:
    """Score exactly two or ten years without changing estimators after results.

    Monthly beta is fitted with an intercept on the last 126 complete prior
    strategy sessions, frozen for the entire month. Calibration returns must
    come from the same frozen strategy and end before the evaluation starts.
    No unavailable month is counted as nonnegative. Calendar coverage is
    checked against the supplied benchmark session calendar as well as period
    boundaries; provider calendar accuracy remains an input-data requirement.
    """
    contract = policy_contract()
    unavailable = lambda why: {"status": "not_measurable", "reason": why, "policy": contract, "passes": False}
    if benchmark is None or risk_free is None:
        return unavailable("Explicit daily SPY/QQQ benchmark and cash returns required")
    if not all(_valid_index(s) for s in (strategy, benchmark, risk_free)):
        return unavailable("Series require unique increasing midnight session dates")
    aligned = pd.DataFrame({"s": strategy, "b": benchmark.reindex(strategy.index), "rf": risk_free.reindex(strategy.index)})
    if not np.isfinite(aligned.to_numpy()).all() or (aligned <= -1).any().any():
        return unavailable("Invalid or missing returns; no imputation")
    prior = pd.Series(dtype=float) if prior_strategy is None else prior_strategy
    if not prior.empty and (not _valid_index(prior) or prior.index[-1] >= strategy.index[0]):
        return unavailable("Calibration strategy must strictly precede evaluation")
    history = pd.concat([prior, strategy]) if not prior.empty else strategy.copy()
    annual = []
    for year, sample in strategy.groupby(strategy.index.year):
        expected = benchmark.loc[f"{year}-01-01":f"{year}-12-31"].index
        complete = (len(sample) >= 240 and sample.index.equals(expected)
                    and sample.index[0] <= pd.Timestamp(year, 1, 7)
                    and sample.index[-1] >= pd.Timestamp(year, 12, 24))
        fit = regression_alpha(sample, benchmark.reindex(sample.index), risk_free.reindex(sample.index))
        annual.append({"year": int(year), "complete": bool(complete), **fit})
    monthly = []
    for period, sample in strategy.groupby(strategy.index.to_period("M")):
        expected = benchmark.loc[period.start_time:period.end_time].index
        complete = (len(sample) >= 15 and sample.index.equals(expected)
                    and sample.index[0].day <= 7 and sample.index[-1].day >= period.days_in_month - 7)
        calibration = history.loc[history.index < period.start_time].tail(BETA_SESSIONS)
        fit = regression_alpha(calibration, benchmark.reindex(calibration.index), risk_free.reindex(calibration.index))
        available = complete and len(calibration) == BETA_SESSIONS and fit["status"] == "available"
        contribution = None
        if available:
            contribution = float(((sample - risk_free.reindex(sample.index)) - fit["beta"] *
                                  (benchmark.reindex(sample.index) - risk_free.reindex(sample.index))).sum() * 100)
        monthly.append({"month": str(period), "complete": bool(complete),
                        "status": "available" if available else "incomplete_month_or_prior_beta",
                        "beta": fit.get("beta") if available else None,
                        "beta_estimated_through": str(calibration.index[-1].date()) if len(calibration) else None,
                        "residual_alpha_contribution_pct": contribution})
    eligible = [r for r in annual if r["complete"] and r["status"] == "available"]
    alphas = [r["alpha_pct"] for r in eligible]
    mean = float(np.mean(alphas)) if alphas else None
    weighted = float(np.average(alphas, weights=[2 if a < 2 else 1 for a in alphas])) if alphas else None
    pooled = regression_alpha(strategy, benchmark.reindex(strategy.index), risk_free.reindex(strategy.index))
    year_gates = {"exactly_ten_complete_years": len(eligible) == len(annual) == 10,
                  "mean_alpha_at_least_5_pct": mean is not None and mean >= 5,
                  "stress_weighted_alpha_at_least_5_pct": weighted is not None and weighted >= 5,
                  "at_most_one_negative_year": sum(a < 0 for a in alphas) <= 1}
    negative_months = sum(r["residual_alpha_contribution_pct"] < 0 for r in monthly if r["status"] == "available")
    month_gates = {"exactly_24_complete_months_with_prior_beta": len(monthly) == 24 and all(r["status"] == "available" for r in monthly),
                   "pooled_annual_alpha_at_least_5_pct": pooled.get("alpha_pct", -np.inf) >= 5,
                   "at_most_two_negative_alpha_months": negative_months <= 2}
    applicable = month_gates if len(monthly) == 24 else year_gates
    return {"status": "available", "policy": contract, "annual": annual, "monthly": monthly, "pooled": pooled,
            "mean_annual_alpha_pct": mean, "stress_weighted_mean_alpha_pct": weighted,
            "complete_years": len(eligible), "negative_years": sum(a < 0 for a in alphas),
            "years_below_2_pct": sum(a < 2 for a in alphas), "negative_alpha_months": negative_months,
            "unavailable_months": sum(r["status"] != "available" for r in monthly),
            "two_year": {"criteria": month_gates, "passes": all(month_gates.values())},
            "ten_year": {"criteria": year_gates, "passes": all(year_gates.values())},
            "criteria": applicable, "passes": all(applicable.values()),
            "qualification": "Numerical gates only; study-level data provenance and unseen evaluation are also required",
            "uncertainty": "Newey-West HAC intervals do not correct for repeated model search"}
