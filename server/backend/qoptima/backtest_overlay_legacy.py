from __future__ import annotations

from typing import Any
import math
import numpy as np
import pandas as pd

from ..market_repository import get_market_repository
from ..provider_credentials import decrypted_credentials
from .problem import QOptimaPortfolioProblem
from .classical_baseline import solve_classical
from .resilience import build_resilience_model
from .hardware_qaoa import HardwareQAOASpec, estimate_hardware_qaoa

_PERIOD_SESSIONS = {"1y": 252, "2y": 504, "5y": 1260, "10y": 2520}
_FETCH_PERIOD = {"1y": "5y", "2y": "5y", "5y": "10y", "10y": "all"}


def _metrics(series: pd.Series, risk_free_rate: float = 0.0) -> dict[str, Any]:
    x = pd.Series(series, dtype=float).dropna()
    if x.empty:
        return {}
    total = float((1.0 + x).prod() - 1.0)
    annual = float((1.0 + total) ** (252.0 / len(x)) - 1.0) if total > -1 else -1.0
    vol = float(x.std(ddof=0) * np.sqrt(252.0))
    daily_rf = (1.0 + float(risk_free_rate)) ** (1.0 / 252.0) - 1.0
    excess = x - daily_rf
    sharpe = float(np.sqrt(252.0) * excess.mean() / excess.std(ddof=0)) if excess.std(ddof=0) > 1e-12 else None
    downside = x[x < daily_rf] - daily_rf
    sortino = float(np.sqrt(252.0) * excess.mean() / downside.std(ddof=0)) if len(downside) > 1 and downside.std(ddof=0) > 1e-12 else None
    curve = (1.0 + x).cumprod()
    dd = curve / curve.cummax() - 1.0
    max_dd = float(dd.min())
    calmar = float(annual / abs(max_dd)) if max_dd < -1e-12 else None
    q05 = float(x.quantile(0.05))
    cvar = float(x[x <= q05].mean()) if np.any(x <= q05) else q05
    return {
        "sessions": int(len(x)),
        "total_return": total,
        "annualized_return": annual,
        "annualized_volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "daily_cvar95": cvar,
    }


def _fetch_prices(user_id: int, universe: list[str], provider: str, period: str, minimum_bars: int) -> pd.DataFrame:
    repo = get_market_repository()
    try:
        credentials = decrypted_credentials(user_id)
    except Exception:
        credentials = {}
    frames = []
    for symbol in universe:
        item = repo.get_history(
            symbol,
            period=period,
            minimum_bars=minimum_bars,
            allow_api=provider != "cache_only",
            provider_preference=provider,
            provider_api_keys=credentials,
            allow_platform_provider_keys=False,
        )
        if item.history is None or item.history.empty or "Close" not in item.history:
            raise ValueError(f"No usable history for {symbol}")
        frames.append(item.history["Close"].astype(float).rename(symbol))
    prices = pd.concat(frames, axis=1, join="inner").dropna()
    if len(prices) < minimum_bars:
        raise ValueError(f"Only {len(prices)} aligned sessions are available; need at least {minimum_bars}")
    return prices


def _run_path(
    returns: pd.DataFrame,
    *,
    lookback: int,
    rebalance: int,
    max_name_weight: float,
    risk_aversion: float,
    turnover_penalty: float,
    risk_free_rate: float,
    transaction_cost_bps: float,
    fragility_weights: dict[str, float],
    correlation_blend: float,
) -> dict[str, Any]:
    n = returns.shape[1]
    if n < 2:
        raise ValueError("QOptima overlay requires at least two symbols")
    target_gross = 1.0
    equal = np.full(n, target_gross / n)
    if equal.max() > max_name_weight + 1e-9:
        raise ValueError("max_name_weight is too low for the selected universe")

    paths = {
        "base_equal_weight": pd.Series(0.0, index=returns.index),
        "classical_normal": pd.Series(0.0, index=returns.index),
        "qoptima_resilience": pd.Series(0.0, index=returns.index),
    }
    current_classical = equal.copy()
    current_qo = equal.copy()
    rebalances = []
    start = lookback
    for anchor in range(start, len(returns), rebalance):
        hist = returns.iloc[max(0, anchor - lookback):anchor]
        if len(hist) < max(63, int(0.7 * lookback)):
            continue
        base_cov = hist.cov().to_numpy(dtype=float) * 252.0
        base_cov = (base_cov + base_cov.T) / 2.0 + np.eye(n) * 1e-9
        mu = 0.50 * hist.mean().to_numpy(dtype=float) * 252.0
        resilience = build_resilience_model(
            hist,
            base_cov,
            tuple(returns.columns),
            downside_weight=fragility_weights.get("downside", 0.0),
            correlation_weight=fragility_weights.get("correlation_collapse", 0.0),
            forecast_weight=0.0,  # historical model forecast panel not yet wired
            regime_weight=fragility_weights.get("regime", 0.0),
            correlation_blend=correlation_blend,
            model_forecasts=None,
        )
        normal_problem = QOptimaPortfolioProblem(
            symbols=tuple(returns.columns), current_weights=current_classical, expected_returns=mu,
            covariance=base_cov, target_gross=1.0, max_name_weight=max_name_weight,
            risk_aversion=risk_aversion, turnover_penalty=turnover_penalty,
            risk_free_rate=risk_free_rate, fragility_enabled=False,
        )
        qo_problem = QOptimaPortfolioProblem(
            symbols=tuple(returns.columns), current_weights=current_qo, expected_returns=mu,
            covariance=base_cov, target_gross=1.0, max_name_weight=max_name_weight,
            risk_aversion=risk_aversion, turnover_penalty=turnover_penalty,
            risk_free_rate=risk_free_rate, fragility_enabled=True,
            effective_covariance=resilience["effective_covariance"],
            fragility_components=resilience["components"],
            fragility_weights=resilience["weights"], fragility_metadata=resilience["metadata"],
        )
        classical = solve_classical(normal_problem)
        qo = solve_classical(qo_problem)  # analytical proxy for repeated walk-forward research
        next_end = min(len(returns), anchor + rebalance)
        window = returns.iloc[anchor:next_end]
        if window.empty:
            break
        cw = np.asarray(classical["weights"], dtype=float)
        qw = np.asarray(qo["weights"], dtype=float)
        base_daily = window.to_numpy(dtype=float) @ equal
        class_daily = window.to_numpy(dtype=float) @ cw
        qo_daily = window.to_numpy(dtype=float) @ qw
        tc = float(transaction_cost_bps) / 10000.0
        class_turnover = float(np.abs(cw - current_classical).sum())
        qo_turnover = float(np.abs(qw - current_qo).sum())
        if len(window):
            class_daily[0] -= tc * class_turnover
            qo_daily[0] -= tc * qo_turnover
        paths["base_equal_weight"].loc[window.index] = base_daily
        paths["classical_normal"].loc[window.index] = class_daily
        paths["qoptima_resilience"].loc[window.index] = qo_daily
        qo_metrics = qo_problem.metrics(qw)
        rebalances.append({
            "date": str(window.index[0].date()) if hasattr(window.index[0], "date") else str(window.index[0]),
            "classical_turnover": class_turnover,
            "qoptima_turnover": qo_turnover,
            "qoptima_qrs": qo_metrics.get("qrs"),
            "qoptima_fragility_premium": qo_metrics.get("fragility_premium"),
            "qoptima_effective_volatility": qo_metrics.get("effective_volatility"),
            "qoptima_normal_volatility": qo_metrics.get("modeled_volatility"),
        })
        current_classical = cw
        current_qo = qw

    eval_index = returns.index[start:]
    results = {}
    for name, series in paths.items():
        series = series.reindex(eval_index).dropna()
        results[name] = _metrics(series, risk_free_rate)
    qo_turnovers = [x["qoptima_turnover"] for x in rebalances]
    qo_fp = [x["qoptima_fragility_premium"] for x in rebalances if x.get("qoptima_fragility_premium") is not None]
    qo_qrs = [x["qoptima_qrs"] for x in rebalances if x.get("qoptima_qrs") is not None]
    results["qoptima_resilience"]["annualized_turnover_proxy"] = float(np.mean(qo_turnovers) * 252.0 / rebalance) if qo_turnovers else 0.0
    results["qoptima_resilience"]["mean_fragility_premium"] = float(np.mean(qo_fp)) if qo_fp else None
    results["qoptima_resilience"]["mean_qrs_at_rebalance"] = float(np.mean(qo_qrs)) if qo_qrs else None
    return {"results": results, "rebalances": rebalances}


def run_qoptima_backtest_overlay(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    universe = []
    for raw in spec.get("universe") or []:
        symbol = str(raw or "").upper().strip()
        if symbol and symbol not in universe:
            universe.append(symbol)
    if len(universe) < 5:
        raise ValueError("Use at least five symbols for QOptima overlay research")
    if len(universe) > 30:
        raise ValueError("QOptima overlay baseline accepts at most 30 symbols")
    period = str(spec.get("evaluation_period") or "1y").lower()
    sessions = _PERIOD_SESSIONS.get(period, 252)
    lookback = max(63, min(504, int(spec.get("lookback_sessions") or 126)))
    rebalance = max(5, min(126, int(spec.get("rebalance_sessions") or 21)))
    prices = _fetch_prices(
        user_id,
        universe,
        str(spec.get("provider") or "cache_only"),
        _FETCH_PERIOD.get(period, "5y"),
        sessions + lookback + 5,
    )
    returns = prices.pct_change().dropna().tail(sessions + lookback)
    if len(returns) <= lookback + 20:
        raise ValueError("Not enough aligned history for requested QOptima walk-forward overlay")
    fragility_weights = {
        "downside": max(0.0, float(spec.get("fragility_downside_weight", 0.25))),
        "correlation_collapse": max(0.0, float(spec.get("fragility_correlation_weight", 0.25))),
        "regime": max(0.0, float(spec.get("fragility_regime_weight", 0.25))),
    }
    core = _run_path(
        returns,
        lookback=lookback,
        rebalance=rebalance,
        max_name_weight=float(spec.get("max_name_weight") or 0.20),
        risk_aversion=float(spec.get("risk_aversion") or 3.0),
        turnover_penalty=float(spec.get("turnover_penalty") or 0.20),
        risk_free_rate=float(spec.get("risk_free_rate") or 0.0),
        transaction_cost_bps=float(spec.get("transaction_cost_bps") or 5.0),
        fragility_weights=fragility_weights,
        correlation_blend=float(spec.get("fragility_correlation_blend") or 0.65),
    )

    # Lightweight ablations use the same causal windows and only change the risk model.
    ablations = {}
    if bool(spec.get("run_ablations", True)):
        modes = {
            "normal_only": {"downside": 0.0, "correlation_collapse": 0.0, "regime": 0.0},
            "plus_downside": {"downside": fragility_weights["downside"], "correlation_collapse": 0.0, "regime": 0.0},
            "plus_correlation": {"downside": 0.0, "correlation_collapse": fragility_weights["correlation_collapse"], "regime": 0.0},
            "plus_regime": {"downside": 0.0, "correlation_collapse": 0.0, "regime": fragility_weights["regime"]},
            "all_available": fragility_weights,
        }
        for name, weights in modes.items():
            run = _run_path(
                returns,
                lookback=lookback, rebalance=rebalance,
                max_name_weight=float(spec.get("max_name_weight") or 0.20),
                risk_aversion=float(spec.get("risk_aversion") or 3.0),
                turnover_penalty=float(spec.get("turnover_penalty") or 0.20),
                risk_free_rate=float(spec.get("risk_free_rate") or 0.0),
                transaction_cost_bps=float(spec.get("transaction_cost_bps") or 5.0),
                fragility_weights=weights,
                correlation_blend=float(spec.get("fragility_correlation_blend") or 0.65),
            )
            ablations[name] = run["results"]["qoptima_resilience"]

    rebalance_count = len(core["rebalances"])
    hardware = estimate_hardware_qaoa(HardwareQAOASpec(
        total_symbols=len(universe),
        qscan_assets=min(int(spec.get("hardware_qscan_assets") or 20), len(universe)),
        bits_per_asset=int(spec.get("hardware_bits_per_asset") or 4),
        include_selection_qubit=bool(spec.get("hardware_selection_qubit", True)),
        qaoa_reps=int(spec.get("hardware_qaoa_reps") or 1),
        optimizer_evaluations=int(spec.get("hardware_optimizer_evaluations") or 28),
        frontier_points=int(spec.get("hardware_frontier_points") or 5),
        estimator_shots=int(spec.get("hardware_estimator_shots") or 1000),
        final_sampler_shots=int(spec.get("hardware_sampler_shots") or 10000),
        mitigation_multiplier=float(spec.get("hardware_mitigation_multiplier") or 1.5),
        planning_price_per_second=float(spec.get("planning_price_per_second") or 1.60),
        local_ram_gb=24.0,
    ))
    per_low = float(hardware["usage"]["estimated_qpu_seconds_low"])
    per_high = float(hardware["usage"]["estimated_qpu_seconds_high"])
    return {
        "mode": "QOPTIMA_WALK_FORWARD_OVERLAY_BASELINE",
        "research_only": True,
        "universe": universe,
        "evaluation_period": period,
        "lookback_sessions": lookback,
        "rebalance_sessions": rebalance,
        "rebalance_count": rebalance_count,
        "results": core["results"],
        "rebalances": core["rebalances"][-50:],
        "ablations": ablations,
        "forecast_disagreement": {
            "available": False,
            "reason": "Historical point-in-time multi-model forecast panel is not wired into this baseline yet; live QOptima supports supplied model forecasts.",
        },
        "hardware_scale_counterfactual": {
            "per_rebalance_plan": hardware,
            "if_every_rebalance_qpu_seconds_low": per_low * rebalance_count,
            "if_every_rebalance_qpu_seconds_high": per_high * rebalance_count,
            "if_every_rebalance_cost_low_usd": float(hardware["usage"]["estimated_cost_low_usd"]) * rebalance_count,
            "if_every_rebalance_cost_high_usd": float(hardware["usage"]["estimated_cost_high_usd"]) * rebalance_count,
            "note": "Counterfactual planning only. The walk-forward baseline uses the same quadratic resilience objective with a classical optimizer; it does not claim historical QPU execution.",
        },
        "next_model_integration": "At each historical rebalance, pass the selected Oryntra model's point-in-time forecast vector(s) into QOptima instead of the trailing-return proxy; use multiple model vectors to populate forecast-disagreement risk without look-ahead.",
    }
