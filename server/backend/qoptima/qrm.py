from __future__ import annotations

from typing import Any
import numpy as np

from .problem import QOptimaPortfolioProblem
from .scoring_memo import prepared_correlation_stress_covariance


DEFAULT_QRM_GATES = {
    "gross_tolerance": 0.015,
    "min_positions": 3,
    "max_hhi": 0.24,
    "max_correlation_stress_vol_ratio": 1.45,
    "max_concentrated_stress_loss": 0.30,
    "max_fragility_premium": 0.60,
    "concentrated_top_n": 3,
    "concentrated_top_shock": -0.35,
    "concentrated_other_shock": -0.10,
}


def _correlation_stress_covariance(cov: np.ndarray, blend: float = 0.65) -> np.ndarray:
    cov = np.asarray(cov, dtype=float)
    vol = np.sqrt(np.maximum(np.diag(cov), 1e-12))
    corr = cov / np.maximum(np.outer(vol, vol), 1e-12)
    stressed = corr.copy()
    n = len(vol)
    for i in range(n):
        stressed[i, i] = 1.0
        for j in range(i + 1, n):
            value = corr[i, j] + float(blend) * (1.0 - corr[i, j])
            stressed[i, j] = stressed[j, i] = float(np.clip(value, -1.0, 1.0))
    return stressed * np.outer(vol, vol)


def evaluate_risk(problem: QOptimaPortfolioProblem, weights: np.ndarray, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = {**DEFAULT_QRM_GATES, **dict(config or {})}
    w = np.asarray(weights, dtype=float)
    metrics = problem.metrics(w)
    cov = np.asarray(problem.covariance, dtype=float)
    mu = np.asarray(problem.expected_returns, dtype=float)
    active_metrics = problem.metrics(problem.current_weights)
    fragility = metrics.get("fragility") or {}

    hhi = float(np.square(w).sum())
    effective_positions = float(1.0 / hhi) if hhi > 1e-12 else 0.0
    cov_to_port = cov @ w
    marginal_variance = 2.0 * cov_to_port
    component_variance = w * cov_to_port
    total_variance = max(float(w @ cov @ w), 1e-12)

    corr_cov = prepared_correlation_stress_covariance(cov, blend=0.65, builder=_correlation_stress_covariance)
    corr_stress_vol = float(np.sqrt(max(0.0, w @ corr_cov @ w)))
    normal_vol = float(metrics["modeled_volatility"])
    corr_stress_ratio = corr_stress_vol / normal_vol if normal_vol > 1e-12 else float("inf")
    vol_spike = float(normal_vol * 1.50)
    broad_market_loss = float(-0.12 * w.sum())

    order = np.argsort(-w)
    top_n = min(int(cfg["concentrated_top_n"]), problem.n)
    concentrated_shock = np.full(problem.n, float(cfg["concentrated_other_shock"]), dtype=float)
    concentrated_shock[order[:top_n]] = float(cfg["concentrated_top_shock"])
    concentrated_loss = float(w @ concentrated_shock)
    expected_minus_one_sigma = float(mu @ w - normal_vol)

    gross_tol = max(float(cfg["gross_tolerance"]), 0.03 * float(problem.target_gross))
    gross_ok = abs(float(w.sum()) - float(problem.target_gross)) <= gross_tol
    cap_ok = bool((w <= float(problem.max_name_weight) + 1e-8).all() and (w >= -1e-10).all())
    positions = int((w > 0.005).sum())
    required_positions = min(problem.n, max(2, int(cfg["min_positions"])))
    diversification_ok = positions >= required_positions
    hhi_ok = bool(hhi <= float(cfg["max_hhi"]) + 1e-12)
    corr_stress_ok = bool(corr_stress_ratio <= float(cfg["max_correlation_stress_vol_ratio"]) + 1e-12)
    concentrated_stress_ok = bool(concentrated_loss >= -abs(float(cfg["max_concentrated_stress_loss"])))
    full_qrs = metrics.get("full_qrs") or {}
    if problem.full_qrs_enabled and full_qrs:
        base_var_full = float(full_qrs.get("base_variance") or 0.0)
        robust_var_full = float(full_qrs.get("robust_variance") or 0.0)
        fp = (np.sqrt(robust_var_full) / np.sqrt(base_var_full) - 1.0) if base_var_full > 1e-18 else 0.0
        fragility_gate_source = "full_qrs_worst_scenario_risk_premium"
        fragility_ok = bool(float(fp) <= float(cfg["max_fragility_premium"]) + 1e-12)
    else:
        fp = fragility.get("fragility_premium")
        fragility_gate_source = "legacy_fragility_premium"
        fragility_ok = bool(fp is None or not problem.fragility_enabled or float(fp) <= float(cfg["max_fragility_premium"]) + 1e-12)

    gates = {
        "gross_ok": gross_ok,
        "position_caps_ok": cap_ok,
        "diversification_ok": diversification_ok,
        "concentration_hhi_ok": hhi_ok,
        "correlation_stress_ok": corr_stress_ok,
        "concentrated_stress_ok": concentrated_stress_ok,
        "fragility_premium_ok": fragility_ok,
    }
    hard_pass = bool(all(gates.values()))

    position_rows = []
    for i, symbol in enumerate(problem.symbols):
        if w[i] <= 1e-8:
            continue
        position_rows.append({
            "symbol": symbol,
            "weight": float(w[i]),
            "marginal_variance": float(marginal_variance[i]),
            "component_variance_share": float(component_variance[i] / total_variance),
        })
    position_rows.sort(key=lambda x: abs(x["component_variance_share"]), reverse=True)

    return {
        "passes_qrm_structural_gates": hard_pass,
        "metrics": metrics,
        "active_sharpe": active_metrics.get("sharpe_ratio"),
        "concentration_hhi": hhi,
        "effective_positions": effective_positions,
        "position_count_over_0_5pct": positions,
        "stress_tests": {
            "broad_market_minus_12pct": {"portfolio_return": broad_market_loss},
            "concentrated_top_shock": {
                "portfolio_return": concentrated_loss,
                "top_n": top_n,
                "top_shock": float(cfg["concentrated_top_shock"]),
                "other_shock": float(cfg["concentrated_other_shock"]),
            },
            "volatility_plus_50pct": {"modeled_volatility": vol_spike},
            "correlation_convergence": {
                "modeled_volatility": corr_stress_vol,
                "volatility_ratio": corr_stress_ratio,
            },
            "expected_return_minus_one_sigma": {"annualized_return_proxy": expected_minus_one_sigma},
        },
        "structural_gates": gates,
        "gate_configuration": cfg,
        "top_risk_positions": position_rows[:8],
        "fragility": {
            "enabled": bool(problem.fragility_enabled),
            "normal_volatility": fragility.get("normal_volatility"),
            "effective_volatility": fragility.get("effective_volatility"),
            "alpha_volatility": fragility.get("alpha_volatility"),
            "fragility_premium": fragility.get("fragility_premium"),
            "qrm_fragility_gate_premium": fp,
            "qrm_fragility_gate_source": fragility_gate_source,
            "qrs": fragility.get("qrs"),
            "gross_qrs": fragility.get("gross_qrs"),
            "cost_adjusted_qrs": fragility.get("cost_adjusted_qrs"),
            "band": fragility.get("band"),
            "band_source": fragility.get("band_source"),
            "historical_percentile": fragility.get("historical_percentile"),
            "components": fragility.get("component_breakdown") or {},
            "weights": dict(problem.fragility_weights or {}),
            "metadata": dict(problem.fragility_metadata or {}),
        },
        "full_qrs": full_qrs,
        "role": "QRM is a research/advisory gate. Universal Risk V2.0.4 and Control remain separate mandatory downstream gates.",
    }
