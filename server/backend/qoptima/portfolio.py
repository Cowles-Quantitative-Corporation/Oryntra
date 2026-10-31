from __future__ import annotations

import json
import math
from typing import Any
import numpy as np
import pandas as pd

from ..database import get_connection
from ..market_repository import get_market_repository
from ..ibkr_adapter import ensure_ibkr_schema
from ..provider_credentials import decrypted_credentials
from .problem import QOptimaPortfolioProblem
from .resilience import build_resilience_model
from .market_data import canonical_price_series


def _recursive_numeric(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().replace("_", "") in keys:
                try:
                    number = float(item)
                    if math.isfinite(number):
                        return number
                except (TypeError, ValueError):
                    pass
        for item in value.values():
            found = _recursive_numeric(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _recursive_numeric(item, keys)
            if found is not None:
                return found
    return None


def _position_symbol(row: dict[str, Any]) -> str | None:
    for key in ("ticker", "symbol", "contractDesc", "description"):
        raw = str(row.get(key) or "").strip().upper()
        if raw:
            return raw.split()[0]
    return None


def _position_market_value(row: dict[str, Any]) -> float | None:
    for key in ("mktValue", "marketValue", "market_value"):
        try:
            value = float(row.get(key))
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            pass
    return None


def latest_active_portfolio(user_id: int) -> dict[str, Any]:
    ensure_ibkr_schema()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM ibkr_account_snapshots WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"available": False, "source": "ibkr_snapshot", "positions": [], "reason": "no_ibkr_snapshot"}
    positions = json.loads(row["positions_json"] or "[]")
    ledger = json.loads(row["ledger_json"] or "{}")
    nlv = _recursive_numeric(ledger, {"netliquidationvalue", "netliquidation", "netliq", "nlv"})
    if nlv is None or abs(nlv) <= 1e-9:
        values = [v for v in (_position_market_value(x) for x in positions if isinstance(x, dict)) if v is not None]
        nlv = float(sum(values)) if values else None
    if nlv is None or abs(nlv) <= 1e-9:
        return {"available": False, "source": "ibkr_snapshot", "positions": [], "reason": "nlv_unavailable", "captured_at": row["captured_at"]}
    weights: dict[str, float] = {}
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = _position_symbol(position)
        value = _position_market_value(position)
        if not symbol or value is None or value <= 0:
            continue
        weights[symbol] = weights.get(symbol, 0.0) + float(value) / float(nlv)
    return {
        "available": bool(weights),
        "source": "ibkr_snapshot",
        "account_id": row["account_id"],
        "captured_at": row["captured_at"],
        "net_liquidation": float(nlv),
        "positions": [{"symbol": s, "weight": float(w)} for s, w in sorted(weights.items())],
    }


def export_active_portfolio_payload(user_id: int) -> dict[str, Any]:
    """Return a paste-safe QOptima portfolio payload from the latest local IBKR snapshot."""
    active = latest_active_portfolio(user_id)
    if not active.get("available"):
        return {
            "available": False,
            "reason": active.get("reason") or "no_ibkr_snapshot",
            "payload": None,
            "text": "",
        }
    positions = [
        {"symbol": str(row["symbol"]).upper(), "weight": float(row["weight"])}
        for row in active.get("positions", [])
        if float(row.get("weight") or 0.0) > 0
    ]
    represented = float(sum(row["weight"] for row in positions))
    payload = {
        "format": "CQC_QOPTIMA_PORTFOLIO_V1",
        "source": "IBKR_ACTIVE_PORTFOLIO",
        "captured_at": active.get("captured_at"),
        "net_liquidation": active.get("net_liquidation"),
        "represented_weight": represented,
        "cash_or_other_weight": max(0.0, 1.0 - represented),
        "positions": positions,
    }
    return {
        "available": True,
        "payload": payload,
        "text": json.dumps(payload, indent=2, sort_keys=False),
    }


def build_problem(
    user_id: int,
    *,
    current_weights: dict[str, float],
    candidate_symbols: list[str],
    provider: str = "cache_only",
    lookback_sessions: int = 126,
    expected_returns: dict[str, float] | None = None,
    max_name_weight: float = 0.20,
    risk_aversion: float = 3.0,
    turnover_penalty: float = 0.20,
    risk_free_rate: float = 0.0,
    borrowing_rate: float | None = None,
    transaction_cost_bps: float = 0.0,
    covariance_method: str = "shrinkage",
    covariance_parameters: dict[str, Any] | None = None,
    covariance_compare: bool = True,
    resilience_alpha: float = 1.0,
    full_qrs_enabled: bool = True,
    full_qrs_beta: float = 0.10,
    full_qrs_bootstrap_reps: int = 256,
    full_qrs_quantile_confidence: float = 0.90,
    full_qrs_max_condition_number: float = 1.0e8,
    v1a_enabled: bool = False,
    v1a_artifact_path: str | None = None,
    v1a_feature_values: dict[str, float] | None = None,
    v1a_es_budget: float | None = None,
    v1a_es_activation_max_ratio: float | None = None,
    v1a_risk_budget_annual: float = 0.15,
    v1a_risk_floor_ratio: float = 0.10,
    v1a_covariance_additive_floor: float = 1e-8,
    v1a_forecast_error_covariance_floor: float = 1e-8,
    v1a_forecast_error_covariance_window: int = 60,
    v1a_forecast_error_min_history: int = 12,
    v1a_quantile_block_stride: int = 1,
    v1a_qaoa_low_tail_quantile: float = 0.05,
    v1a_qaoa_phase_global_floor_ratio: float = 0.10,
    v1a_qaoa_phase_absolute_floor: float = 1e-8,
    v1a_qaoa_energy_cap: float = 8.0,
    v1a_qaoa_infeasible_gap: float = 1.0,
    v1a_scale_tolerance: float = 0.03,
    fragility_enabled: bool = True,
    fragility_downside_weight: float = 0.0,
    fragility_correlation_weight: float = 0.0,
    fragility_forecast_weight: float = 0.0,
    fragility_regime_weight: float = 0.0,
    fragility_correlation_blend: float = 0.65,
    model_forecasts: dict[str, dict[str, float]] | None = None,
    forecast_records: list[dict[str, Any]] | None = None,
    forecast_error_history: list[dict[str, Any]] | None = None,
    decision_time: str | None = None,
    forecast_horizon_sessions: int = 252,
    require_adjusted_prices: bool = False,
    gamma_enabled: bool = False, gamma_min_error_records: int = 2,
    gamma_feature_cutoff: str | None = None, gamma_decision_lag_sessions: int = 0, gamma_config: dict[str, Any] | None = None,
) -> tuple[QOptimaPortfolioProblem, dict[str, Any]]:
    from .covariance import estimate_covariance, compare_covariance_methods
    from .forecast_contract import align_forecast_records, forecast_error_covariance, forecast_error_sample_matrix, ForecastRecord
    from .calibration import calibrate_resilience_weights, calibrate_fragility_bands
    from .full_qrs import build_full_qrs_scenarios
    from .v1a import infer_v1a_snapshot, V1A_SPEC_VERSION

    symbols: list[str] = []
    for raw in list(current_weights) + list(candidate_symbols or []):
        symbol = str(raw or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if len(symbols) < 2:
        raise ValueError("QOptima needs at least two active/candidate symbols")
    if len(symbols) > 80:
        raise ValueError("QOptima preprocessing accepts at most 80 symbols per burst")

    repo = get_market_repository()
    credentials: dict[str, str] = {}
    try:
        credentials = decrypted_credentials(user_id)
    except Exception:
        credentials = {}
    frames: dict[str, pd.Series] = {}
    metadata: dict[str, Any] = {}
    minimum_bars = max(63, int(lookback_sessions) + 1)
    for symbol in symbols:
        result = repo.get_history(
            symbol,
            period="2y" if lookback_sessions <= 252 else "5y",
            minimum_bars=minimum_bars,
            allow_api=provider != "cache_only",
            provider_preference=provider,
            provider_api_keys=credentials,
            allow_platform_provider_keys=False,
        )
        if result.history is None or result.history.empty or "Close" not in result.history:
            raise ValueError(f"No usable history for {symbol}")
        frame = result.history
        series, price_meta = canonical_price_series(
            repo, symbol, frame, minimum_bars=minimum_bars, require_adjusted=bool(require_adjusted_prices)
        )
        frames[symbol] = series
        metadata[symbol] = {
            "provider": result.metadata.provider,
            "source": result.metadata.source,
            "bars": int(price_meta.get("bars") or result.metadata.bars or 0),
            "last_bar": result.metadata.last_bar,
            **price_meta,
        }
    prices = pd.concat(frames.values(), axis=1, join="inner").dropna()
    source_session_index = prices.index.copy()
    research_lag = max(int(gamma_decision_lag_sessions), int(gamma_enabled))
    if research_lag not in (0, 1):
        raise ValueError("gamma_decision_lag_sessions must be zero or one")
    gamma_audit = None
    if research_lag:
        from .gamma_integration import utc_timestamp, GammaContractError
        if decision_time is None:
            raise GammaContractError("Gamma research clock requires explicit entry-close decision_time")
        entry = utc_timestamp(decision_time)
        stamps = pd.DatetimeIndex([utc_timestamp(day) for day in prices.index])
        prior = stamps[stamps < entry]
        if len(prior) < 2:
            raise GammaContractError("Insufficient history strictly before Gamma entry close")
        cutoff = utc_timestamp(gamma_feature_cutoff) if gamma_feature_cutoff is not None else prior[-1]
        if cutoff >= entry:
            raise GammaContractError("Gamma feature cutoff must strictly precede entry close")
        prices = prices.loc[stamps <= cutoff]
        gamma_feature_cutoff = cutoff.isoformat()
    prices = prices.tail(int(lookback_sessions) + 1)
    if len(prices) < max(42, int(lookback_sessions * 0.60)):
        raise ValueError(f"Only {len(prices)} aligned sessions are available for the requested QOptima lookback")
    returns = prices.pct_change().dropna()

    cov_est = estimate_covariance(returns, covariance_method, parameters=covariance_parameters)
    cov = np.asarray(cov_est.covariance, dtype=float)
    cov_comparison = compare_covariance_methods(
        returns,
        method_parameters={
            "quantum_density": dict(covariance_parameters or {}) if str(covariance_method).lower() == "quantum_density" else {},
        },
    ) if covariance_compare else {cov_est.method: cov_est.as_dict(include_matrix=False)}

    decision = decision_time or str(returns.index[-1])
    if gamma_enabled:
        from .gamma_integration import prepare_gamma_inputs, validate_gamma_config
        gcfg = validate_gamma_config(gamma_config)
        prepared = prepare_gamma_inputs(forecast_records, decision_time=decision,
            feature_cutoff=gamma_feature_cutoff, symbols=symbols,
            horizon_sessions=forecast_horizon_sessions, min_error_records=gamma_min_error_records,
            error_records=forecast_error_history, required_beta=bool(gcfg.get("require_beta", False)),
            require_verified_macro_vintages=bool(gcfg.get("require_verified_macro_vintages", False)),
            required_sec=bool(gcfg.get("require_sec", False)), session_index=source_session_index)
        forecast_records, forecast_error_history = prepared["current_records"], prepared["history_records"]
        gamma_audit = prepared["audit"]
    if research_lag:
        from .gamma_integration import gamma_score_rates
        rates = gamma_score_rates(forecast_horizon_sessions, risk_free_rate, borrowing_rate, transaction_cost_bps)
        risk_free_rate, borrowing_rate, transaction_cost_bps = rates["risk_free_rate"], rates["borrowing_rate"], rates["transaction_cost_bps"]
    aligned_forecasts = align_forecast_records(
        forecast_records,
        decision_time=(decision if gamma_enabled or not research_lag else gamma_feature_cutoff),
        symbols=symbols,
        required_horizon_sessions=forecast_horizon_sessions if forecast_records else None,
        strict=False,
    )
    current_regime_labels = [
        str((record.metadata or {}).get("v1a_regime_tail_v1_label") or "").strip()
        for record in (aligned_forecasts.get("records") or [])
        if str((record.metadata or {}).get("v1a_regime_tail_v1_label") or "").strip()
    ]
    v1a_current_regime_label = (current_regime_labels[0] if current_regime_labels and len(set(current_regime_labels)) == 1 else None)
    if aligned_forecasts["available"]:
        mu = np.asarray(aligned_forecasts["matrix"], dtype=float).mean(axis=0)
        expected_source = "point_in_time_forecast_contract_consensus"
    elif expected_returns:
        mu = np.array([float(expected_returns.get(s, np.nan)) for s in symbols], dtype=float)
        fallback = returns.mean().to_numpy(dtype=float) * 252.0
        mu = np.where(np.isfinite(mu), mu, fallback)
        expected_source = "caller_supplied_unverified_with_historical_fallback"
    else:
        raw = returns.mean().to_numpy(dtype=float) * 252.0
        mu = 0.50 * raw
        expected_source = "50pct_shrunk_trailing_mean_proxy"

    error_records: list[ForecastRecord] = []
    error_rejections: list[dict[str, str]] = []
    for raw in list(forecast_error_history or []):
        try:
            record = ForecastRecord.from_mapping(raw)
            # Historical error records must themselves have been created before the current decision.
            record.validate(decision_time=(gamma_feature_cutoff if research_lag else decision), symbols=symbols, required_horizon_sessions=None)
            if record.realized_outcome and record.realized_outcome_available_by(gamma_feature_cutoff if research_lag else decision):
                error_records.append(record)
            elif record.realized_outcome:
                error_rejections.append({
                    "error": "realized outcome was not demonstrably available by decision_time; excluded from forecast-error covariance",
                    "record": str(raw)[:300],
                })
        except Exception as exc:
            error_rejections.append({"error": str(exc), "record": str(raw)[:300]})
    forecast_error_cov, forecast_error_meta = forecast_error_covariance(error_records, symbols)
    forecast_error_meta["rejected"] = error_rejections
    forecast_error_samples, forecast_error_samples_meta = forecast_error_sample_matrix(error_records, symbols)
    forecast_error_samples_meta["rejected"] = error_rejections

    current = np.array([max(0.0, float(current_weights.get(s, 0.0))) for s in symbols], dtype=float)
    gross = float(current.sum())
    if gross <= 1e-8:
        raise ValueError("Active portfolio weights are empty")
    # Keep uninvested cash instead of scaling it away.  Only scale down leverage >100%.
    target_gross = min(1.0, gross)
    if gross > 1.0 + 1e-12:
        current *= target_gross / gross

    manual_weights = {
        "downside": max(0.0, float(fragility_downside_weight)),
        "correlation_collapse": max(0.0, float(fragility_correlation_weight)),
        "forecast_disagreement": max(0.0, float(fragility_forecast_weight)),
        "regime": max(0.0, float(fragility_regime_weight)),
    }
    calibration = calibrate_resilience_weights(
        returns, cov, correlation_blend=float(fragility_correlation_blend),
        covariance_method=cov_est.method, covariance_parameters=dict(covariance_parameters or {}),
        forecast_error_covariance=forecast_error_cov,
    )
    if fragility_enabled and not any(v > 0 for v in manual_weights.values()):
        resilience_weights = dict(calibration.get("weights") or {})
        weight_source = "training_window_calibration"
    elif fragility_enabled:
        resilience_weights = manual_weights
        weight_source = "operator_supplied_experimental"
    else:
        resilience_weights = {k: 0.0 for k in manual_weights}
        weight_source = "fragility_disabled"

    record_forecasts = None
    if aligned_forecasts.get("records"):
        record_forecasts = {
            f"{r.model_id}:{r.model_version}": {symbol: float(r.values[symbol]) for symbol in symbols}
            for r in aligned_forecasts["records"]
        }
    resilience = build_resilience_model(
        returns,
        cov,
        tuple(symbols),
        downside_weight=float(resilience_weights.get("downside", 0.0)),
        correlation_weight=float(resilience_weights.get("correlation_collapse", 0.0)),
        forecast_weight=float(resilience_weights.get("forecast_disagreement", 0.0)),
        regime_weight=float(resilience_weights.get("regime", 0.0)),
        correlation_blend=float(fragility_correlation_blend),
        model_forecasts=record_forecasts or model_forecasts,
        forecast_error_covariance=forecast_error_cov,
        forecast_error_metadata=forecast_error_meta,
        alpha=float(resilience_alpha),
    )
    v1a_snapshot = None
    if bool(v1a_enabled):
        if not v1a_artifact_path:
            raise ValueError("v1a_enabled requires v1a_artifact_path; live QOptima never retrains V1A inside a burst")
        v1a_snapshot = infer_v1a_snapshot(
            v1a_artifact_path,
            returns,
            information_cutoff=str(gamma_feature_cutoff if research_lag else decision),
            feature_overrides=dict(v1a_feature_values or {}),
            es_budget_override=v1a_es_budget,
            r_max_override=v1a_es_activation_max_ratio,
        )
        if int(v1a_snapshot.horizon_sessions) != int(forecast_horizon_sessions):
            raise ValueError(
                f"V1A artifact horizon {v1a_snapshot.horizon_sessions} does not match QOptima forecast horizon {forecast_horizon_sessions}"
            )
        if bool(v1a_snapshot.signed_control):
            raise ValueError("signed V1A-R control artifacts are validation-only and cannot drive production/research QPO scoring")

    full_qrs_scenarios, full_qrs_scenario_meta = build_full_qrs_scenarios(
        returns,
        cov,
        correlation_blend=float(fragility_correlation_blend),
        max_condition_number=float(full_qrs_max_condition_number),
        v1a_gamma=(None if v1a_snapshot is None else float(v1a_snapshot.gamma)),
        v1a_additive_floor=float(v1a_covariance_additive_floor),
    )
    full_qrs_scenario_meta["forecast_error_samples"] = forecast_error_samples_meta
    full_qrs_scenario_meta["forecast_tail_beta"] = float(full_qrs_beta)
    full_qrs_scenario_meta["forecast_tail_estimator"] = "empirical_lower_quantile_with_deterministic_bootstrap_lower_bound_v1"

    fragility_bands = calibrate_fragility_bands(
        returns, covariance_method=cov_est.method, covariance_parameters=dict(covariance_parameters or {}),
        resilience_weights=resilience_weights, correlation_blend=float(fragility_correlation_blend),
    )
    problem = QOptimaPortfolioProblem(
        symbols=tuple(symbols),
        current_weights=current,
        expected_returns=mu,
        covariance=cov,
        target_gross=target_gross,
        max_name_weight=float(max_name_weight),
        risk_aversion=float(risk_aversion),
        turnover_penalty=float(turnover_penalty),
        risk_free_rate=float(risk_free_rate),
        transaction_cost_bps=float(transaction_cost_bps),
        horizon_sessions=int(forecast_horizon_sessions),
        fragility_enabled=bool(fragility_enabled),
        effective_covariance=resilience["effective_covariance"],
        alpha_covariance=resilience["alpha_covariance"],
        resilience_alpha=float(resilience_alpha),
        fragility_components=resilience["components"],
        fragility_weights=resilience["weights"],
        fragility_metadata={**resilience["metadata"], "calibration": calibration, "weight_source": weight_source, "fragility_bands": fragility_bands},
        covariance_method=cov_est.method,
        covariance_provenance=cov_est.as_dict(include_matrix=False),
        full_qrs_enabled=bool(full_qrs_enabled),
        lending_rate=float(risk_free_rate),
        borrowing_rate=float(risk_free_rate if borrowing_rate is None else borrowing_rate),
        full_qrs_beta=float(full_qrs_beta),
        full_qrs_bootstrap_reps=int(full_qrs_bootstrap_reps),
        full_qrs_quantile_confidence=float(full_qrs_quantile_confidence),
        forecast_error_samples=forecast_error_samples,
        v1a_forecast_error_regime_labels=tuple(forecast_error_samples_meta.get("v1a_regime_tail_v1_labels") or []) if forecast_error_samples is not None else None,
        v1a_forecast_error_information_cutoffs=(tuple(forecast_error_samples_meta.get("record_information_cutoffs") or []) if gamma_enabled and forecast_error_samples is not None else None),
        v1a_forecast_error_realized_at=(tuple(forecast_error_samples_meta.get("record_realized_at") or []) if gamma_enabled and forecast_error_samples is not None else None),
        v1a_current_regime_label=v1a_current_regime_label,
        full_qrs_stress_covariances=full_qrs_scenarios,
        full_qrs_stress_metadata=full_qrs_scenario_meta,
        v1a_enabled=bool(v1a_snapshot is not None),
        v1a_state_metadata=(None if v1a_snapshot is None else v1a_snapshot.as_dict()),
        v1a_gamma=(0.0 if v1a_snapshot is None else float(v1a_snapshot.gamma)),
        v1a_risk_budget_annual=float(v1a_risk_budget_annual),
        v1a_risk_floor_ratio=float(v1a_risk_floor_ratio),
        v1a_forecast_error_covariance_floor=float(v1a_forecast_error_covariance_floor),
        v1a_forecast_error_covariance_window=int(v1a_forecast_error_covariance_window),
        v1a_forecast_error_min_history=int(v1a_forecast_error_min_history),
        v1a_quantile_block_stride=int(v1a_quantile_block_stride),
        v1a_qaoa_low_tail_quantile=float(v1a_qaoa_low_tail_quantile),
        v1a_qaoa_phase_global_floor_ratio=float(v1a_qaoa_phase_global_floor_ratio),
        v1a_qaoa_phase_absolute_floor=float(v1a_qaoa_phase_absolute_floor),
        v1a_qaoa_energy_cap=float(v1a_qaoa_energy_cap),
        v1a_qaoa_infeasible_gap=float(v1a_qaoa_infeasible_gap),
        v1a_scale_tolerance=float(v1a_scale_tolerance),
    )
    # Same-portfolio, same-window risk-matrix comparison.  This does not rerun
    # QPO for every estimator; it answers the narrower and auditable question:
    # how does the *same active portfolio* look under each covariance method and
    # its training-window resilience calibration?
    if covariance_compare:
        from .covariance import COVARIANCE_METHODS
        for method_name in COVARIANCE_METHODS:
            try:
                params_for_method = dict(covariance_parameters or {}) if method_name == str(covariance_method).lower() else {}
                est_cmp = estimate_covariance(returns, method_name, parameters=params_for_method)
                cal_cmp = calibrate_resilience_weights(
                    returns, np.asarray(est_cmp.covariance, dtype=float),
                    correlation_blend=float(fragility_correlation_blend),
                    covariance_method=est_cmp.method, covariance_parameters=params_for_method,
                    forecast_error_covariance=forecast_error_cov,
                )
                if fragility_enabled and not any(v > 0 for v in manual_weights.values()):
                    weights_cmp = dict(cal_cmp.get("weights") or {})
                    weight_source_cmp = "training_window_calibration"
                elif fragility_enabled:
                    weights_cmp = dict(manual_weights)
                    weight_source_cmp = "operator_supplied_experimental"
                else:
                    weights_cmp = {k: 0.0 for k in manual_weights}
                    weight_source_cmp = "fragility_disabled"
                res_cmp = build_resilience_model(
                    returns, np.asarray(est_cmp.covariance, dtype=float), tuple(symbols),
                    downside_weight=float(weights_cmp.get("downside", 0.0)),
                    correlation_weight=float(weights_cmp.get("correlation_collapse", 0.0)),
                    forecast_weight=float(weights_cmp.get("forecast_disagreement", 0.0)),
                    regime_weight=float(weights_cmp.get("regime", 0.0)),
                    correlation_blend=float(fragility_correlation_blend),
                    model_forecasts=record_forecasts or model_forecasts,
                    forecast_error_covariance=forecast_error_cov,
                    forecast_error_metadata=forecast_error_meta,
                    alpha=float(resilience_alpha),
                )
                cmp_problem = QOptimaPortfolioProblem(
                    symbols=tuple(symbols), current_weights=current, expected_returns=mu,
                    covariance=np.asarray(est_cmp.covariance, dtype=float), target_gross=target_gross,
                    max_name_weight=float(max_name_weight), risk_aversion=float(risk_aversion),
                    turnover_penalty=float(turnover_penalty), risk_free_rate=float(risk_free_rate),
                    transaction_cost_bps=float(transaction_cost_bps), horizon_sessions=252,
                    fragility_enabled=bool(fragility_enabled),
                    effective_covariance=res_cmp["effective_covariance"],
                    alpha_covariance=res_cmp["alpha_covariance"], resilience_alpha=float(resilience_alpha),
                    fragility_components=res_cmp["components"], fragility_weights=res_cmp["weights"],
                    fragility_metadata={**res_cmp["metadata"], "calibration": cal_cmp, "weight_source": weight_source_cmp},
                    covariance_method=est_cmp.method, covariance_provenance=est_cmp.as_dict(include_matrix=False),
                    full_qrs_enabled=False,
                )
                row = dict(cov_comparison.get(method_name) or est_cmp.as_dict(include_matrix=False))
                row["same_active_portfolio_metrics"] = cmp_problem.metrics(current)
                row["resilience_coefficients"] = dict(res_cmp.get("weights") or {})
                row["resilience_weight_source"] = weight_source_cmp
                row["comparison_contract"] = "same symbols, same return window, same expected-return vector, same active weights"
                cov_comparison[method_name] = row
            except Exception as exc:
                row = dict(cov_comparison.get(method_name) or {})
                row["comparison_error"] = f"{type(exc).__name__}: {exc}"
                cov_comparison[method_name] = row

    adjusted_flags = [metadata[s].get("verified_adjusted") for s in symbols]
    price_warning = None
    if not all(flag is True for flag in adjusted_flags):
        price_warning = "One or more histories are not explicitly adjusted/total-return compatible; dividend/split handling is not fully verified for this live research run."
    provenance = {
        "gamma": gamma_audit,
        "research_clock": {"decision_lag_sessions": research_lag, "feature_cutoff": gamma_feature_cutoff,
            "score_units": "linear_annual_equivalent_not_compound_return" if research_lag else "legacy_annualized"},
        "aligned_sessions": int(len(returns)),
        "first_session": str(returns.index[0].date()),
        "last_session": str(returns.index[-1].date()),
        "decision_time": str(decision),
        "expected_return_source": expected_source,
        "market_data": metadata,
        "price_adjustment_warning": price_warning,
        "symbols": symbols,
        "covariance": {
            "selected_method": cov_est.method,
            "selected": cov_est.as_dict(include_matrix=False),
            "comparison": cov_comparison,
        },
        "forecasts": {
            "current": {
                "compatible_models": aligned_forecasts["compatible_models"],
                "provenance": aligned_forecasts["provenance"],
                "rejected": aligned_forecasts["rejected"],
            },
            "historical_error_covariance": forecast_error_meta,
        },
        "fragility": {
            "enabled": bool(fragility_enabled),
            "alpha": float(resilience_alpha),
            "weights": resilience["weights"],
            "weight_source": weight_source,
            "calibration": calibration,
            "fragility_bands": fragility_bands,
            "metadata": resilience["metadata"],
            "formula": "FP=sqrt(w^T Sigma_eff w)/sqrt(w^T Sigma_base w)-1; Sigma_alpha=(1-alpha)Sigma_base+alpha Sigma_eff; legacy QRS=(R-rf)/sqrt(w^T Sigma_alpha w)",
        },
        "v1a": {
            "enabled": bool(v1a_snapshot is not None),
            "spec_version": V1A_SPEC_VERSION,
            "state": None if v1a_snapshot is None else v1a_snapshot.as_dict(),
            "live_training": False,
            "role": "PIT classical state generator feeding Full-QRS stress conditioning; neural network is not inside the QAOA circuit",
        },
        "full_qrs": {
            "enabled": bool(full_qrs_enabled),
            "schema": "cqc.qoptima.full_qrs.v1",
            "scenario_set": full_qrs_scenario_meta,
            "forecast_error_samples": forecast_error_samples_meta,
            "lending_rate": float(risk_free_rate),
            "borrowing_rate": float(risk_free_rate if borrowing_rate is None else borrowing_rate),
            "beta": float(full_qrs_beta),
            "bootstrap_reps": int(full_qrs_bootstrap_reps),
            "quantile_confidence": float(full_qrs_quantile_confidence),
            "formula": (
                "V1A-QRS v1.6 piecewise composition score with economic risk floor, PIT-standardized conservative forecast-error tail, "
                "soft-RMT state-conditioned stress scenarios, and separate hard viability gate"
                if v1a_snapshot is not None
                else "[mu'w + c+*r_l - c-*r_b - r_l - C_L1 + lowerQ_beta(w'epsilon)] / sqrt((1-alpha)w'Sigma_b w + alpha*max_j w'Sigma_j w); exact margin viability remains a separate hard QRM/broker-engine gate"
            ),
        },
    }
    if research_lag:
        provenance["_gamma_returns_history"] = returns.to_numpy(dtype=float)
    return problem, provenance
