from __future__ import annotations

"""Causal QOptima walk-forward research.

This module deliberately separates two studies:
1. ``classical_resilience_study`` -- classical optimization using each selected
   covariance/resilience formulation.
2. ``true_qoptima_walkforward`` -- QScan -> discrete QUBO -> selected local
   QAOA/exact backend -> QRM, with no failed QRM candidate ever promoted.

No historical result is described as a QPU result.  Hardware cost is a planning
counterfactual only.
"""

from typing import Any, Callable
import math
import time
import numpy as np
import pandas as pd

from ..market_repository import get_market_repository
from ..provider_credentials import decrypted_credentials
from .problem import QOptimaPortfolioProblem
from .classical_baseline import solve_classical
from .covariance import estimate_covariance, COVARIANCE_METHODS
from .resilience import build_resilience_model
from .calibration import calibrate_resilience_weights, calibrate_fragility_bands
from .qscan import scan_universe
from .qpo import generate_portfolio_candidates, _backend
from .qrm import evaluate_risk
from .hardware_qaoa import HardwareQAOASpec, estimate_hardware_qaoa
from .forecast_contract import select_point_in_time_forecasts, forecast_error_sample_matrix
from .full_qrs import build_full_qrs_scenarios, full_qrs_exact_qaoa_search, build_exact_full_qrs_diagonal_objective, FULL_QRS_SCHEMA
from .market_data import canonical_price_series
from .backends.diagonal_qaoa import ExactDiagonalQAOA
from .replay_checkpoint import checkpoint_run_lock
from ..historical_universe import materialize_universe_schedule

_PERIOD_SESSIONS = {"3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "3y": 756, "5y": 1260, "10y": 2520}
_FETCH_PERIOD = {"3mo": "all", "6mo": "all", "1y": "all", "2y": "all", "3y": "all", "5y": "all", "10y": "all"}


def _metrics(series: pd.Series, risk_free_rate: float = 0.0) -> dict[str, Any]:
    x = pd.Series(series, dtype=float).dropna()
    if x.empty:
        return {}
    total = float((1.0 + x).prod() - 1.0)
    annual = float((1.0 + total) ** (252.0 / len(x)) - 1.0) if total > -1 else -1.0
    vol = float(x.std(ddof=0) * np.sqrt(252.0))
    daily_rf = (1.0 + float(risk_free_rate)) ** (1.0 / 252.0) - 1.0
    excess = x - daily_rf
    exstd = float(excess.std(ddof=0))
    sharpe = float(np.sqrt(252.0) * excess.mean() / exstd) if exstd > 1e-12 else None
    downside = x[x < daily_rf] - daily_rf
    dstd = float(downside.std(ddof=0)) if len(downside) > 1 else 0.0
    sortino = float(np.sqrt(252.0) * excess.mean() / dstd) if dstd > 1e-12 else None
    curve = (1.0 + x).cumprod()
    dd = curve / np.maximum(1.0, curve.cummax()) - 1.0
    max_dd = float(dd.min())
    calmar = float(annual / abs(max_dd)) if max_dd < -1e-12 else None
    q05 = float(x.quantile(0.05))
    cvar = float(x[x <= q05].mean()) if np.any(x <= q05) else q05
    return {
        "sessions": int(len(x)), "total_return": total, "annualized_return": annual,
        "annualized_volatility": vol, "sharpe": sharpe, "sortino": sortino,
        "max_drawdown": max_dd, "calmar": calmar, "daily_cvar95": cvar,
    }


def _fetch_price_panel(user_id: int, universe: list[str], provider: str, period: str, minimum_bars: int, *, require_adjusted: bool = True, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    repo = get_market_repository()
    try:
        credentials = decrypted_credentials(user_id)
    except Exception:
        credentials = {}
    frames: list[pd.Series] = []
    metadata: dict[str, Any] = {}
    unavailable: list[str] = []
    total_symbols = max(1, len(universe))
    for symbol_index, symbol in enumerate(universe, start=1):
        if progress_callback is not None:
            progress_callback({"stage":"market_data","message":f"Loading market history {symbol_index}/{total_symbols}: {symbol}","fraction":float((symbol_index-1)/total_symbols)})
        try:
            item = repo.get_history(
                symbol, period=period, minimum_bars=min(63, minimum_bars),
                allow_api=provider != "cache_only", provider_preference=provider,
                provider_api_keys=credentials, allow_platform_provider_keys=False,
            )
        except Exception as exc:
            unavailable.append(symbol)
            metadata[symbol] = {"available": False, "error": str(exc)}
            continue
        if item.history is None or item.history.empty or "Close" not in item.history:
            unavailable.append(symbol)
            metadata[symbol] = {"available": False, "error": "no usable Close history"}
            continue
        frame = item.history
        try:
            s, price_meta = canonical_price_series(
                repo, symbol, frame, minimum_bars=min(63, minimum_bars), require_adjusted=bool(require_adjusted)
            )
        except Exception as exc:
            unavailable.append(symbol)
            metadata[symbol] = {
                "available": False, "provider": item.metadata.provider, "source": item.metadata.source,
                "error": str(exc), "price_basis": "unavailable_or_unverified", "verified_adjusted": False,
            }
            continue
        frames.append(s)
        metadata[symbol] = {
            "available": True,
            "provider": item.metadata.provider,
            "source": item.metadata.source,
            "bars": int(price_meta.get("bars") or item.metadata.bars or 0),
            "first_bar": str(s.dropna().index[0]) if s.notna().any() else None,
            "last_bar": item.metadata.last_bar,
            **price_meta,
        }
    if progress_callback is not None:
        progress_callback({"stage":"market_data","message":f"Market history loaded for {len(frames)} usable symbols","fraction":1.0})
    if len(frames) < 5:
        raise ValueError("Fewer than five requested symbols have usable point-in-time histories")
    prices = pd.concat(frames, axis=1, join="outer").sort_index()
    prices = prices[~prices.index.duplicated(keep="last")]
    if len(prices) < minimum_bars:
        raise ValueError(f"Only {len(prices)} calendar sessions are available; need at least {minimum_bars}")
    adjusted_ok = all(v.get("verified_adjusted") is True for v in metadata.values() if v.get("available"))
    provenance = {
        "symbols_requested": universe,
        "symbols_available": [c for c in prices.columns],
        "symbols_unavailable": unavailable,
        "market_data": metadata,
        "point_in_time_universe_rule": "At each rebalance a symbol is eligible only from history observed through that date. Partial/delisted histories are retained when supplied by the provider.",
        "survivorship_warning": "A static user-supplied universe is not a survivorship-bias-free historical constituent database. Delisted names are included only when their histories are present in the source.",
        "price_adjustment_verified": bool(adjusted_ok),
        "price_adjustment_warning": None if adjusted_ok else "At least one included series is not explicitly adjusted/total-return compatible. Dividend handling is unverified; delisting-return handling always depends on source coverage.",
        "require_adjusted_prices": bool(require_adjusted),
        "dividend_handling": "verified adjusted/total-return price series" if adjusted_ok else "not fully verified",
        "delisting_return_handling": "provider/source dependent; missing future cells are reported and never future-filled",
    }
    return prices, provenance


def _eligible(hist: pd.DataFrame, min_sessions: int) -> list[str]:
    out = []
    for c in hist.columns:
        s = hist[c]
        if int(s.notna().sum()) >= min_sessions and pd.notna(s.iloc[-1]):
            out.append(str(c))
    return out


def _normalize_target(w: np.ndarray, max_name: float) -> np.ndarray:
    x = np.clip(np.asarray(w, dtype=float), 0.0, max_name)
    if x.sum() <= 1e-12:
        return x
    # Do not force cash away when caps make 100% impossible.
    for _ in range(100):
        total = float(x.sum())
        if total >= 1.0 - 1e-10:
            x *= min(1.0, 1.0 / total)
            break
        room = np.maximum(max_name - x, 0.0)
        add = min(1.0 - total, float(room.sum()))
        if add <= 1e-12:
            break
        x += add * room / max(float(room.sum()), 1e-12)
    return x


def _carry_weights(old_symbols: list[str], old_w: np.ndarray, new_symbols: list[str]) -> np.ndarray:
    m = {str(s): float(old_w[i]) for i, s in enumerate(old_symbols)}
    return np.asarray([m.get(s, 0.0) for s in new_symbols], dtype=float)


def _run_holding_window(target: np.ndarray, window: pd.DataFrame, *, risk_free_rate: float, transaction_cost: float, lending_rate: float | None = None, borrowing_rate: float | None = None) -> tuple[pd.Series, np.ndarray, int]:
    """Hold a self-financed book with signed cash and one proportional trade cost.

    Explicit lending/borrowing rates override the legacy common financing rate.
    Negative cash is debt and remains in the equity denominator.
    The one-time cost scales the whole book, so it does not change drift weights.
    """
    w = np.asarray(target, dtype=float).copy()
    if w.ndim != 1 or len(w) != window.shape[1] or not np.all(np.isfinite(w)) or np.any(w < 0):
        raise ValueError("Holding target must be a finite, nonnegative vector aligned with return columns")
    rate = float(risk_free_rate if lending_rate is None else lending_rate)
    borrow = float(rate if borrowing_rate is None else borrowing_rate)
    cost = float(transaction_cost)
    if not np.isfinite(rate) or rate <= -1.0 or not np.isfinite(borrow) or borrow <= -1.0:
        raise ValueError("Holding annual financing rate must be finite and greater than -1")
    if not np.isfinite(cost) or cost < 0.0 or cost >= 1.0:
        raise ValueError("Holding transaction cost must be finite and in [0, 1)")
    gross = float(w.sum())
    if not np.isfinite(gross):
        raise ValueError("Holding target gross must be finite")
    cash = 1.0 - gross
    lend_daily = (1.0 + rate) ** (1.0 / 252.0) - 1.0
    borrow_daily = (1.0 + borrow) ** (1.0 / 252.0) - 1.0
    out = []
    missing_cells = 0
    for day, (_, row) in enumerate(window.iterrows()):
        r = row.to_numpy(dtype=float)
        miss = ~np.isfinite(r)
        missing_cells += int(miss.sum())
        # Missing post-decision returns cannot be repaired with future knowledge.
        # Hold at last marked value (0 return) and flag this in provenance.
        r = np.where(miss, 0.0, r)
        if np.any(r < -1.0):
            raise ValueError("Holding simple asset returns cannot be below -1")
        rf_daily = lend_daily if cash >= 0 else borrow_daily
        port_ret = float(w @ r + cash * rf_daily)
        if day == 0 and cost > 0:
            port_ret = (1.0 - cost) * (1.0 + port_ret) - 1.0
        if not np.isfinite(port_ret) or 1.0 + port_ret <= 0.0:
            raise ValueError("Holding wealth must remain finite and strictly positive")
        out.append(port_ret)
        invested_values = w * (1.0 + r)
        cash_value = cash * (1.0 + rf_daily)
        denom = float(invested_values.sum() + cash_value)
        if not np.isfinite(denom) or denom <= 0.0:
            raise ValueError("Holding equity must remain finite and strictly positive")
        w = invested_values / denom
        cash = cash_value / denom
        if not np.all(np.isfinite(w)) or not np.isfinite(cash):
            raise ValueError("Holding drift weights must remain finite")
    return pd.Series(out, index=window.index, dtype=float), w, missing_cells


def _make_problem(
    hist: pd.DataFrame,
    symbols: list[str],
    current: np.ndarray,
    *,
    covariance_method: str,
    covariance_parameters: dict[str, Any],
    alpha: float,
    max_name_weight: float,
    risk_aversion: float,
    turnover_penalty: float,
    risk_free_rate: float,
    transaction_cost_bps: float,
    correlation_blend: float,
    decision_time: Any | None = None,
    forecast_records: list[dict[str, Any]] | None = None,
    required_forecast_horizon_sessions: int | None = None,
    component_mask: set[str] | None = None,
    full_qrs_enabled: bool = False,
    full_qrs_beta: float = 0.10,
    full_qrs_bootstrap_reps: int = 128,
    full_qrs_quantile_confidence: float = 0.90,
    full_qrs_max_condition_number: float = 1.0e8,
    v1a_enabled: bool = False,
    v1a_artifact_path: str | None = None,
    v1a_es_budget: float | None = None,
    v1a_es_activation_max_ratio: float | None = None,
    v1a_risk_budget_annual: float = 0.15,
    v1a_risk_floor_ratio: float = 0.10,
    v1a_covariance_additive_floor: float = 1e-8,
    v1a_forecast_error_covariance_floor: float = 1e-8,
    v1a_scale_tolerance: float = 0.03,
    target_gross_override: float | None = None,
    gamma_enabled: bool = False,
    gamma_feature_cutoff: Any | None = None,
    gamma_min_error_records: int = 2,
    forecast_error_history: list[dict[str, Any]] | None = None,
    gamma_score_basis: bool = False,
    gamma_config: dict[str, Any] | None = None, gamma_session_index=None,
    lending_rate: float | None = None, borrowing_rate: float | None = None,
) -> tuple[QOptimaPortfolioProblem, dict[str, Any]]:
    clean = hist[symbols].dropna(how="any")
    est = estimate_covariance(clean, covariance_method, parameters=covariance_parameters)

    pit = {
        "available": False,
        "provenance": [],
        "rejected": [],
        "forecast_error_covariance": None,
        "forecast_error_metadata": {"available": False, "reason": "no_forecast_records_supplied"},
        "records": [],
    }
    gamma_audit = None
    if gamma_enabled:
        from .gamma_integration import prepare_gamma_inputs, validate_gamma_config
        gcfg = validate_gamma_config(gamma_config)
        prepared = prepare_gamma_inputs(
            forecast_records, decision_time=decision_time,
            feature_cutoff=gamma_feature_cutoff, symbols=symbols,
            horizon_sessions=required_forecast_horizon_sessions,
            min_error_records=gamma_min_error_records, error_records=forecast_error_history,
            required_beta=bool(gcfg.get("require_beta", False)), required_sec=bool(gcfg.get("require_sec", False)), session_index=gamma_session_index,
            require_verified_macro_vintages=bool(gcfg.get("require_verified_macro_vintages", False)),
        )
        pit, gamma_audit = prepared["pit"], prepared["audit"]
    elif forecast_records and decision_time is not None:
        pit = select_point_in_time_forecasts(
            forecast_records,
            decision_time=(gamma_feature_cutoff if gamma_score_basis and gamma_feature_cutoff is not None else decision_time),
            symbols=symbols,
            required_horizon_sessions=required_forecast_horizon_sessions,
            max_age_days=max(7, 2 * int(required_forecast_horizon_sessions or 21)),
        )

    calibration = calibrate_resilience_weights(
        clean, est.covariance, correlation_blend=correlation_blend,
        covariance_method=covariance_method, covariance_parameters=covariance_parameters,
        forecast_error_covariance=pit.get("forecast_error_covariance"),
    )
    weights = dict(calibration["weights"])
    if component_mask is not None:
        for name in ("downside", "correlation_collapse", "forecast_disagreement", "regime"):
            if name not in component_mask:
                weights[name] = 0.0

    bands = calibrate_fragility_bands(
        clean,
        covariance_method=covariance_method,
        covariance_parameters=covariance_parameters,
        resilience_weights=weights,
        correlation_blend=correlation_blend,
    )
    calibration["fragility_bands"] = bands

    model_forecasts = None
    if pit.get("records"):
        model_forecasts = {
            f"{r.model_id}:{r.model_version}": {s: float(r.values[s]) for s in symbols}
            for r in pit["records"]
        }
    resilience = build_resilience_model(
        clean, est.covariance, tuple(symbols),
        downside_weight=weights.get("downside", 0.0),
        correlation_weight=weights.get("correlation_collapse", 0.0),
        forecast_weight=weights.get("forecast_disagreement", 0.0),
        regime_weight=weights.get("regime", 0.0),
        correlation_blend=correlation_blend,
        model_forecasts=model_forecasts,
        forecast_error_covariance=pit.get("forecast_error_covariance"),
        forecast_error_metadata=pit.get("forecast_error_metadata"),
        allow_experimental_forecast_disagreement=False,
        alpha=alpha,
    )
    if pit.get("available") and pit.get("annualized_consensus") is not None:
        mu = np.asarray(pit["annualized_consensus"], dtype=float)
        forecast_source = "point_in_time_model_consensus"
    else:
        mu = 0.50 * clean.mean().to_numpy(dtype=float) * 252.0
        forecast_source = "trailing_return_proxy_shrunk_50pct"
    lend = float(risk_free_rate if lending_rate is None else lending_rate)
    borrow = float(lend if borrowing_rate is None else borrowing_rate)
    if gamma_score_basis or gamma_enabled:
        from .gamma_integration import gamma_score_rates
        rates = gamma_score_rates(int(required_forecast_horizon_sessions or 0), lend, borrow, transaction_cost_bps)
        lend, borrow, transaction_cost_bps = rates["risk_free_rate"], rates["borrowing_rate"], rates["transaction_cost_bps"]
    risk_free_rate = lend
    gross = min(1.0, max(1e-9, float(current.sum()))) if target_gross_override is None else float(np.clip(target_gross_override, 1e-9, 1.0))
    provenance = est.as_dict(include_matrix=False)
    provenance["scoring_horizon"] = ("linear_annual_equivalent_not_compound_return" if gamma_score_basis or gamma_enabled else "annualized_252_session_equivalent")
    provenance["gamma"] = gamma_audit
    provenance["decision_history_cutoff"] = str(hist.index[-1])
    provenance["forecast_source"] = forecast_source
    provenance["forecast_contract"] = {
        "available": bool(pit.get("available")),
        "compatible_models": int(pit.get("compatible_models") or 0),
        "provenance": pit.get("provenance") or [],
        "rejected": pit.get("rejected") or [],
        "stale": pit.get("stale") or [],
        "forecast_error_metadata": pit.get("forecast_error_metadata") or {},
        "required_source_horizon_sessions": required_forecast_horizon_sessions,
    }
    full_qrs_errors, full_qrs_error_meta = forecast_error_sample_matrix(
        pit.get("historical_realized_records") or [], symbols
    )
    v1a_snapshot = None
    if bool(v1a_enabled):
        if not v1a_artifact_path:
            raise ValueError("historical V1A replay requires a causally eligible frozen artifact path")
        from .v1a import infer_v1a_snapshot
        v1a_snapshot = infer_v1a_snapshot(
            v1a_artifact_path, clean, information_cutoff=str(gamma_feature_cutoff if gamma_feature_cutoff is not None else decision_time),
            es_budget_override=v1a_es_budget, r_max_override=v1a_es_activation_max_ratio,
        )
        if required_forecast_horizon_sessions is not None and int(v1a_snapshot.horizon_sessions) != int(required_forecast_horizon_sessions):
            raise ValueError("historical V1A artifact horizon does not match replay horizon")
        if v1a_snapshot.signed_control:
            raise ValueError("signed V1A-R artifact is validation-only and cannot drive QPO replay")

    full_qrs_scenarios, full_qrs_scenario_meta = build_full_qrs_scenarios(
        clean, np.asarray(est.covariance, dtype=float),
        correlation_blend=correlation_blend,
        max_condition_number=full_qrs_max_condition_number,
        v1a_gamma=(None if v1a_snapshot is None else float(v1a_snapshot.gamma)),
        v1a_additive_floor=float(v1a_covariance_additive_floor),
    )
    provenance["v1a"] = {
        "enabled": bool(v1a_snapshot is not None),
        "state": None if v1a_snapshot is None else v1a_snapshot.as_dict(),
        "historical_causality_contract": "artifact training cutoff must predate information cutoff",
    }
    provenance["full_qrs"] = {
        "enabled": bool(full_qrs_enabled),
        "schema": FULL_QRS_SCHEMA,
        "forecast_error_samples": full_qrs_error_meta,
        "scenario_set": full_qrs_scenario_meta,
        "historical_replay_note": "Full-QRS QAOA uses only information available by each rebalance information cutoff.",
    }
    problem = QOptimaPortfolioProblem(
        symbols=tuple(symbols), current_weights=np.asarray(current, dtype=float), expected_returns=mu,
        covariance=np.asarray(est.covariance, dtype=float), target_gross=gross,
        max_name_weight=max_name_weight, risk_aversion=risk_aversion,
        turnover_penalty=turnover_penalty, risk_free_rate=risk_free_rate,
        transaction_cost_bps=transaction_cost_bps, horizon_sessions=int(required_forecast_horizon_sessions or 252),
        fragility_enabled=bool(alpha > 0), effective_covariance=resilience["effective_covariance"],
        alpha_covariance=resilience["alpha_covariance"], resilience_alpha=float(alpha),
        fragility_components=resilience["components"], fragility_weights=resilience["weights"],
        fragility_metadata={**resilience["metadata"], "calibration": calibration, "fragility_bands": bands},
        covariance_method=est.method, covariance_provenance=provenance,
        full_qrs_enabled=bool(full_qrs_enabled),
        lending_rate=lend, borrowing_rate=borrow,
        full_qrs_beta=float(full_qrs_beta),
        full_qrs_bootstrap_reps=int(full_qrs_bootstrap_reps),
        full_qrs_quantile_confidence=float(full_qrs_quantile_confidence),
        forecast_error_samples=full_qrs_errors,
        v1a_forecast_error_regime_labels=tuple(full_qrs_error_meta.get("v1a_regime_tail_v1_labels") or []) if full_qrs_errors is not None else None,
        v1a_forecast_error_information_cutoffs=(tuple(full_qrs_error_meta.get("record_information_cutoffs") or []) if gamma_enabled and full_qrs_errors is not None else None),
        v1a_forecast_error_realized_at=(tuple(full_qrs_error_meta.get("record_realized_at") or []) if gamma_enabled and full_qrs_errors is not None else None),
        v1a_current_regime_label=pit.get("v1a_current_regime_tail_v1_label"),
        full_qrs_stress_covariances=full_qrs_scenarios,
        full_qrs_stress_metadata=full_qrs_scenario_meta,
        v1a_enabled=bool(v1a_snapshot is not None),
        v1a_state_metadata=(None if v1a_snapshot is None else v1a_snapshot.as_dict()),
        v1a_gamma=(0.0 if v1a_snapshot is None else float(v1a_snapshot.gamma)),
        v1a_risk_budget_annual=float(v1a_risk_budget_annual),
        v1a_risk_floor_ratio=float(v1a_risk_floor_ratio),
        v1a_forecast_error_covariance_floor=float(v1a_forecast_error_covariance_floor),
        v1a_scale_tolerance=float(v1a_scale_tolerance),
    )
    calibration["forecast_contract"] = provenance["forecast_contract"]
    calibration["forecast_source"] = forecast_source
    calibration["component_mask"] = sorted(component_mask) if component_mask is not None else "all_validated"
    return problem, calibration



def _metric_rank_value(metrics: dict[str, Any]) -> float:
    for key in ("cost_adjusted_qrs", "qrs", "sharpe_ratio"):
        value = metrics.get(key)
        if value is not None and np.isfinite(value):
            return float(value)
    return -1e9


class QPOResearchRejection(ValueError):
    """No authorized finite-domain allocation; carries auditable rejection data."""
    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.research_diagnostics = dict(diagnostics)


def _replay_viability(problem: QOptimaPortfolioProblem, weights: np.ndarray, minimum_positive_positions: int = 0) -> dict[str, Any]:
    """Optional caller domain restriction; the QRM and Full-QRS equations are unchanged."""
    minimum = int(minimum_positive_positions)
    if minimum:
        w = np.asarray(weights, dtype=float)
        cheap_gates = {
            "research_minimum_positive_positions": bool(np.count_nonzero(w > 1e-12) >= minimum),
            "gross_ok": bool(abs(float(w.sum())-float(problem.target_gross)) <= max(.015, .03*float(problem.target_gross))),
            "position_caps_ok": bool(np.isfinite(w).all() and (w >= -1e-10).all() and (w <= float(problem.max_name_weight)+1e-8).all()),
        }
        if not all(cheap_gates.values()):
            return {"passes_qrm_structural_gates": False, "structural_gates": cheap_gates,
                "domain_rejected_before_full_qrs": True}
    result = evaluate_risk(problem, np.asarray(weights, dtype=float))
    if minimum:
        result = {**result, "structural_gates": {**result["structural_gates"],
            "research_minimum_positive_positions": True}}
    return result


def _choose_qoptima(
    problem: QOptimaPortfolioProblem, *, qscan_assets: int, bits: int, backend: str, seed: int,
    full_qrs_primary: bool = False, full_qrs_threshold_iterations: int = 4,
    full_qrs_active_set_iterations: int = 3, full_qrs_exact_variable_cap: int = 12,
    full_qrs_qaoa_reps: int = 2, full_qrs_qaoa_evaluations: int = 96,
    full_qrs_decision_mode: str = "qaoa_observed", simulator_audit: bool = True,
    minimum_positive_positions: int = 0,
    rejection_policy: str = "abort",
    construction_policy: str = "legacy_exact", risk_transform=None,
) -> tuple[np.ndarray, dict[str, Any]]:
    from .gamma_construction import POLICIES, choose_joint_reference
    if construction_policy == "qpo_budget_completion_reference":
        if not (full_qrs_primary and problem.full_qrs_enabled and full_qrs_decision_mode == "exact_reference") or risk_transform is not None:
            raise ValueError("QPO residual-budget research requires native exact reference and select-then-overlay")
        from .qpo_budget_completion import choose_qpo_budget_completion
        return choose_qpo_budget_completion(problem, qscan_assets=qscan_assets, bits=bits, minimum_positions=minimum_positive_positions)
    if construction_policy not in POLICIES:
        raise ValueError("Unknown Gamma construction policy")
    if construction_policy != "legacy_exact":
        if not (full_qrs_primary and problem.full_qrs_enabled and full_qrs_decision_mode == "exact_reference"):
            raise ValueError("Gamma construction references require explicit Full-QRS reference mode")
        return choose_joint_reference(problem, policy=construction_policy, qscan_assets=qscan_assets,
            bits=bits, minimum_positions=minimum_positive_positions, risk_transform=risk_transform)
    if risk_transform is not None:
        raise ValueError("Legacy exact construction cannot silently gain candidate-level Risk205")
    from .scoring_memo import memoized_metrics_problem
    problem = memoized_metrics_problem(problem)
    minimum = int(minimum_positive_positions)
    if minimum < 0 or minimum > problem.n:
        raise ValueError("replay_min_positive_positions must be within the available universe")
    if minimum and not (full_qrs_primary and problem.full_qrs_enabled and str(full_qrs_decision_mode) == "exact_reference"):
        raise ValueError("The minimum-position research domain requires exact Full-QRS reference decisions")
    if rejection_policy not in {"abort", "cash_on_qrm_rejection"}:
        raise ValueError("Unknown replay_rejection_policy")
    if rejection_policy != "abort" and not (full_qrs_primary and problem.full_qrs_enabled and str(full_qrs_decision_mode) == "exact_reference"):
        raise ValueError("The forced-cash research policy requires exact Full-QRS reference decisions")
    reserve = max(1, min(2, qscan_assets // 3))
    if minimum:
        reserve = min(reserve, max(0, qscan_assets-minimum))
    qscan = scan_universe(problem, max_assets=min(qscan_assets, problem.n), candidate_reserve=reserve)
    if minimum:
        qscan["research_domain"] = {"minimum_positive_positions": minimum,
            "candidate_reserve": reserve, "selected_assets": len(qscan["selected_indices"]),
            "bits_per_asset": int(bits), "name_cap": float(problem.max_name_weight),
            "target_gross": float(problem.target_gross), "fallback": "no_nonexact_or_repaired_fallback"}
    selected = list(qscan["selected_indices"])
    qpo = generate_portfolio_candidates(
        problem, selected, backend=backend,
        bits_per_asset=bits, max_quantum_variables=max(6, min(24, bits * len(selected))),
        shots=1024, seed=seed, gross_penalty=80.0, matched_solver_comparison=True,
    )
    evaluated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for c in qpo["candidates"]:
        qrm = _replay_viability(problem, np.asarray(c["weights"], dtype=float), minimum)
        evaluated.append((c, qrm))

    full_search = None
    if bool(full_qrs_primary and problem.full_qrs_enabled and selected):
        exact_cap = max(4, min(int(full_qrs_exact_variable_cap), max(6, bits * len(selected))))

        rejection_counts: dict[str, int] = {}
        def viability_cb(w: np.ndarray):
            qrm = _replay_viability(problem, np.asarray(w, dtype=float), minimum)
            for gate, passed in qrm.get("structural_gates", {}).items():
                if not passed:
                    rejection_counts[gate] = rejection_counts.get(gate, 0)+1
            return bool(qrm.get("passes_qrm_structural_gates", False)), {
                "source": "QRM_STRUCTURAL_RESEARCH_GATE",
                "model_version": "QRM_STRUCTURAL_V1",
                "qrm": qrm,
                "margin_note": "Historical replay has no exact broker portfolio-margin engine; this is the configured m_v viability model only.",
            }

        objective = build_exact_full_qrs_diagonal_objective(
            problem, selected, bits_per_asset=bits, viability_callback=viability_cb,
            seed=seed, require_viability_gate=True,
        )
        qtable = objective.qrs_table()
        feasible = objective.feasible_table()
        objective_metadata = objective.metadata()
        # Invalid scores are numerical/model failures, not QRM-infeasible
        # sentinels. Check before accepting any finite winner or running an
        # optional simulator audit, including a mixed finite/invalid table.
        if int(objective_metadata.get("invalid_score_states", 0)) > 0:
            raise ValueError("Exact Full-QRS produced invalid scores for viable allocations; this is not a QRM rejection and cannot become policy cash")
        if not np.any(feasible):
            full_search = {
                "schema": FULL_QRS_SCHEMA,
                "method": "exact_reference_with_optional_diagonal_qaoa_audit",
                "status": "NO_FEASIBLE_ENCODED_STATE",
                "validation_reference": None,
                "simulator": None,
                "objective_metadata": objective_metadata,
                "viability_rejection_counts": dict(rejection_counts),
            }
        if np.any(feasible):
            best_global_index = int(np.nanargmax(np.where(feasible, qtable, -np.inf)))
            best_global_bits = [int((best_global_index >> i) & 1) for i in range(objective.num_variables)]
            best_global_weights = objective.full_weights(best_global_bits)
            exact_ref = {
                "global_best_basis_index": best_global_index,
                "global_best_bits": best_global_bits,
                "global_best_weights": best_global_weights.tolist(),
                "global_best_qrs": float(qtable[best_global_index]),
                "variables": int(objective.num_variables),
                "basis_states": int(2 ** objective.num_variables),
            }
            simulator_result = None
            if bool(simulator_audit):
                solver = ExactDiagonalQAOA(
                    max_variables=exact_cap, reps=max(1, int(full_qrs_qaoa_reps)),
                    optimizer_evaluations=max(12, int(full_qrs_qaoa_evaluations)),
                )
                simulator_result = full_qrs_exact_qaoa_search(
                    problem, solver, selected_indices=selected, bits_per_asset=bits,
                    shots=1024, seed=seed, viability_callback=viability_cb, require_viability_gate=True,
                )
                obs = simulator_result.get("best_observed") or {}
                obs_qrs = ((obs.get("full_qrs") or {}).get("qrs") if isinstance(obs.get("full_qrs"), dict) else None)
                exact_ref["simulator_audit"] = {
                    "ran": True,
                    "backend": obs.get("backend"),
                    "observed_qrs": obs_qrs,
                    "qrs_gap_to_exact": (None if obs_qrs is None else float(exact_ref["global_best_qrs"] - float(obs_qrs))),
                    "observed_global_best": bool((simulator_result.get("validation_reference") or {}).get("qaoa_observed_global_best", False)),
                }
            else:
                exact_ref["simulator_audit"] = {"ran": False}
            full_search = {
                "schema": FULL_QRS_SCHEMA,
                "method": "exact_reference_with_optional_diagonal_qaoa_audit",
                "status": "EXACT_REFERENCE_DECISION" if str(full_qrs_decision_mode) == "exact_reference" else "QAOA_OBSERVATION",
                "validation_reference": exact_ref,
                "simulator": simulator_result,
                "objective_metadata": objective_metadata,
                "viability_rejection_counts": dict(rejection_counts),
            }
            if str(full_qrs_decision_mode) == "exact_reference":
                w = np.asarray(best_global_weights, dtype=float)
                metrics = problem.metrics(w)
                candidate = {
                    "candidate_id": "QPO-FULL-QRS-EXACT-REFERENCE",
                    "risk_aversion": None, "weights": w, "raw_weights": w.copy(),
                    "repair_applied": False,
                    "repair": {"source": "literal_full_qrs_exact_finite_domain_reference", "applied": False},
                    "metrics": metrics,
                    "quantum": {
                        "backend": "exact_full_qrs_finite_domain_reference",
                        "energy": -float(exact_ref["global_best_qrs"]),
                        "qubo": {},
                        "metadata": {"search": "full_canonical_qrs_exact_reference", "simulator_audit": exact_ref.get("simulator_audit")},
                    },
                }
                exact_qrm = _replay_viability(problem, w, minimum)
                evaluated.append((candidate, exact_qrm))
                if exact_qrm.get("passes_qrm_structural_gates", False):
                    return w, {
                        "status": "VIABLE_EXACT_FULL_QRS_REFERENCE",
                        "winner_id": candidate["candidate_id"],
                        "qscan": qscan,
                        "candidate_count": len(evaluated),
                        "qrm_passes": 1,
                        "backend": "exact_full_qrs_finite_domain_reference",
                        "qrm": exact_qrm,
                        "full_qrs_optimization": full_search,
                        "selection_rule": "exact finite-domain Full-QRS/QPO reference; simulator is validation-only",
                    }
            elif simulator_result and simulator_result.get("best_observed") is not None:
                best = simulator_result["best_observed"]
                w = np.asarray(best["weights"], dtype=float)
                metrics = problem.metrics(w)
                candidate = {
                    "candidate_id": "QPO-FULL-QRS", "risk_aversion": None,
                    "weights": w, "raw_weights": w.copy(), "repair_applied": False,
                    "repair": {"source": "literal_full_qrs_exact_diagonal_qaoa", "applied": False},
                    "metrics": metrics,
                    "quantum": {
                        "backend": best.get("backend"), "energy": best.get("energy"), "qubo": best.get("qubo") or {},
                        "metadata": {"search": "full_canonical_qrs_exact_diagonal_qaoa", "full_qrs": best.get("full_qrs")},
                    },
                }
                evaluated.append((candidate, evaluate_risk(problem, w)))

        if minimum or rejection_policy == "cash_on_qrm_rejection":
            raise QPOResearchRejection(
                ("Broad" if minimum else "Concentrated") + " Pre-Gamma has no viable exact encoded portfolio; "
                f"minimum_positive_positions={minimum}; selected={len(selected)}; "
                f"gate_rejections={rejection_counts}. No repaired or quadratic fallback was used.",
                {"rejection_reason": "NO_FEASIBLE_ENCODED_STATE", "failed_gates": dict(rejection_counts),
                 "solver_diagnostics": full_search, "qscan": qscan}
            )

    viable = [(c, r) for c, r in evaluated if r.get("passes_qrm_structural_gates", False)]
    if not viable:
        return np.asarray(problem.current_weights, dtype=float), {
            "status": "NO_VIABLE_CANDIDATE", "qscan": qscan,
            "candidate_count": len(evaluated), "qpo": qpo, "qrm_passes": 0,
            "full_qrs_optimization": full_search,
        }
    viable.sort(key=lambda cr: _metric_rank_value(cr[0]["metrics"]), reverse=True)
    c, r = viable[0]
    return np.asarray(c["weights"], dtype=float), {
        "status": "VIABLE_CANDIDATE", "winner_id": c["candidate_id"], "qscan": qscan,
        "candidate_count": len(evaluated), "qrm_passes": len(viable),
        "matched_classical": c["quantum"].get("matched_classical"),
        "backend": c["quantum"].get("backend"),
        "energy_error": c["quantum"].get("energy_full_objective_error"),
        "repair_applied": c.get("repair_applied"), "qrm": r,
        "full_qrs_optimization": full_search,
        "selection_rule": "highest full canonical QRS among QRM-viable candidates when Full-QRS replay is enabled",
    }


def _choose_with_rejection_policy(problem: QOptimaPortfolioProblem, *, policy: str,
    context: dict[str, Any], **kwargs):
    try:
        target, metadata = _choose_qoptima(problem, rejection_policy=policy, **kwargs)
        return target, metadata, None
    except QPOResearchRejection as exc:
        event = {**context, **exc.research_diagnostics, "policy": policy,
            "proposed_target": None, "decision_known_weights": np.asarray(problem.current_weights).tolist(),
            "forced_cash_weights": np.zeros(problem.n).tolist(), "optimized_qrs_available": False}
        from .preset_backtest import json_safe
        event = json_safe(event)
        exc.research_diagnostics = {"failure_context": event}
        if policy != "cash_on_qrm_rejection":
            raise
        return np.zeros(problem.n), {"status": "POLICY_CASH_ON_QRM_REJECTION", "qrm_passes": 0,
            "selection_rule": "explicit_forced_cash_research_policy_no_optimizer_substitution",
            "rejection": event}, event

def _initialize_equal(symbols: list[str], max_name: float) -> np.ndarray:
    if not symbols:
        return np.array([], dtype=float)
    w = np.full(len(symbols), 1.0 / len(symbols), dtype=float)
    return _normalize_target(w, max_name)



def _paired_block_bootstrap(
    classical: pd.Series,
    qoptima: pd.Series,
    *,
    risk_free_rate: float,
    seed: int,
    replications: int = 300,
    block_sessions: int = 21,
) -> dict[str, Any]:
    common = classical.index.intersection(qoptima.index)
    c = classical.loc[common].to_numpy(dtype=float)
    q = qoptima.loc[common].to_numpy(dtype=float)
    n = len(common)
    if n < max(63, block_sessions * 2):
        return {"status": "INSUFFICIENT_OUT_OF_SAMPLE_SESSIONS", "sessions": n}
    rng = np.random.default_rng(int(seed))
    sharpe_diff: list[float] = []
    annual_diff: list[float] = []
    starts_max = max(1, n - block_sessions + 1)
    for _ in range(max(50, int(replications))):
        idx: list[int] = []
        while len(idx) < n:
            start = int(rng.integers(0, starts_max))
            idx.extend(range(start, min(n, start + block_sessions)))
        idx = idx[:n]
        cm = _metrics(pd.Series(c[idx]), risk_free_rate)
        qm = _metrics(pd.Series(q[idx]), risk_free_rate)
        if cm.get("sharpe") is not None and qm.get("sharpe") is not None:
            sharpe_diff.append(float(qm["sharpe"] - cm["sharpe"]))
        if cm.get("annualized_return") is not None and qm.get("annualized_return") is not None:
            annual_diff.append(float(qm["annualized_return"] - cm["annualized_return"]))
    def interval(values: list[float]) -> dict[str, Any] | None:
        if len(values) < 30:
            return None
        arr = np.asarray(values, dtype=float)
        lo, med, hi = np.quantile(arr, [0.025, 0.50, 0.975])
        return {"low_95": float(lo), "median": float(med), "high_95": float(hi), "replications": int(len(arr))}
    s_ci = interval(sharpe_diff)
    r_ci = interval(annual_diff)
    threshold = 0.10
    return {
        "status": "PAIRED_MOVING_BLOCK_BOOTSTRAP",
        "sessions": n,
        "block_sessions": int(block_sessions),
        "seed": int(seed),
        "sharpe_difference_qoptima_minus_classical": s_ci,
        "annualized_return_difference_qoptima_minus_classical": r_ci,
        "sharpe_difference_excludes_zero_95": bool(s_ci and (s_ci["low_95"] > 0 or s_ci["high_95"] < 0)),
        "economic_materiality_reference_abs_sharpe": threshold,
        "median_sharpe_difference_material_by_reference": bool(s_ci and abs(s_ci["median"]) >= threshold),
        "interpretation": "Paired descriptive uncertainty estimate; not an inferential claim about future performance.",
    }

@checkpoint_run_lock
def _walk_forward_method(
    returns: pd.DataFrame,
    *, method: str, params: dict[str, Any], alpha: float, lookback: int, rebalance: int,
    max_name: float, risk_aversion: float, turnover_penalty: float, risk_free_rate: float,
    transaction_cost_bps: float, correlation_blend: float, true_replay: bool,
    replay_qscan_assets: int, replay_bits: int, replay_backend: str, seed: int,
    forecast_records: list[dict[str, Any]] | None = None,
    universe_schedule: dict[str, dict[str, Any]] | None = None,
    component_mask: set[str] | None = None,
    full_qrs_primary: bool = False,
    full_qrs_beta: float = 0.10,
    full_qrs_bootstrap_reps: int = 128,
    full_qrs_quantile_confidence: float = 0.90,
    full_qrs_threshold_iterations: int = 4,
    full_qrs_active_set_iterations: int = 3,
    full_qrs_exact_variable_cap: int = 12,
    full_qrs_qaoa_reps: int = 2,
    full_qrs_qaoa_evaluations: int = 96,
    v1a_enabled: bool = False,
    v1a_artifact_schedule: list[dict[str, Any]] | None = None,
    v1a_es_budget: float | None = None,
    v1a_es_activation_max_ratio: float | None = None,
    v1a_risk_budget_annual: float = 0.15,
    v1a_risk_floor_ratio: float = 0.10,
    v1a_covariance_additive_floor: float = 1e-8,
    v1a_forecast_error_covariance_floor: float = 1e-8,
    v1a_scale_tolerance: float = 0.03,
    v1a_gate_b_block_sessions: int = 21,
    v1a_gate_b_bootstrap_replications: int = 2000,
    v1a_gate_b_alpha: float = 0.10,
    v1a_gate_b_effect_size: float = 0.0,
    start_from_cash: bool = False, target_gross_override: float | None = None,
    full_qrs_decision_mode: str = "qaoa_observed", simulator_audit_every_rebalances: int = 1,
    progress_callback: Callable[[dict[str, Any]], None] | None = None, progress_label: str = "QPO walk-forward",
    gamma_enabled: bool = False, gamma_min_error_records: int = 2, gamma_config: dict[str, Any] | None = None,
    forecast_error_history: list[dict[str, Any]] | None = None,
    gamma_decision_lag_sessions: int = 0,
    gamma_risk205_enabled: bool = False, gamma_risk205_config: dict[str, Any] | None = None,
    gamma_risk205_scope: str = "full_supervisor",
    gamma_risk_state: dict[str, Any] | None = None, gamma_beta_records: list[dict[str, Any]] | None = None,
    gamma_session_index=None,
    gamma_construction_policy: str = "legacy_exact",
    replay_min_positive_positions: int = 0,
    replay_rejection_policy: str = "abort",
    replay_checkpoint_directory: str | None = None,
    replay_checkpoint_resume: bool = True,
    replay_checkpoint_context: dict[str, Any] | None = None,
    cash_lending_rate: float | None = None, borrowing_rate: float | None = None,
    qpo_only: bool = False,
) -> dict[str, Any]:
    checkpoint_arguments = {key: value for key, value in locals().items()
        if key not in {"progress_callback", "progress_label", "replay_checkpoint_directory", "replay_checkpoint_resume"}}
    if progress_callback is not None:
        observer = progress_callback
        def progress_callback(payload):
            try:
                observer(payload)
            except Exception:
                pass  # An observer cannot change portfolio decisions.
    index = returns.index
    decision_lag = max(int(gamma_decision_lag_sessions), int(gamma_enabled or gamma_risk205_enabled))
    if decision_lag not in (0, 1):
        raise ValueError("gamma_decision_lag_sessions must be zero or one")
    if replay_rejection_policy not in {"abort", "cash_on_qrm_rejection"}:
        raise ValueError("Unknown replay_rejection_policy")
    if replay_rejection_policy != "abort" and not (gamma_enabled or gamma_risk205_enabled):
        raise ValueError("Forced-cash rejection policy is opt-in Gamma research only")
    from .gamma_construction import POLICIES
    if gamma_construction_policy != "qpo_budget_completion_reference" and (gamma_construction_policy not in POLICIES or (gamma_construction_policy != "legacy_exact" and not gamma_enabled)):
        raise ValueError("New construction policies are explicit Gamma-only research variants")
    risk_state = dict(gamma_risk_state or {})
    strategy_returns: list[pd.Series] = []
    canonical_qoptima_returns: list[pd.Series] = []
    classical_returns: list[pd.Series] = []
    base_returns: list[pd.Series] = []
    current_symbols: list[str] = []
    current_q = np.array([], dtype=float)
    current_q_canonical = np.array([], dtype=float)
    current_c = np.array([], dtype=float)
    current_b = np.array([], dtype=float)
    known_q = known_c = known_q_canonical = np.array([], dtype=float)
    rebalances: list[dict[str, Any]] = []
    skipped_universe: list[dict[str, Any]] = []
    missing_future_cells = 0
    legacy_cash_reset_count = 0
    legacy_qoptima_cash_reset_count = 0
    rejection_events: list[dict[str, Any]] = []
    forced_cash_sessions = 0
    anchors = list(range(lookback, len(returns), rebalance))
    total_anchors = max(1, len(anchors))
    checkpoint = None
    start_anchor_index = 0
    if replay_checkpoint_directory:
        from .replay_checkpoint import ReplayCheckpoint, artifact_identity, code_runtime_identity, content_digest
        checkpoint_arguments["artifact_file_sha256"] = artifact_identity(list(v1a_artifact_schedule or []))
        checkpoint_arguments["implementation"] = code_runtime_identity()
        slot = content_digest({"method": method, "alpha": alpha, "true_replay": true_replay,
                               "component_mask": component_mask})[:24]
        checkpoint = ReplayCheckpoint(replay_checkpoint_directory, slot=slot, inputs=checkpoint_arguments,
                                      resume=bool(replay_checkpoint_resume))
        restored = checkpoint.load(anchors=anchors)
        if restored is not None:
            from .replay_checkpoint import validate_replay_state
            saved = restored["state"]
            start_anchor_index = restored["next_anchor_index"]
            validate_replay_state(saved, returns=returns, anchors=anchors, cursor=start_anchor_index,
                                  rebalance=rebalance, paired=bool(v1a_enabled and not qpo_only), qpo_only=qpo_only)
            risk_state = saved["risk_state"]
            strategy_returns = saved["strategy_returns"]
            canonical_qoptima_returns = saved["canonical_qoptima_returns"]
            classical_returns = saved["classical_returns"]
            base_returns = saved["base_returns"]
            current_symbols = saved["current_symbols"]
            current_q, current_q_canonical = saved["current_q"], saved["current_q_canonical"]
            current_c, current_b = saved["current_c"], saved["current_b"]
            known_q, known_c, known_q_canonical = saved["known_q"], saved["known_c"], saved["known_q_canonical"]
            rebalances, skipped_universe = saved["rebalances"], saved["skipped_universe"]
            missing_future_cells = saved["missing_future_cells"]
            legacy_cash_reset_count = saved["legacy_cash_reset_count"]
            legacy_qoptima_cash_reset_count = saved["legacy_qoptima_cash_reset_count"]
            rejection_events, forced_cash_sessions = saved["rejection_events"], saved["forced_cash_sessions"]
            if progress_callback is not None:
                progress_callback({"stage": "replay_resumed", "message": f"Verified checkpoint: {start_anchor_index}/{total_anchors} rebalances committed",
                    "fraction": start_anchor_index/total_anchors, "rebalance": start_anchor_index,
                    "rebalance_total": total_anchors, "rebalance_status": "resumed",
                    "completed_rebalances": len(rebalances), "next_rebalance": start_anchor_index+1 if start_anchor_index < len(anchors) else None,
                    "checkpoint_status": checkpoint.status, "checkpoint_path": str(checkpoint.state_path),
                    "checkpoint_identity": checkpoint.identity})

    def commit_replay(cursor: int, *, complete: bool = False) -> None:
        if checkpoint is not None:
            checkpoint.save(next_anchor_index=cursor, anchors=anchors, complete=complete, state={
                "risk_state": risk_state, "strategy_returns": strategy_returns,
                "canonical_qoptima_returns": canonical_qoptima_returns, "classical_returns": classical_returns,
                "base_returns": base_returns, "current_symbols": current_symbols,
                "current_q": current_q, "current_q_canonical": current_q_canonical,
                "current_c": current_c, "current_b": current_b,
                "known_q": known_q, "known_c": known_c, "known_q_canonical": known_q_canonical,
                "rebalances": rebalances, "skipped_universe": skipped_universe,
                "missing_future_cells": missing_future_cells,
                "legacy_cash_reset_count": legacy_cash_reset_count,
                "legacy_qoptima_cash_reset_count": legacy_qoptima_cash_reset_count,
                "rejection_events": rejection_events, "forced_cash_sessions": forced_cash_sessions})

    for k in range(start_anchor_index, len(anchors)):
        anchor = anchors[k]
        legacy_cash_reset_events: list[dict[str, Any]] = []
        if progress_callback is not None:
            progress_callback({"stage":"walk_forward","message":f"{progress_label}: rebalance {k+1}/{total_anchors}","fraction":float(k/total_anchors),"rebalance":k+1,"rebalance_total":total_anchors})
        decision_date = index[anchor]
        information_cutoff = index[anchor - 1]
        history_end = anchor - decision_lag
        feature_cutoff = index[history_end - 1]
        hist_raw = returns.iloc[max(0, history_end-lookback):history_end]
        eligible = _eligible(hist_raw, max(63, int(0.70 * lookback)))
        schedule_row = None
        if universe_schedule is not None:
            membership_time = feature_cutoff if decision_lag else decision_date
            key = str(pd.Timestamp(membership_time).date())
            schedule_row = universe_schedule.get(key)
            if not schedule_row or schedule_row.get("status") != "eligible":
                skipped_universe.append({"decision_date": key, "reason": (schedule_row or {}).get("status", "missing_schedule")})
                commit_replay(k+1)
                continue
            allowed = set(schedule_row.get("symbols") or [])
            eligible = [symbol for symbol in eligible if symbol in allowed]
        if len(eligible) < 5:
            commit_replay(k+1)
            continue
        hist = hist_raw[eligible].dropna(how="any")
        if len(hist) < 63:
            commit_replay(k+1)
            continue
        if not current_symbols:
            current_symbols = eligible
            current_q = np.zeros(len(eligible), dtype=float) if start_from_cash else _initialize_equal(eligible, max_name)
            current_q_canonical = current_q.copy()
            current_c = current_q.copy()
            current_b = current_q.copy()
            known_q, known_c, known_q_canonical = current_q.copy(), current_c.copy(), current_q_canonical.copy()
        else:
            known_q = _carry_weights(current_symbols, known_q, eligible)
            known_c = _carry_weights(current_symbols, known_c, eligible)
            known_q_canonical = _carry_weights(current_symbols, known_q_canonical, eligible)
            current_q = _carry_weights(current_symbols, current_q, eligible)
            current_q_canonical = _carry_weights(current_symbols, current_q_canonical, eligible)
            current_c = _carry_weights(current_symbols, current_c, eligible)
            current_b = _carry_weights(current_symbols, current_b, eligible)
            current_symbols = eligible
            retain_cash = bool(gamma_enabled or gamma_risk205_enabled)
            if current_q.sum() <= 1e-12 and not retain_cash:
                before_reset = current_q.copy()
                current_q = _initialize_equal(eligible, max_name)
                legacy_cash_reset_events.append({"portfolio": "qoptima", "weights_before": before_reset.tolist(), "weights_after": current_q.tolist()})
                legacy_qoptima_cash_reset_count += 1
            if current_q_canonical.sum() <= 1e-12 and not retain_cash:
                before_reset = current_q_canonical.copy()
                current_q_canonical = _initialize_equal(eligible, max_name)
                legacy_cash_reset_events.append({"portfolio": "canonical_qoptima", "weights_before": before_reset.tolist(), "weights_after": current_q_canonical.tolist()})
            if current_c.sum() <= 1e-12 and not retain_cash:
                before_reset = current_c.copy()
                current_c = _initialize_equal(eligible, max_name)
                legacy_cash_reset_events.append({"portfolio": "classical", "weights_before": before_reset.tolist(), "weights_after": current_c.tolist()})
            if current_b.sum() <= 1e-12:
                current_b = _initialize_equal(eligible, max_name)
            legacy_cash_reset_count += len(legacy_cash_reset_events)

        selected_v1a_artifact = None
        v1a_artifact_meta = None
        if bool(v1a_enabled):
            from .v1a import select_causal_artifact
            selected_v1a_artifact, v1a_artifact_meta = select_causal_artifact(
                list(v1a_artifact_schedule or []), str(feature_cutoff)
            )
            if selected_v1a_artifact is None:
                if gamma_enabled or gamma_risk205_enabled:
                    raise ValueError(
                        f"Requested Gamma/Risk205 replay has no causal V1A artifact at {feature_cutoff}; "
                        "refusing to omit a requested evaluation period"
                    )
                skipped_universe.append({
                    "decision_date": str(pd.Timestamp(decision_date).date()),
                    "reason": "NO_CAUSAL_V1A_ARTIFACT",
                    "v1a_artifact_selection": v1a_artifact_meta,
                })
                commit_replay(k+1)
                continue

        problem_kwargs = dict(
            covariance_method=method, covariance_parameters=params, alpha=alpha,
            max_name_weight=max_name, risk_aversion=risk_aversion,
            turnover_penalty=turnover_penalty, risk_free_rate=risk_free_rate,
            lending_rate=cash_lending_rate, borrowing_rate=borrowing_rate,
            transaction_cost_bps=transaction_cost_bps, correlation_blend=correlation_blend,
            decision_time=information_cutoff, forecast_records=forecast_records,
            required_forecast_horizon_sessions=rebalance, component_mask=component_mask,
            full_qrs_enabled=bool(true_replay and full_qrs_primary),
            full_qrs_beta=float(full_qrs_beta),
            full_qrs_bootstrap_reps=int(full_qrs_bootstrap_reps),
            full_qrs_quantile_confidence=float(full_qrs_quantile_confidence),
            v1a_enabled=bool(v1a_enabled),
            v1a_artifact_path=selected_v1a_artifact,
            v1a_es_budget=v1a_es_budget,
            v1a_es_activation_max_ratio=v1a_es_activation_max_ratio,
            v1a_risk_budget_annual=float(v1a_risk_budget_annual),
            v1a_risk_floor_ratio=float(v1a_risk_floor_ratio),
            v1a_covariance_additive_floor=float(v1a_covariance_additive_floor),
            v1a_forecast_error_covariance_floor=float(v1a_forecast_error_covariance_floor),
            v1a_scale_tolerance=float(v1a_scale_tolerance),
            target_gross_override=target_gross_override,
            gamma_enabled=bool(gamma_enabled), gamma_feature_cutoff=feature_cutoff,
            gamma_min_error_records=int(gamma_min_error_records), forecast_error_history=forecast_error_history,
            gamma_score_basis=bool(decision_lag), gamma_config=gamma_config, gamma_session_index=gamma_session_index,
        )
        decision_q = known_q if decision_lag else current_q
        decision_c = known_c if decision_lag else current_c
        decision_qc = known_q_canonical if decision_lag else current_q_canonical
        q_problem, calibration = _make_problem(hist, eligible, decision_q, **problem_kwargs)
        c_problem_kwargs = dict(problem_kwargs)
        c_problem_kwargs.update({"v1a_enabled": False, "v1a_artifact_path": None})
        c_problem = None if qpo_only else _make_problem(hist, eligible, decision_c, **c_problem_kwargs)[0]
        canonical_q_problem = None
        if bool(v1a_enabled) and not qpo_only:
            canonical_q_problem = _make_problem(hist, eligible, decision_qc, **c_problem_kwargs)[0]
        c_target = np.zeros(len(eligible)) if qpo_only else np.asarray(solve_classical(c_problem)["weights"], dtype=float)
        canonical_q_target = None
        canonical_replay_meta = None
        q_rejection = canonical_rejection = None
        failure_context = {"decision_date": str(decision_date), "entry_close": str(information_cutoff),
            "feature_cutoff": str(feature_cutoff), "completed_rebalances": len(rebalances),
            "forecast_metadata": dict((q_problem.covariance_provenance or {}).get("gamma") or {})}
        risk_transform = None
        if gamma_construction_policy not in {"legacy_exact", "qpo_budget_completion_reference"} and gamma_risk205_enabled:
            from .gamma_integration import apply_gamma_risk205, select_gamma_beta_state
            from copy import deepcopy
            decision_risk_state = deepcopy(risk_state)
            # Validate/select the identical causal Beta snapshot once per date.
            # The selected record retains original provenance and is revalidated
            # by apply_gamma_risk205; candidate enumeration cannot change it.
            selected_beta = select_gamma_beta_state(gamma_beta_records, feature_cutoff)
            decision_beta_records = [] if selected_beta is None else [deepcopy(selected_beta)]
            def risk_transform(candidate):
                return apply_gamma_risk205(q_problem, candidate,
                    returns_history=hist.to_numpy(dtype=float).copy(), feature_cutoff=feature_cutoff,
                    beta_records=decision_beta_records, config_overrides=gamma_risk205_config,
                    state=deepcopy(decision_risk_state), actual_transaction_cost_bps=transaction_cost_bps,
                    scope=gamma_risk205_scope)
        if true_replay:
            if progress_callback is not None:
                progress_callback({"stage":"qpo_search", "message":("Evaluating classical QPO residual-budget Full-QRS reference" if gamma_construction_policy == "qpo_budget_completion_reference" else "Evaluating bounded classical joint Full-QRS reference" if gamma_construction_policy != "legacy_exact" else "Evaluating exact Gamma/QPO decision"), "fraction":float(k/total_anchors),
                                   "rebalance":k+1, "rebalance_total":total_anchors, "portfolio_path":"qoptima", "rebalance_status":"started"})
            q_target, replay_meta, q_rejection = _choose_with_rejection_policy(
                q_problem,
                policy=replay_rejection_policy, context={**failure_context, "portfolio_path": "qoptima"},
                qscan_assets=min(replay_qscan_assets, len(eligible)), bits=replay_bits,
                backend=replay_backend, seed=seed + k,
                full_qrs_primary=bool(full_qrs_primary),
                full_qrs_threshold_iterations=int(full_qrs_threshold_iterations),
                full_qrs_active_set_iterations=int(full_qrs_active_set_iterations),
                full_qrs_exact_variable_cap=int(full_qrs_exact_variable_cap),
                full_qrs_qaoa_reps=int(full_qrs_qaoa_reps),
                full_qrs_qaoa_evaluations=int(full_qrs_qaoa_evaluations),
                full_qrs_decision_mode=str(full_qrs_decision_mode),
                simulator_audit=(int(simulator_audit_every_rebalances) > 0 and k % int(simulator_audit_every_rebalances) == 0),
                minimum_positive_positions=int(replay_min_positive_positions),
                construction_policy=gamma_construction_policy, risk_transform=risk_transform,
            )
            if progress_callback is not None:
                progress_callback({"stage":"qpo_search", "message":("QPO residual-budget reference decision returned" if gamma_construction_policy == "qpo_budget_completion_reference" else "Classical joint reference decision returned" if gamma_construction_policy != "legacy_exact" else "Exact QPO decision returned"), "fraction":float(k/total_anchors),
                                   "rebalance":k+1, "rebalance_total":total_anchors, "portfolio_path":"qoptima", "rebalance_status":"returned"})
            if canonical_q_problem is not None:
                if progress_callback is not None:
                    progress_callback({"stage":"qpo_search", "message":"Evaluating paired canonical QPO decision", "fraction":float(k/total_anchors),
                                       "rebalance":k+1, "rebalance_total":total_anchors, "portfolio_path":"canonical_qoptima", "rebalance_status":"started"})
                canonical_q_target, canonical_replay_meta, canonical_rejection = _choose_with_rejection_policy(
                    canonical_q_problem,
                    policy=replay_rejection_policy, context={**failure_context, "portfolio_path": "canonical_qoptima"},
                    qscan_assets=min(replay_qscan_assets, len(eligible)), bits=replay_bits,
                    backend=replay_backend, seed=seed + k,
                    full_qrs_primary=bool(full_qrs_primary),
                    full_qrs_threshold_iterations=int(full_qrs_threshold_iterations),
                    full_qrs_active_set_iterations=int(full_qrs_active_set_iterations),
                    full_qrs_exact_variable_cap=int(full_qrs_exact_variable_cap),
                    full_qrs_qaoa_reps=int(full_qrs_qaoa_reps),
                    full_qrs_qaoa_evaluations=int(full_qrs_qaoa_evaluations),
                    full_qrs_decision_mode=(str(full_qrs_decision_mode) if replay_min_positive_positions or replay_rejection_policy != "abort" else "qaoa_observed"),
                    simulator_audit=((int(simulator_audit_every_rebalances) > 0 and k % int(simulator_audit_every_rebalances) == 0)
                        if replay_min_positive_positions or replay_rejection_policy != "abort" else True),
                    minimum_positive_positions=int(replay_min_positive_positions),
                    construction_policy=gamma_construction_policy,
                )
                if progress_callback is not None:
                    progress_callback({"stage":"qpo_search", "message":"Paired canonical QPO decision returned", "fraction":float(k/total_anchors),
                                       "rebalance":k+1, "rebalance_total":total_anchors, "portfolio_path":"canonical_qoptima", "rebalance_status":"returned"})
        else:
            q_target = np.asarray(solve_classical(q_problem)["weights"], dtype=float)
            replay_meta = {"status": "CLASSICAL_RESILIENCE_ONLY", "backend": "classical"}
            if canonical_q_problem is not None:
                canonical_q_target = np.asarray(solve_classical(canonical_q_problem)["weights"], dtype=float)
                canonical_replay_meta = {"status": "CLASSICAL_CANONICAL_ONLY", "backend": "classical"}
        risk205_audit = replay_meta.get("joint_risk205_audit")
        if risk205_audit is not None and q_rejection is None:
            risk_state = dict(replay_meta["joint_risk_state"])
        if gamma_risk205_enabled and q_rejection is None and gamma_construction_policy in {"legacy_exact", "qpo_budget_completion_reference"}:
            from .gamma_integration import apply_gamma_risk205, GammaQRMRejection
            try:
                q_target, risk205_audit, risk_state = apply_gamma_risk205(
                    q_problem, q_target, returns_history=hist.to_numpy(dtype=float),
                    feature_cutoff=feature_cutoff, beta_records=gamma_beta_records,
                    config_overrides=gamma_risk205_config, state=risk_state,
                    actual_transaction_cost_bps=transaction_cost_bps, scope=gamma_risk205_scope,
                )
            except GammaQRMRejection as exc:
                event = {**failure_context, "portfolio_path": "qoptima", "rejection_reason": exc.stage,
                    "failed_gates": {name: passed for name, passed in exc.qrm.get("structural_gates", {}).items() if not passed},
                    "qrm": exc.qrm, "policy": replay_rejection_policy, "proposed_target": q_target.tolist(),
                    "decision_known_weights": np.asarray(q_problem.current_weights).tolist(),
                    "forced_cash_weights": np.zeros(q_problem.n).tolist(), "optimized_qrs_available": False}
                from .preset_backtest import json_safe
                event = json_safe(event)
                exc.research_diagnostics = {"failure_context": event}
                if replay_rejection_policy != "cash_on_qrm_rejection":
                    raise
                q_target = np.zeros(q_problem.n)
                q_rejection = event
                replay_meta = {"status": "POLICY_CASH_ON_QRM_REJECTION", "qrm_passes": 0,
                    "selection_rule": "explicit_forced_cash_after_risk_qrm_rejection", "rejection": event}
        if q_rejection is not None:
            rejection_events.append(q_rejection)
            if gamma_risk205_enabled:
                before_state = dict(risk_state)
                risk_state = {"predictive_persistence_count": 0, "beta_persistence_count": 0, "v1a_persistence_count": 0}
                risk205_audit = {"enabled": True, "scope": gamma_risk205_scope, "supervisor_applied": False,
                    "policy": "explicit_forced_cash_after_qrm_rejection", "state_before": before_state,
                    "state_after": dict(risk_state), "persistence_policy": "reset_after_rejected_risky_decision",
                    "approved_final_gross": 0.0, "actual_turnover_l1": float(np.abs(decision_q).sum()),
                    "actual_one_event_transaction_cost": float(np.abs(decision_q).sum())*transaction_cost_bps/10000,
                    "final_qrm": {"passes_qrm_structural_gates": False, "policy": "separate_cash_policy_not_a_qrm_approved_risky_allocation"},
                    "rejection": q_rejection}
        if canonical_rejection is not None:
            rejection_events.append(canonical_rejection)
        b_target = np.zeros(len(eligible)) if qpo_only else _initialize_equal(eligible, max_name)

        next_end = min(len(returns), anchor + rebalance)
        window = returns.iloc[anchor:next_end][eligible]
        if window.empty:
            break
        if gamma_enabled or gamma_risk205_enabled:
            missing = ~np.isfinite(window.to_numpy(dtype=float))
            if missing.any():
                first_row, first_col = np.argwhere(missing)[0]
                raise ValueError(f"Requested Gamma/Risk205 replay has missing realized-return cells at {window.index[first_row]} for {window.columns[first_col]}; no held-flat substitution, retroactive universe selection, or automatic date truncation")
        if q_rejection is not None:
            forced_cash_sessions += len(window)
        tc_rate = float(transaction_cost_bps) / 10000.0
        c_turn = float(np.abs(c_target - current_c).sum())
        q_turn = float(np.abs(q_target - current_q).sum())
        qc_turn = float(np.abs(canonical_q_target - current_q_canonical).sum()) if canonical_q_target is not None else 0.0
        b_turn = float(np.abs(b_target - current_b).sum())
        c_series, c_drift, miss_c = _run_holding_window(c_target, window, risk_free_rate=risk_free_rate, lending_rate=cash_lending_rate, borrowing_rate=borrowing_rate, transaction_cost=tc_rate*c_turn)
        q_series, q_drift, miss_q = _run_holding_window(q_target, window, risk_free_rate=risk_free_rate, lending_rate=cash_lending_rate, borrowing_rate=borrowing_rate, transaction_cost=tc_rate*q_turn)
        if canonical_q_target is not None:
            qc_series, qc_drift, miss_qc = _run_holding_window(canonical_q_target, window, risk_free_rate=risk_free_rate, lending_rate=cash_lending_rate, borrowing_rate=borrowing_rate, transaction_cost=tc_rate*qc_turn)
            canonical_qoptima_returns.append(qc_series)
        else:
            qc_drift, miss_qc = current_q_canonical, 0
        b_series, b_drift, miss_b = _run_holding_window(b_target, window, risk_free_rate=risk_free_rate, lending_rate=cash_lending_rate, borrowing_rate=borrowing_rate, transaction_cost=tc_rate*b_turn)
        missing_future_cells += max(miss_c, miss_q, miss_qc, miss_b)
        if not qpo_only:
            classical_returns.append(c_series)
            base_returns.append(b_series)
        strategy_returns.append(q_series)
        from dataclasses import replace
        qm = replace(q_problem, current_weights=current_q).metrics(q_target)
        if q_rejection is not None:
            qm = {**qm, "qrs": None, "cost_adjusted_qrs": None,
                "optimized_qrs_available": False, "decision_source": "forced_cash_research_policy"}
        if risk205_audit is not None:
            risk205_audit["estimated_one_event_transaction_cost"] = risk205_audit.pop("actual_one_event_transaction_cost")
            risk205_audit["estimated_turnover_l1"] = risk205_audit.pop("actual_turnover_l1")
            risk205_audit["actual_one_event_transaction_cost"] = tc_rate*q_turn
            risk205_audit["actual_turnover_l1"] = q_turn
            risk205_audit["actual_fill_post_risk_metrics"] = qm
            risk205_audit["fill_cost_policy"] = "ex_post_entry_holdings_only_for_cost_and_rescore_not_candidate_search"
        known_window = window.iloc[:-decision_lag] if decision_lag else window
        known_q = _run_holding_window(q_target, known_window, risk_free_rate=risk_free_rate, lending_rate=cash_lending_rate, borrowing_rate=borrowing_rate, transaction_cost=0.0)[1]
        known_c = _run_holding_window(c_target, known_window, risk_free_rate=risk_free_rate, lending_rate=cash_lending_rate, borrowing_rate=borrowing_rate, transaction_cost=0.0)[1]
        known_q_canonical = (_run_holding_window(canonical_q_target, known_window, risk_free_rate=risk_free_rate, lending_rate=cash_lending_rate, borrowing_rate=borrowing_rate, transaction_cost=0.0)[1]
                             if canonical_q_target is not None else current_q_canonical.copy())
        forecast_meta = dict((q_problem.covariance_provenance or {}).get("forecast_contract") or {})
        rebalances.append({
            "decision_date": str(decision_date), "information_cutoff": str(information_cutoff),
            "feature_cutoff": str(feature_cutoff), "decision_lag_sessions": decision_lag,
            "gamma_risk205": risk205_audit,
            "decision_known_weights": decision_q.tolist(), "actual_entry_weights": current_q.tolist(),
            "legacy_cash_reset_events": legacy_cash_reset_events,
            "rejection_policy_events": [item for item in (q_rejection, canonical_rejection) if item is not None],
            "symbols": eligible, "method": method, "alpha": float(alpha),
            "covariance_provenance": q_problem.covariance_provenance,
            "resilience_calibration": calibration,
            "forecast_contract": forecast_meta,
            "universe_snapshot": schedule_row,
            "universe_membership_cutoff": str(feature_cutoff if decision_lag else decision_date),
            "base_turnover": b_turn, "classical_turnover": c_turn, "qoptima_turnover": q_turn,
            "qoptima_qrs": qm.get("qrs"), "qoptima_cost_adjusted_qrs": qm.get("cost_adjusted_qrs"),
            "fragility_premium": qm.get("fragility_premium"), "fragility_band": qm.get("fragility_band"),
            "replay": replay_meta,
            "canonical_qoptima_replay": canonical_replay_meta,
            "v1a_artifact_selection": v1a_artifact_meta,
            "canonical_qoptima_turnover": qc_turn if canonical_q_target is not None else None,
            "qoptima_target_weights": [
                {"symbol": str(sym), "weight": float(wt)}
                for sym, wt in zip(eligible, q_target) if float(wt) > 1e-12
            ],
            "canonical_qoptima_target_weights": ([
                {"symbol": str(sym), "weight": float(wt)}
                for sym, wt in zip(eligible, canonical_q_target) if float(wt) > 1e-12
            ] if canonical_q_target is not None else None),
        })
        current_c, current_q, current_q_canonical, current_b = c_drift, q_drift, qc_drift, b_drift
        # Commit the paired paths, costs, drift and state counters atomically.
        # A lost process before this point repeats this same anchor/seed.
        commit_replay(k+1)

        if progress_callback is not None:
            progress_callback({"stage":"walk_forward", "message":f"{progress_label}: rebalance {k+1}/{total_anchors} scored",
                               "fraction":float((k+1)/total_anchors), "rebalance":k+1, "rebalance_total":total_anchors, "rebalance_status":"scored",
                               **({"completed_rebalances": len(rebalances), "next_rebalance": k+2 if k+1 < len(anchors) else None,
                                   "checkpoint_status": checkpoint.status, "checkpoint_path": str(checkpoint.state_path),
                                   "checkpoint_identity": checkpoint.identity} if checkpoint is not None else {})})

    if progress_callback is not None:
        progress_callback({"stage":"walk_forward","message":f"{progress_label}: scoring completed path","fraction":1.0,"rebalance_total":total_anchors})
    c_ret = pd.concat(classical_returns).sort_index() if classical_returns else pd.Series(dtype=float)
    q_ret = pd.concat(strategy_returns).sort_index() if strategy_returns else pd.Series(dtype=float)
    qc_ret = pd.concat(canonical_qoptima_returns).sort_index() if canonical_qoptima_returns else pd.Series(dtype=float)
    b_ret = pd.concat(base_returns).sort_index() if base_returns else pd.Series(dtype=float)
    c_ret = c_ret[~c_ret.index.duplicated(keep="first")]
    q_ret = q_ret[~q_ret.index.duplicated(keep="first")]
    qc_ret = qc_ret[~qc_ret.index.duplicated(keep="first")]
    b_ret = b_ret[~b_ret.index.duplicated(keep="first")]
    requested_sessions = int(len(index[lookback:]))
    if gamma_enabled or gamma_risk205_enabled:
        if len(q_ret) != requested_sessions:
            raise ValueError(
                f"Requested Gamma/Risk205 replay scored {len(q_ret)} of {requested_sessions} sessions; "
                "partial coverage cannot be reported as a complete comparison"
            )
        if missing_future_cells:
            raise ValueError(
                f"Requested Gamma/Risk205 replay has {missing_future_cells} missing realized-return cells; "
                "held-flat substitution cannot be reported as a complete comparison"
            )
    cq = [r["qoptima_qrs"] for r in rebalances if r.get("qoptima_qrs") is not None]
    fp = [r["fragility_premium"] for r in rebalances if r.get("fragility_premium") is not None]
    forecast_used = sum(1 for r in rebalances if (r.get("forecast_contract") or {}).get("available"))
    gate_b = None
    if bool(v1a_enabled) and not qc_ret.empty and not q_ret.empty:
        from .v1a import paired_block_bootstrap_gate
        common_idx = q_ret.index.intersection(qc_ret.index)
        gate_b = paired_block_bootstrap_gate(
            q_ret.loc[common_idx].to_numpy(dtype=float),
            qc_ret.loc[common_idx].to_numpy(dtype=float),
            block_length=int(v1a_gate_b_block_sessions),
            replications=int(v1a_gate_b_bootstrap_replications),
            alpha=float(v1a_gate_b_alpha),
            effect_size=float(v1a_gate_b_effect_size),
            seed=int(seed) + 9100,
        )
    daily_rows = lambda series: [{"date": str(pd.Timestamp(day).date()), "net_return": float(value)} for day, value in pd.Series(series, dtype=float).dropna().items()]
    result = {
        "research_variant": {"gamma_enabled": bool(gamma_enabled), "risk205_enabled": bool(gamma_risk205_enabled),
            "qpo_only": bool(qpo_only), "cash_lending_rate": cash_lending_rate, "borrowing_rate": borrowing_rate, "metric_risk_free_rate": risk_free_rate,
            "rejection_policy": replay_rejection_policy,
            "decision_lag_sessions": decision_lag, "construction_policy": gamma_construction_policy, "risk_state_final": risk_state, "risk205_scope": gamma_risk205_scope,
            "score_units": "linear_annual_equivalent_not_compound_return" if decision_lag else "legacy_annualized"},
        "legacy_cash_reset_audit": {"all_path_events": legacy_cash_reset_count,
            "qoptima_path_events": legacy_qoptima_cash_reset_count,
            "behavior_preserved_for_legacy_parity": not bool(gamma_enabled or gamma_risk205_enabled),
            "interpretation": "A positive QPO count means actual entry holdings were reinitialized after cash before turnover accounting; inspect the matched rebalance events. Zero means the branch was not exercised in this replay."},
        "replay_min_positive_positions": int(replay_min_positive_positions),
        "rejection_policy_audit": {"policy": replay_rejection_policy, "events": rejection_events,
            "qoptima_rejected_decisions": sum(item["portfolio_path"] == "qoptima" for item in rejection_events),
            "canonical_rejected_decisions": sum(item["portfolio_path"] == "canonical_qoptima" for item in rejection_events),
            "forced_cash_sessions": forced_cash_sessions,
            "forced_cash_session_fraction": forced_cash_sessions/max(1, requested_sessions),
            "interpretation": "Explicit policy economics including actual costs; rejected allocations have no optimized QRS score."},
        "evaluation_coverage": {"requested_sessions": requested_sessions, "scored_sessions": int(len(q_ret)),
            "complete": bool(len(q_ret) == requested_sessions), "skipped_rebalance_count": len(skipped_universe)},
        "base_equal_weight": _metrics(b_ret, risk_free_rate),
        "classical_resilience": {**_metrics(c_ret, risk_free_rate), "mean_qrs_at_rebalance": None},
        "qoptima_replay": {**_metrics(q_ret, risk_free_rate), "mean_qrs_at_rebalance": float(np.mean(cq)) if cq else None, "mean_fragility_premium": float(np.mean(fp)) if fp else None},
        "canonical_qoptima_replay": (_metrics(qc_ret, risk_free_rate) if not qc_ret.empty else None),
        "v1a_gate_b": gate_b,
        "comparison_uncertainty": _paired_block_bootstrap(c_ret, q_ret, risk_free_rate=risk_free_rate, seed=seed),
        "rebalances": rebalances,
        "skipped_universe_dates": skipped_universe,
        "forecast_rebalances_using_pit_models": int(forecast_used),
        "missing_future_return_cells_held_flat": int(missing_future_cells),
        "replay_backend": replay_backend if true_replay else "classical",
        "full_qrs_primary_replay": bool(true_replay and full_qrs_primary),
        "historical_qpu_execution_claimed": False,
        "daily_returns": {
            "qoptima": daily_rows(q_ret),
            "canonical_qoptima": daily_rows(qc_ret),
            "classical": daily_rows(c_ret),
            "equal_weight": daily_rows(b_ret),
        },
    }
    commit_replay(len(anchors), complete=True)
    if checkpoint is not None:
        result["replay_checkpoint"] = checkpoint.audit(completed_anchors=len(anchors))
    return result


def _session_day(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("Evaluation bound cannot be NaT")
    return stamp.tz_localize(None).normalize() if stamp.tzinfo is not None else stamp.normalize()


def _limit_evaluation_prices(prices: pd.DataFrame, evaluation_end: Any | None) -> pd.DataFrame:
    """Daily-session inclusive end, including cache bars stamped 04:00/05:00."""
    if evaluation_end is None:
        return prices
    result = prices.loc[prices.index.tz_localize(None).normalize() <= _session_day(evaluation_end)]
    if result.empty:
        raise ValueError("No prices at or before evaluation_end")
    return result


def run_qoptima_backtest_overlay(user_id: int, spec: dict[str, Any], *, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    def emit(stage: str, message: str, percent: float, **details) -> None:
        if progress_callback is not None:
            try:
                progress_callback({"stage":stage,"message":message,"percent":float(max(0.0,min(100.0,percent))), **details})
            except Exception:
                pass  # Progress delivery is never part of the trading policy.

    emit("preflight", "Preparing QPO preset backtest", 3.0)
    universe: list[str] = []
    for raw in spec.get("universe") or []:
        symbol = str(raw or "").upper().strip()
        if symbol and symbol not in universe:
            universe.append(symbol)
    snapshots = list(spec.get("universe_snapshots") or [])
    if snapshots and bool(spec.get("gamma_enabled", False)):
        raise ValueError("Gamma dynamic universe_snapshots are unsupported until membership masks enter feature, reference and calibration construction; no QPO-only PIT claim")
    snapshot_symbols: list[str] = []
    for snapshot in snapshots:
        for raw in (snapshot or {}).get("symbols") or []:
            symbol = str(raw or "").upper().strip()
            if symbol and symbol not in snapshot_symbols:
                snapshot_symbols.append(symbol)
    fetch_universe = list(dict.fromkeys(universe + snapshot_symbols))
    if len(fetch_universe) < 5:
        raise ValueError("Use at least five symbols or provide point-in-time universe snapshots with at least five symbols")
    if len(fetch_universe) > 100:
        raise ValueError("QOptima walk-forward currently accepts at most 100 distinct historical symbols across requested/snapshot universes")

    period = str(spec.get("evaluation_period") or "1y").lower()
    sessions = _PERIOD_SESSIONS.get(period, 252)
    lookback = max(84, min(504, int(spec.get("lookback_sessions") or 126)))
    rebalance = max(5, min(126, int(spec.get("rebalance_sessions") or 21)))
    holdout_sessions = max(0, int(spec.get("holdout_sessions") or 0))
    v1a_initial_train_sessions = max(0, int(spec.get("v1a_initial_train_sessions") or 0)) if bool(spec.get("v1a_auto_train", False)) else 0
    minimum_history = sessions + lookback + holdout_sessions + v1a_initial_train_sessions + 10
    def market_progress(payload: dict[str, Any]) -> None:
        frac=float(payload.get("fraction") or 0.0)
        emit(str(payload.get("stage") or "market_data"), str(payload.get("message") or "Loading market history"), 4.0 + 14.0*max(0.0,min(1.0,frac)))
    prices, market_provenance = _fetch_price_panel(
        user_id, fetch_universe, str(spec.get("provider") or "cache_only"),
        _FETCH_PERIOD.get(period, "all"), minimum_history,
        require_adjusted=bool(spec.get("require_adjusted_prices", True)),
        progress_callback=market_progress,
    )
    emit("market_data", f"Market panel ready: {len(prices.columns)} symbols", 18.0)
    evaluation_start = spec.get("evaluation_start")
    evaluation_end = spec.get("evaluation_end")
    if evaluation_start is not None and evaluation_end is not None and _session_day(evaluation_start) > _session_day(evaluation_end):
        raise ValueError("evaluation_start must not be after evaluation_end")
    prices = _limit_evaluation_prices(prices, evaluation_end)
    if bool(spec.get("gamma_enabled", False) or spec.get("gamma_risk205_enabled", False)):
        unavailable = [symbol for symbol in fetch_universe if symbol not in prices.columns]
        if unavailable:
            raise ValueError(f"Requested Gamma/Risk205 universe has unavailable symbols: {unavailable}; no silent universe reduction")
    full_returns = prices.pct_change(fill_method=None)
    eval_end = len(full_returns) - holdout_sessions if holdout_sessions else len(full_returns)
    eval_start = max(0, eval_end - (sessions + lookback))
    if evaluation_start is not None:
        first_score = int(full_returns.index.tz_localize(None).normalize().searchsorted(_session_day(evaluation_start), side="left"))
        if first_score >= eval_end:
            raise ValueError("evaluation_start has no sessions in the evaluation window")
        if first_score < lookback:
            raise ValueError("Not enough warmup before evaluation_start")
        eval_start = first_score - lookback
    market_provenance["requested_evaluation_start"] = evaluation_start
    market_provenance["requested_evaluation_end"] = evaluation_end
    returns = full_returns.iloc[eval_start:eval_end].copy()
    if len(returns) <= lookback + 20:
        raise ValueError("Not enough history for requested walk-forward study after reserved holdout")
    market_provenance["reserved_holdout_sessions"] = int(holdout_sessions)
    market_provenance["reserved_holdout_start"] = (str(full_returns.index[eval_end]) if holdout_sessions and eval_end < len(full_returns) else None)

    universe_schedule_payload = None
    universe_schedule = None
    if snapshots:
        lagged_membership = bool(spec.get("gamma_enabled", False) or spec.get("gamma_risk205_enabled", False) or int(spec.get("gamma_decision_lag_sessions", 0)))
        decision_dates = list(returns.index) if lagged_membership else [returns.index[i] for i in range(lookback, len(returns), rebalance)]
        universe_schedule_payload = materialize_universe_schedule(
            snapshots,
            decision_dates,
            max_staleness_days=int(spec.get("universe_max_staleness_days") or 31),
        )
        universe_schedule = {row["decision_date"]: row for row in universe_schedule_payload["schedule"]}
        market_provenance["point_in_time_universe_schedule"] = universe_schedule_payload
        market_provenance["survivorship_warning"] = (
            "Point-in-time snapshot membership was enforced on supplied snapshot dates. "
            "Delisted return coverage still depends on the market-data source."
        )

    requested_methods = [str(x).strip().lower() for x in (spec.get("covariance_methods") or list(COVARIANCE_METHODS))]
    methods = [m for m in COVARIANCE_METHODS if m in requested_methods]
    if bool(spec.get("run_covariance_benchmarks", True)) and "sample" not in methods:
        methods.insert(0, "sample")
    if bool(spec.get("run_alpha_frontier", True)):
        alpha_values = sorted({float(np.clip(a, 0.0, 1.0)) for a in (spec.get("alpha_values") or [0.0, 0.5, 1.0])})
    else:
        alpha_values = []
    selected_method = str(spec.get("selected_covariance_method") or "shrinkage").lower()
    if selected_method not in COVARIANCE_METHODS:
        raise ValueError(f"selected_covariance_method must be one of {COVARIANCE_METHODS}")
    if selected_method not in methods:
        methods.append(selected_method)
    selected_params = dict(spec.get("covariance_parameters") or {})
    params_by_method = {str(k).lower(): dict(v or {}) for k, v in dict(spec.get("covariance_parameters_by_method") or {}).items()}
    params_by_method.setdefault(selected_method, selected_params)
    forecast_records = list(spec.get("forecast_records") or [])
    gamma_diagnostics = None
    if bool(spec.get("gamma_enabled", False)):
        from .gamma_forecast import GammaForecastConfig, build_gamma_forecast_records
        from dataclasses import fields
        supplied = dict(spec.get("gamma_config") or {})
        unknown = set(supplied) - {f.name for f in fields(GammaForecastConfig)}
        if unknown:
            raise ValueError(f"Unknown Gamma configuration fields: {sorted(unknown)}")
        supplied.setdefault("horizon_sessions", rebalance)
        if int(supplied["horizon_sessions"]) != rebalance:
            raise ValueError("Gamma horizon must match replay rebalance horizon")
        if not forecast_records:
            evaluation_origins = [returns.index[i-1] for i in range(lookback, len(returns), rebalance)]
            first_origin = int(prices.index.get_loc(evaluation_origins[0]))
            historical_origins = list(prices.index[first_origin % rebalance:first_origin:rebalance])
            emit("gamma_forecast", "Generating causal Gamma forecast/error ledger", 18.5)
            generated = build_gamma_forecast_records(
                prices.iloc[:eval_end], config=GammaForecastConfig(**supplied),
                decision_dates=historical_origins + evaluation_origins,
                beta_records=spec.get("gamma_beta_records"),
                sec_cache_directory=spec.get("gamma_sec_cache_directory"),
                sec_histories=spec.get("gamma_sec_histories"),
            )
            # Publish the exact global diagnostic pool before json_safe/deepcopy
            # can multiply it across reports. This does not modify any forecast.
            from .lineage_sidecar import externalize_sec_lineage
            forecast_records = generated["records"]
            gamma_diagnostics = externalize_sec_lineage(generated["diagnostics"])
            del generated
            emit("gamma_forecast_ready", "Gamma forecast generation returned", 19.5, records_emitted=len(forecast_records))
        else:
            gamma_diagnostics = {"source": "caller_supplied_strict_gamma_ledger"}
        if not forecast_records:
            from .preset_backtest import json_safe
            exc = ValueError("Gamma has no eligible forecasts; historical proxy fallback is forbidden")
            exc.research_diagnostics = json_safe({
                "market_data_provenance": {**market_provenance, "gamma": gamma_diagnostics},
                "research_variant": {"gamma_enabled": True,
                    "risk205_enabled": bool(spec.get("gamma_risk205_enabled", False)),
                    "risk205_scope": spec.get("gamma_risk205_scope", "full_supervisor"),
                    "rejection_policy": spec.get("replay_rejection_policy", "abort")},
                "failure_context": {"stage": "forecast_preparation", "rejection_reason": "NO_ELIGIBLE_GAMMA_FORECAST"},
                "completed_rebalances": 0})
            raise exc
    market_provenance["gamma"] = gamma_diagnostics
    seed = int(spec.get("seed") or 41)

    if bool(spec.get("v1a_enabled", False)) and bool(spec.get("v1a_auto_train", False)):
        from .preset_backtest import build_causal_v1a_artifact_schedule
        auto_dir = spec.get("v1a_artifact_directory")
        research_lag = max(int(spec.get("gamma_decision_lag_sessions", 0)),
            int(bool(spec.get("gamma_enabled", False) or spec.get("gamma_risk205_enabled", False))))
        auto_schedule, auto_training_meta = build_causal_v1a_artifact_schedule(
            full_returns.iloc[:eval_end],
            # The scheduler trains through the session BEFORE this boundary.
            # Lagged research inference requires artifact_cutoff < feature_cutoff,
            # so equality with the first feature cutoff is insufficient.
            # Keep the original unlagged QPO schedule unchanged for parity.
            evaluation_start=returns.index[min(len(returns)-1, lookback-research_lag-int(research_lag > 0))],
            evaluation_end=returns.index[-1],
            output_directory=auto_dir,
            horizon_sessions=rebalance,
            initial_train_sessions=max(756, int(spec.get("v1a_initial_train_sessions") or 1512)),
            refit_sessions=max(rebalance, int(spec.get("v1a_refit_sessions") or 252)),
            seed=seed,
            max_epochs=max(50, int(spec.get("v1a_training_max_epochs") or 500)),
            patience=max(10, int(spec.get("v1a_training_patience") or 50)),
            progress_callback=(lambda payload: emit(str(payload.get("stage") or "v1a_training"), str(payload.get("message") or "Training V1A"), 20.0 + 20.0*max(0.0,min(1.0,float(payload.get("percent") or 0.0)/100.0)))),
        )
        emit("v1a_training", f"V1A causal artifact schedule ready: {len(auto_schedule)} artifacts", 40.0)
        spec = dict(spec)
        spec["v1a_artifact_schedule"] = auto_schedule
        market_provenance["v1a_auto_training"] = auto_training_meta

    if not (bool(spec.get("v1a_enabled", False)) and bool(spec.get("v1a_auto_train", False))):
        emit("v1a_training", "V1A auto-training not required for this preset", 40.0)

    common = dict(
        lookback=lookback, rebalance=rebalance,
        max_name=float(spec.get("max_name_weight") or 0.20),
        risk_aversion=float(spec.get("risk_aversion") or 3.0),
        turnover_penalty=float(spec.get("turnover_penalty") or 0.20),
        risk_free_rate=float(spec.get("risk_free_rate") or 0.0),
        transaction_cost_bps=float(spec.get("transaction_cost_bps") or 5.0),
        correlation_blend=float(spec.get("fragility_correlation_blend") or 0.65),
        replay_qscan_assets=int(spec.get("replay_qscan_assets") or 6),
        replay_bits=int(spec.get("replay_bits_per_asset") or 2),
        replay_backend=str(spec.get("replay_backend") or "exact"), seed=seed,
        replay_min_positive_positions=int(spec.get("replay_min_positive_positions", 0)),
        replay_rejection_policy=str(spec.get("replay_rejection_policy", "abort")),
        forecast_records=forecast_records,
        forecast_error_history=spec.get("forecast_error_history"),
        gamma_enabled=bool(spec.get("gamma_enabled", False)), gamma_config=dict(spec.get("gamma_config") or {}),
        gamma_construction_policy=str(spec.get("gamma_construction_policy", "legacy_exact")),
        gamma_min_error_records=int(spec.get("gamma_min_error_records", 2)),
        gamma_decision_lag_sessions=int(spec.get("gamma_decision_lag_sessions", 0)),
        gamma_risk205_enabled=bool(spec.get("gamma_risk205_enabled", False)),
        gamma_risk205_config=dict(spec.get("gamma_risk205_config") or {}),
        gamma_risk205_scope=str(spec.get("gamma_risk205_scope", "full_supervisor")),
        gamma_risk_state=dict(spec.get("gamma_risk_state") or {}),
        gamma_beta_records=list(spec.get("gamma_beta_records") or []),
        gamma_session_index=prices.index[:eval_end],
        universe_schedule=universe_schedule,
        full_qrs_primary=bool(spec.get("full_qrs_primary_qaoa", True)),
        full_qrs_beta=float(spec.get("full_qrs_beta") or 0.10),
        full_qrs_bootstrap_reps=int(spec.get("full_qrs_bootstrap_reps") or 128),
        full_qrs_quantile_confidence=float(spec.get("full_qrs_quantile_confidence") or 0.90),
        full_qrs_threshold_iterations=int(spec.get("full_qrs_threshold_iterations") or 4),
        full_qrs_active_set_iterations=int(spec.get("full_qrs_active_set_iterations") or 3),
        full_qrs_exact_variable_cap=int(spec.get("full_qrs_exact_variable_cap") or 12),
        full_qrs_qaoa_reps=int(spec.get("full_qrs_qaoa_reps") or 2),
        full_qrs_qaoa_evaluations=int(spec.get("full_qrs_qaoa_evaluations") or 96),
        v1a_enabled=bool(spec.get("v1a_enabled", False)),
        v1a_artifact_schedule=list(spec.get("v1a_artifact_schedule") or []),
        v1a_es_budget=(None if spec.get("v1a_es_budget") is None else float(spec.get("v1a_es_budget"))),
        v1a_es_activation_max_ratio=(None if spec.get("v1a_es_activation_max_ratio") is None else float(spec.get("v1a_es_activation_max_ratio"))),
        v1a_risk_budget_annual=float(spec.get("v1a_risk_budget_annual") or 0.15),
        v1a_risk_floor_ratio=float(spec.get("v1a_risk_floor_ratio") or 0.10),
        v1a_covariance_additive_floor=float(spec.get("v1a_covariance_additive_floor") or 1e-8),
        v1a_forecast_error_covariance_floor=float(spec.get("v1a_forecast_error_covariance_floor") or 1e-8),
        v1a_scale_tolerance=float(spec.get("v1a_scale_tolerance") if spec.get("v1a_scale_tolerance") is not None else 0.03),
        v1a_gate_b_block_sessions=int(spec.get("v1a_gate_b_block_sessions") or 21),
        v1a_gate_b_bootstrap_replications=int(spec.get("v1a_gate_b_bootstrap_replications") or 2000),
        v1a_gate_b_alpha=float(spec.get("v1a_gate_b_alpha") if spec.get("v1a_gate_b_alpha") is not None else 0.10),
        v1a_gate_b_effect_size=float(spec.get("v1a_gate_b_effect_size") if spec.get("v1a_gate_b_effect_size") is not None else 0.0),
        start_from_cash=bool(spec.get("start_from_cash", False)),
        target_gross_override=(None if spec.get("target_gross_override") is None else float(spec.get("target_gross_override"))),
        full_qrs_decision_mode=str(spec.get("full_qrs_decision_mode") or "qaoa_observed"),
        simulator_audit_every_rebalances=max(0, int(spec.get("simulator_audit_every_rebalances") or 0)),
    )
    if spec.get("replay_checkpoint_directory"):
        # Inputs and provenance are preserved with the committed state. Cache
        # hit/training timing metadata is observational and deliberately kept
        # outside the identity; actual artifact bytes are checked separately.
        common.update({
            "replay_checkpoint_directory": str(spec["replay_checkpoint_directory"]),
            "replay_checkpoint_resume": bool(spec.get("replay_checkpoint_resume", True)),
            "replay_checkpoint_context": {
                "user_id": int(user_id), "source_prices": prices.iloc[:eval_end],
                "sec_price_histories": spec.get("gamma_sec_histories"),
                "request_spec": {key: ({"stored_once_in": "replay_checkpoint_context.sec_price_histories" if key == "gamma_sec_histories" else key,
                                        "supplied": value is not None, "entries": len(value) if value is not None else 0}
                    if key in {"gamma_beta_records", "forecast_records", "forecast_error_history", "gamma_sec_histories"} else value)
                    for key, value in spec.items() if key not in {"replay_checkpoint_directory", "replay_checkpoint_resume"}},
                "market_provenance": {key: value for key, value in market_provenance.items()
                    if key != "v1a_auto_training"},
            },
        })

    def research_walk(*args, **kwargs):
        try:
            return _walk_forward_method(*args, **kwargs)
        except Exception as exc:
            # Preserve preparation and exact failure chronology without turning
            # an input/causality error into a tradable or scored cash result.
            from .preset_backtest import json_safe
            details = dict(getattr(exc, "research_diagnostics", {}) or {})
            from .replay_checkpoint import ReplayCheckpointError
            if isinstance(exc, ReplayCheckpointError):
                details["replay_checkpoint"] = {"enabled": True, "status": "resume_rejected",
                    "directory": common.get("replay_checkpoint_directory"), "reason": str(exc)}
            context = dict(details.get("failure_context") or {})
            completed = 0
            trace = exc.__traceback__
            while trace is not None:
                if trace.tb_frame.f_code.co_name == "_walk_forward_method":
                    values = trace.tb_frame.f_locals
                    for key, variable in (("decision_date", "decision_date"), ("entry_close", "information_cutoff"), ("feature_cutoff", "feature_cutoff")):
                        if variable in values:
                            context.setdefault(key, str(values[variable]))
                    completed = len(values.get("rebalances") or [])
                    checkpoint = values.get("checkpoint")
                    if checkpoint is not None:
                        details["replay_checkpoint"] = checkpoint.audit()
                    p = values.get("q_problem")
                    if p is not None:
                        context.setdefault("forecast_metadata", dict((p.covariance_provenance or {}).get("gamma") or {}))
                trace = trace.tb_next
            exc.research_diagnostics = json_safe({**details,
                "market_data_provenance": market_provenance,
                "research_variant": {"gamma_enabled": common["gamma_enabled"],
                    "risk205_enabled": common["gamma_risk205_enabled"], "risk205_scope": common["gamma_risk205_scope"],
                    "rejection_policy": common["replay_rejection_policy"]},
                "failure_context": context, "completed_rebalances": completed})
            raise

    method_results: dict[str, Any] = {}
    started = time.perf_counter()
    total_main_runs=max(1,len(methods))
    for method_index, method in enumerate(methods):
        seg_start=42.0 + 38.0*(method_index/total_main_runs)
        seg_end=42.0 + 38.0*((method_index+1)/total_main_runs)
        def method_progress(payload: dict[str, Any], _start=seg_start, _end=seg_end, _method=method) -> None:
            frac=max(0.0,min(1.0,float(payload.get("fraction") or 0.0)))
            emit(str(payload.get("stage") or "walk_forward"), str(payload.get("message") or f"{_method} QPO walk-forward"), _start+(_end-_start)*frac, **{key: payload[key] for key in ("rebalance", "rebalance_total", "rebalance_status", "portfolio_path", "completed_rebalances", "next_rebalance", "checkpoint_status", "checkpoint_path", "checkpoint_identity") if key in payload})
        run = research_walk(
            returns, method=method, params=params_by_method.get(method, {}),
            alpha=1.0, true_replay=bool(spec.get("true_qoptima_replay", True)),
            progress_callback=method_progress, progress_label=f"{method} QPO", **common,
        )
        method_results[method] = run

    alpha_frontier: dict[str, Any] = {}
    for alpha_index, alpha in enumerate(alpha_values):
        alpha_count=max(1,len(alpha_values))
        seg_start=81.0 + 9.0*(alpha_index/alpha_count)
        seg_end=81.0 + 9.0*((alpha_index+1)/alpha_count)
        def alpha_progress(payload: dict[str, Any], _start=seg_start, _end=seg_end, _alpha=alpha) -> None:
            frac=max(0.0,min(1.0,float(payload.get("fraction") or 0.0)))
            emit(str(payload.get("stage") or "alpha_frontier"), str(payload.get("message") or f"Alpha {_alpha} validation"), _start+(_end-_start)*frac)
        run = research_walk(
            returns, method=selected_method, params=params_by_method.get(selected_method, {}), alpha=alpha,
            true_replay=False, progress_callback=alpha_progress, progress_label=f"alpha={alpha} validation", **common,
        )
        alpha_frontier[str(alpha)] = {
            "base_equal_weight": run["base_equal_weight"],
            "classical_resilience": run["classical_resilience"],
            "qoptima_replay": run["qoptima_replay"],
        }
    emit("statistics", "Computing paired statistics and final report", 92.0)

    component_ablations: dict[str, Any] = {}
    if bool(spec.get("run_component_ablations", spec.get("run_ablations", True))):
        ablations = {
            "normal_covariance_only": set(),
            "plus_downside": {"downside"},
            "plus_correlation_collapse": {"correlation_collapse"},
            "plus_forecast_error": {"forecast_disagreement"},
            "plus_regime": {"regime"},
            "all_validated_components": {"downside", "correlation_collapse", "forecast_disagreement", "regime"},
        }
        for name, mask in ablations.items():
            run = research_walk(
                returns, method=selected_method, params=params_by_method.get(selected_method, {}), alpha=1.0,
                true_replay=False, component_mask=mask, **common,
            )
            component_ablations[name] = {
                "classical_resilience": run["classical_resilience"],
                "forecast_rebalances_using_pit_models": run["forecast_rebalances_using_pit_models"],
                "note": "Classical ablation isolates the risk-matrix component; it is not labeled predictive without out-of-sample evidence.",
            }

    selected = method_results[selected_method]
    selected_classical = selected["classical_resilience"]
    selected_qo = selected["qoptima_replay"]
    sample = method_results.get("sample", selected)
    rebalances = selected["rebalances"]

    hw = estimate_hardware_qaoa(HardwareQAOASpec(
        total_symbols=len(fetch_universe), qscan_assets=min(int(spec.get("hardware_qscan_assets") or 20), len(fetch_universe)),
        bits_per_asset=int(spec.get("hardware_bits_per_asset") or 4),
        include_selection_qubit=bool(spec.get("hardware_selection_qubit", True)),
        qaoa_reps=int(spec.get("hardware_qaoa_reps") or 1),
        optimizer_evaluations=int(spec.get("hardware_optimizer_evaluations") or 28),
        frontier_points=int(spec.get("hardware_frontier_points") or 5),
        estimator_shots=int(spec.get("hardware_estimator_shots") or 1000),
        final_sampler_shots=int(spec.get("hardware_sampler_shots") or 10000),
        mitigation_multiplier=float(spec.get("hardware_mitigation_multiplier") or 1.5),
        planning_price_per_second=float(spec.get("planning_price_per_second") or 1.60), local_ram_gb=24.0,
        covariance_method=selected_method, resilience_alpha=float(max(alpha_values) if alpha_values else 1.0),
    ))
    per_low = float(hw["usage"]["estimated_qpu_seconds_low"])
    per_high = float(hw["usage"]["estimated_qpu_seconds_high"])

    comparison = {}
    for method, result in method_results.items():
        q = result["qoptima_replay"]
        c = result["classical_resilience"]
        comparison[method] = {
            "base_equal_weight": result["base_equal_weight"],
            "classical": c,
            "qoptima": q,
            "qoptima_minus_classical_sharpe": (
                q.get("sharpe") - c.get("sharpe")
                if q.get("sharpe") is not None and c.get("sharpe") is not None else None
            ),
            "paired_uncertainty": result["comparison_uncertainty"],
            "forecast_rebalances_using_pit_models": result["forecast_rebalances_using_pit_models"],
        }

    forecast_used = selected.get("forecast_rebalances_using_pit_models", 0)
    emit("finalizing", "Finalizing QPO preset result", 98.0)
    return {
        "mode": "QOPTIMA_CAUSAL_WALK_FORWARD_REVIEWED",
        "research_only": True, "real_qpu_executed": False,
        "universe_requested": universe,
        "historical_symbols_fetched": fetch_universe,
        "evaluation_period": period,
        "lookback_sessions": lookback, "rebalance_sessions": rebalance,
        "selected_covariance_method": selected_method,
        "selected_covariance_parameters": params_by_method.get(selected_method, {}),
        "covariance_methods": methods,
        "method_comparison": comparison,
        "method_results": method_results,
        "alpha_frontier": {
            "method": selected_method, "points": alpha_frontier, "recommended_alpha": None,
            "note": "No alpha is recommended without stronger out-of-sample evidence.",
        },
        "component_ablations": component_ablations,
        "classical_resilience_study": selected_classical,
        "true_qoptima_walkforward": selected_qo,
        "v1a": {
            "enabled": bool(spec.get("v1a_enabled", False)),
            "artifact_schedule_entries": len(spec.get("v1a_artifact_schedule") or []),
            "gate_b": method_results.get(selected_method, {}).get("v1a_gate_b"),
            "canonical_qoptima_replay": method_results.get(selected_method, {}).get("canonical_qoptima_replay"),
            "holdout_integrity_note": "Confirmatory interpretation requires a frozen spec and an unspent holdout ledger entry.",
        },
        "results": {
            "base_equal_weight": selected["base_equal_weight"],
            "classical_normal": sample["classical_resilience"],
            "qoptima_resilience": selected_qo,
        },
        "rebalances": rebalances[-50:], "rebalance_count": len(rebalances),
        "market_data_provenance": market_provenance,
        "universe_schedule": universe_schedule_payload,
        "forecast_contract": {
            "records_supplied": len(forecast_records),
            "used_in_this_replay": bool(forecast_used),
            "rebalance_dates_using_compatible_pit_forecasts": int(forecast_used),
            "rule": "Only forecasts created and cut off by the historical information cutoff, aligned to symbols, and matching the rebalance horizon are eligible. Realized errors enter Omega_forecast only after realized_at.",
            "fallback": ("Forbidden: Gamma requires a fresh absolute forecast and matured error ledger at every requested decision."
                if bool(spec.get("gamma_enabled", False)) else
                "Trailing-return proxy is used only where compatible PIT forecasts are unavailable and is explicitly labeled in rebalance provenance."),
        },
        "hardware_scale_counterfactual": {
            "per_rebalance_plan": hw,
            "if_every_rebalance_qpu_seconds_low": per_low * len(rebalances),
            "if_every_rebalance_qpu_seconds_high": per_high * len(rebalances),
            "if_every_rebalance_cost_low_usd": float(hw["usage"]["estimated_cost_low_usd"]) * len(rebalances),
            "if_every_rebalance_cost_high_usd": float(hw["usage"]["estimated_cost_high_usd"]) * len(rebalances),
            "note": "Planning counterfactual only. No historical or real QPU execution occurred.",
        },
        "uncertainty": selected["comparison_uncertainty"],
        "run_manifest": {
            "qoptima_version": "0.4.0-reviewed",
            "seed": seed,
            "covariance_method": selected_method,
            "covariance_parameters": params_by_method.get(selected_method, {}),
            "alpha_values": alpha_values,
            "forecast_records_supplied": len(forecast_records),
            "point_in_time_universe_snapshots_supplied": len(snapshots),
            "replay_backend": common["replay_backend"],
            "full_qrs_primary_qaoa": bool(common["full_qrs_primary"]),
            "full_qrs_schema": FULL_QRS_SCHEMA,
            "full_qrs_beta": float(common["full_qrs_beta"]),
            "real_qpu_executed": False,
            "holdout_sessions_reserved": int(holdout_sessions),
            "development_only": True,
            "performance_validated": False,
            "confirmation_status": "research_only_no_promotion_authorization",
            "full_qrs_decision_mode": common.get("full_qrs_decision_mode"),
            "simulator_audit_every_rebalances": common.get("simulator_audit_every_rebalances"),
            "start_from_cash": common.get("start_from_cash"),
            "replay_min_positive_positions": common["replay_min_positive_positions"],
            "replay_rejection_policy": common["replay_rejection_policy"],
            "rejection_policy_audit": selected.get("rejection_policy_audit"),
            "gamma_enabled": common["gamma_enabled"],
            "risk205_enabled": common["gamma_risk205_enabled"],
            "risk205_scope": common["gamma_risk205_scope"] if common["gamma_risk205_enabled"] else None,
            "research_variant": selected.get("research_variant"),
            "evaluation_coverage": selected.get("evaluation_coverage"),
            "legacy_cash_reset_audit": selected.get("legacy_cash_reset_audit"),
            "replay_checkpoint": selected.get("replay_checkpoint"),
        },
        "interpretation": "Results are causal walk-forward research with explicit uncertainty. When enabled, true replay includes the literal Full-QRS exact-diagonal QAOA refinement and exact full-score rescoring at each rebalance. No method is labeled superior from in-sample fit alone.",
        "wall_clock_seconds": float(time.perf_counter() - started),
    }

# Backward-compatible helper retained for the pre-review regression tests.  New
# research routes use ``_walk_forward_method`` above so the old classical proxy
# cannot be mistaken for true QOptima replay.
def _run_path(*args, **kwargs):
    from .backtest_overlay_legacy import _run_path as legacy_run_path
    return legacy_run_path(*args, **kwargs)
