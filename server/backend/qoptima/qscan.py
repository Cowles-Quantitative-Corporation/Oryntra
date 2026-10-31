from __future__ import annotations

from typing import Any
import numpy as np

from .problem import QOptimaPortfolioProblem


def scan_universe(problem: QOptimaPortfolioProblem, *, max_assets: int = 8, candidate_reserve: int = 3) -> dict[str, Any]:
    """Reduce a full active+candidate universe to the securities worth a QPO burst.

    QScan examines the full portfolio classically and sends only a compact set of
    high-risk current positions and attractive/diversifying candidates to QPO.
    """
    max_assets = max(3, min(int(max_assets), problem.n))
    current = np.asarray(problem.current_weights, dtype=float)
    mu = np.asarray(problem.expected_returns, dtype=float)
    cov = np.asarray(problem.covariance, dtype=float)
    effective_cov = np.asarray(problem.optimization_covariance, dtype=float)
    vols = np.sqrt(np.maximum(np.diag(cov), 1e-12))
    effective_vols = np.sqrt(np.maximum(np.diag(effective_cov), 1e-12))
    standalone = (mu - float(problem.risk_free_rate)) / vols
    portfolio_var = max(float(current @ cov @ current), 1e-12)
    portfolio_vol = float(np.sqrt(portfolio_var))
    cov_to_port = cov @ current
    corr_to_port = cov_to_port / np.maximum(vols * portfolio_vol, 1e-12)
    risk_contrib = np.abs(current * cov_to_port)
    if risk_contrib.max(initial=0.0) > 0:
        risk_contrib = risk_contrib / risk_contrib.max()
    fragility_cov = np.maximum(effective_cov - cov, 0.0)
    fragility_contrib = np.abs(current * (fragility_cov @ current))
    if fragility_contrib.max(initial=0.0) > 0:
        fragility_contrib = fragility_contrib / fragility_contrib.max()

    active = [i for i, w in enumerate(current) if w > 1e-8]
    candidates = [i for i, w in enumerate(current) if w <= 1e-8]

    # Active score: size + marginal risk + poor standalone reward/risk + correlation.
    active_scores = {}
    for i in active:
        weak_return = max(0.0, -float(standalone[i]))
        active_scores[i] = float(2.0 * current[i] + risk_contrib[i] + 0.50 * fragility_contrib[i] + 0.35 * abs(corr_to_port[i]) + 0.20 * weak_return)

    # Candidate score: expected reward/risk plus diversification benefit.
    candidate_scores = {}
    for i in candidates:
        diversification = max(0.0, 1.0 - abs(float(corr_to_port[i])))
        candidate_scores[i] = float(max(-2.0, min(5.0, standalone[i])) + 0.75 * diversification)

    cand_slots = min(len(candidates), max(0, min(candidate_reserve, max_assets // 2)))
    active_slots = max_assets - cand_slots
    if active and active_slots < min(2, len(active)):
        active_slots = min(2, len(active))
        cand_slots = max(0, max_assets - active_slots)

    selected_active = sorted(active, key=lambda i: active_scores[i], reverse=True)[:active_slots]
    selected_candidates = sorted(candidates, key=lambda i: candidate_scores[i], reverse=True)[:cand_slots]
    selected = selected_active + selected_candidates

    # Fill spare slots with whichever remaining securities score highest.
    if len(selected) < max_assets:
        remaining = [i for i in range(problem.n) if i not in selected]
        remaining.sort(key=lambda i: active_scores.get(i, candidate_scores.get(i, -999.0)), reverse=True)
        selected.extend(remaining[: max_assets - len(selected)])

    fixed = [i for i in range(problem.n) if i not in selected]
    rows = []
    for i in selected:
        rows.append({
            "symbol": problem.symbols[i],
            "index": int(i),
            "active_weight": float(current[i]),
            "expected_return": float(mu[i]),
            "annualized_volatility": float(vols[i]),
            "effective_volatility": float(effective_vols[i]),
            "asset_fragility_premium": float(effective_vols[i] / vols[i] - 1.0) if vols[i] > 1e-12 else 0.0,
            "fragility_attention": float(fragility_contrib[i]),
            "standalone_sharpe_proxy": float(standalone[i]),
            "correlation_to_active_portfolio": float(np.clip(corr_to_port[i], -1.0, 1.0)),
            "attention_score": float(active_scores.get(i, candidate_scores.get(i, 0.0))),
            "reason": "active_risk_review" if i in active else "candidate_opportunity",
        })
    return {
        "selected_indices": selected,
        "fixed_indices": fixed,
        "selected_symbols": [problem.symbols[i] for i in selected],
        "fixed_symbols": [problem.symbols[i] for i in fixed],
        "selected_active_gross": float(current[selected].sum()) if selected else 0.0,
        "full_portfolio_volatility": portfolio_vol,
        "rows": rows,
        "method": "classical_attention_filter_before_quantum_burst",
    }
