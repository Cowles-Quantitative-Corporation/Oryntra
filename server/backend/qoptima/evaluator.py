from __future__ import annotations

import numpy as np

from .problem import QOptimaPortfolioProblem


def comparison(problem: QOptimaPortfolioProblem, classical: np.ndarray, qoptima: np.ndarray) -> dict:
    return {
        "active": problem.metrics(problem.current_weights),
        "classical": problem.metrics(classical),
        "qoptima": problem.metrics(qoptima),
        "qoptima_vs_classical_utility": float(problem.objective(qoptima) - problem.objective(classical)),
        "qoptima_vs_active_utility": float(problem.objective(qoptima) - problem.objective(problem.current_weights)),
    }


def targeted_position_checks(problem: QOptimaPortfolioProblem, proposed: np.ndarray, *, minimum_change: float = 0.005, max_checks: int = 8) -> list[dict]:
    current = np.asarray(problem.current_weights, dtype=float)
    proposed = np.asarray(proposed, dtype=float)
    cov = np.asarray(problem.covariance, dtype=float)
    mu = np.asarray(problem.expected_returns, dtype=float)
    variance = max(float(proposed @ cov @ proposed), 1e-12)
    portfolio_vol = float(np.sqrt(variance))
    rows: list[dict] = []
    for i, symbol in enumerate(problem.symbols):
        delta = float(proposed[i] - current[i])
        if abs(delta) < minimum_change and not (current[i] > 0 and proposed[i] <= 1e-6):
            continue
        asset_vol = float(np.sqrt(max(0.0, cov[i, i])))
        covariance_to_portfolio = float(cov[i] @ proposed)
        corr_to_portfolio = covariance_to_portfolio / max(asset_vol * portfolio_vol, 1e-12)
        marginal_variance = float(2.0 * covariance_to_portfolio)
        if current[i] <= 1e-8 and proposed[i] > 1e-8:
            action = "ADD"
        elif proposed[i] <= 1e-8 and current[i] > 1e-8:
            action = "REMOVE"
        elif delta > 0:
            action = "INCREASE"
        else:
            action = "REDUCE"
        rows.append({
            "symbol": symbol,
            "action": action,
            "current_weight": float(current[i]),
            "proposed_weight": float(proposed[i]),
            "weight_change": delta,
            "expected_return_proxy": float(mu[i]),
            "annualized_volatility": asset_vol,
            "correlation_to_proposed_portfolio": float(np.clip(corr_to_portfolio, -1.0, 1.0)),
            "marginal_variance": marginal_variance,
            "review_mode": "qoptima_targeted_position_burst",
        })
    rows.sort(key=lambda r: abs(r["weight_change"]), reverse=True)
    return rows[:max_checks]
