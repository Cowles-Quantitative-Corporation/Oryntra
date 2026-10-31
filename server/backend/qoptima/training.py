from __future__ import annotations

from typing import Any
import numpy as np


def training_blueprint() -> dict[str, Any]:
    return {
        "mode": "walk_forward_point_in_time_deep_research",
        "objective": "maximize realized out-of-sample QRS/Sharpe robustness subject to drawdown, turnover, liquidity, QRM and Risk V2 gates",
        "loop": [
            "freeze a historical as-of date",
            "build PIT Alpha/Beta/market inputs available on that date only",
            "run QScan to choose the burst universe",
            "run QPO across multiple risk-aversion points with real Qiskit circuit simulation",
            "run QRM structural + fat-tail Monte Carlo/stress review",
            "freeze the highest-QRS viable candidate (ordinary Sharpe fallback when fragility is disabled)",
            "reveal the next out-of-sample window",
            "score realized return, volatility, Sharpe, drawdown, turnover and tail loss",
            "store whether the expensive quantum burst materially beat the classical baseline",
            "update calibration data without leaking future information",
        ],
        "calibration_targets": {
            "QScan": [
                "when a quantum burst adds value",
                "universe size",
                "candidate reserve",
                "attention thresholds",
                "which active/candidate interactions most often matter",
            ],
            "QPO": [
                "risk-aversion frontier",
                "weight bit depth",
                "turnover penalty",
                "QAOA depth",
                "QAOA initialization/restarts",
                "gross penalty",
                "compute seconds per frontier point",
                "solution improvement per circuit evaluation",
            ],
            "Fragility/QRS": [
                "downside covariance weight",
                "correlation-collapse weight",
                "forecast-disagreement weight",
                "regime fragility weight",
                "empirical Fragility Premium percentiles",
                "minimum economically meaningful QRS improvement",
            ],
            "QRM": [
                "fat-tail severity",
                "correlation stress",
                "volatility multipliers",
                "Monte Carlo path count",
                "risk rejection thresholds",
                "which simulated tail metrics predict realized drawdown",
            ],
        },
        "learning_records": [
            "classical Sharpe vs QOptima expected Sharpe",
            "realized out-of-sample Sharpe",
            "QPU-style circuit evaluations and estimated cost",
            "Mac simulation seconds",
            "QRM tail VaR/CVaR, Fragility Premium/QRS, and realized drawdown",
            "whether QScan was right to invoke an expensive burst",
        ],
        "non_negotiable": [
            "PIT data only",
            "classical benchmark on every study",
            "no promotion from in-sample performance alone",
            "QOptima remains candidate/advisory only",
            "Risk V2.0.4 and Control remain downstream execution gates",
        ],
    }


def realized_window_score(returns, risk_free_rate: float = 0.0) -> dict[str, float | None]:
    r = np.asarray(list(returns), dtype=float)
    if r.size == 0:
        return {"return": None, "volatility": None, "sharpe": None, "max_drawdown": None}
    wealth = np.cumprod(1.0 + r)
    total_return = float(wealth[-1] - 1.0)
    vol = float(np.std(r, ddof=1) * np.sqrt(252.0)) if r.size > 1 else 0.0
    annualized = float(np.mean(r) * 252.0)
    sharpe = float((annualized - risk_free_rate) / vol) if vol > 1e-12 else None
    peaks = np.maximum.accumulate(wealth)
    dd = wealth / np.maximum(peaks, 1e-12) - 1.0
    return {"return": total_return, "volatility": vol, "sharpe": sharpe, "max_drawdown": float(dd.min())}
