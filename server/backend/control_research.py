"""Interactive proprietary research runners for Oryntra Control.

This module is intentionally distinct from frozen confirmatory protocols. It
uses the selected current/cached market-data provider, runs one or more models
through identical construction/risk/benchmark settings, and can report an
explicit equal-weight *strategy ensemble* without altering any underlying model.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .control_benchmarks import (
    benchmark_symbols,
    build_benchmark_returns,
    evaluate_many,
    parse_benchmark_list,
    parse_benchmark_spec,
)
from .market_repository import get_market_repository, normalize_ticker
from .control_risk_free import build_control_risk_free
from .corporate_repository import get_corporate_repository
from .control_beta import (
    beta_current_status_from_histories, beta_operational_status, research_beta_state_panel,
    required_market_tickers, beta_service,
)
from .models.registry import get_model_spec
from .models.pre_gamma import PRE_GAMMA_IDS, pre_gamma_policy
from .phase4_control import build_control_config
from .provider_credentials import decrypted_credentials
from .promotion_gates import evaluate_promotion_gates
from .universal_research import run_universal
from .risk.v204 import V204BetaControlConfig, apply_beta_overlay_v204, beta_regime_modifier_v204
from .qoptima.backtest_overlay import run_qoptima_backtest_overlay
from .qoptima.preset_backtest import build_causal_v1a_artifact_schedule

_EVAL_SESSIONS = {"3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260, "10y": 2520}
_FETCH_PERIOD = {"3mo": "2y", "6mo": "2y", "1y": "5y", "2y": "5y", "5y": "10y", "10y": "all"}
_MAX_MODELS = 12
_CONTROL_REPLAY_ROOT = Path(__file__).resolve().parents[1] / "data" / "control_replay_checkpoints"


def _pre_gamma_replay_directory(user_id: int, model_id: str, spec: dict[str, Any],
                                universe: list[str], evaluation_start: str | None,
                                evaluation_end: str) -> Path:
    """Server-local namespace for retrying one logical research study.

    This is only a namespace, never permission to reuse a state. The replay
    engine separately binds every prepared input, artifact and code version.
    Job IDs, row order and budget do not change the underlying replay.
    """
    if model_id not in PRE_GAMMA_IDS:
        raise ValueError("Replay checkpoint namespace is restricted to Pre-Gamma rows")
    identity = {"model_id": model_id, "universe": list(universe),
                "evaluation_start": evaluation_start, "evaluation_end": evaluation_end,
                "requested": {key: spec.get(key) for key in (
                    "provider", "evaluation_period", "construction", "risk_model", "phase3_enabled",
                    "fundamentals_mode", "risk_free_mode", "risk_free_annual_pct",
                    "pre_gamma_config", "pre_gamma_beta_source", "pre_gamma_rejection_policy")}}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False,
                                      separators=(",", ":")).encode()).hexdigest()
    return _CONTROL_REPLAY_ROOT / f"user_{int(user_id)}" / model_id / digest


def _notify_research_progress(callback, payload: dict[str, Any]) -> None:
    """Observability must never change a model decision or abort its computation."""
    if callback is not None:
        try:
            callback(dict(payload))
        except Exception:
            pass  # The normal result/error path remains authoritative.


def _pre_gamma_rejection_policy(spec: dict[str, Any]) -> str:
    policy = str(spec.get("pre_gamma_rejection_policy") or "abort")
    if policy not in {"abort", "cash_on_qrm_rejection"}:
        raise ValueError("pre_gamma_rejection_policy must be abort or cash_on_qrm_rejection")
    return policy


def _control_model_identity(model_spec: Any, spec: dict[str, Any]) -> dict[str, Any]:
    model = dict(model_spec.public_dict())
    if model_spec.id in PRE_GAMMA_IDS and spec.get("pre_gamma_rejection_policy") == "cash_on_qrm_rejection":
        model["label"] += " · forced-cash research policy"
        model["description"] = "Explicit forced-cash research policy applied to this native Pre-Gamma variant. Typed QRM rejections may execute cash with costs; other failures remain blocking."
    return model


def _prepare_control_pre_gamma_beta(spec: dict[str, Any], universe: list[str], evaluation_start: str, evaluation_end: str, *, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Prepare the explicit research producer once without mutating native Beta."""
    source = str(spec.get("pre_gamma_beta_source") or "supplied")
    if source not in {"supplied", "causal_research"}:
        raise ValueError("pre_gamma_beta_source must be supplied or causal_research")
    if source == "supplied":
        return spec
    if spec.get("pre_gamma_beta_records"):
        raise ValueError("Choose causal_research generation or supplied Pre-Gamma Beta records, not both")
    from . import database
    from .control_beta import beta_root
    from .qoptima.gamma_forecast import _validated_beta_records
    from .qoptima.gamma_integration import GammaContractError
    preparation: dict[str, Any] = {"source": source, "evaluation_start": evaluation_start,
                                   "evaluation_end": evaluation_end, "native_beta_artifacts_modified": False}
    try:
        from .qoptima.gamma_beta import build_causal_beta_records
        generated = build_causal_beta_records(
            market_db_path=database.DB_PATH, beta_db_path=beta_root() / "beta.sqlite",
            stock_symbols=list(universe), evaluation_start=evaluation_start, evaluation_end=evaluation_end,
            output_directory=Path(__file__).resolve().parents[1] / "data" / "qoptima" / "gamma_beta_research",
            progress_callback=(lambda event: _notify_research_progress(progress_callback, event)) if progress_callback is not None else None,
        )
        preparation["producer_diagnostics"] = generated.get("diagnostics") or {}
        records = list(generated.get("records") or [])
        accepted, rejected = _validated_beta_records(records)
        preparation.update(records_supplied=len(records), records_accepted=len(accepted), records_rejected=rejected)
        if not records or rejected or len(accepted) != len(records):
            raise GammaContractError("Causal Pre-Gamma Beta producer returned no usable records or invalid PIT provenance")
        return {**spec, "pre_gamma_beta_records": records, "_pre_gamma_beta_preparation": preparation}
    except Exception as exc:
        producer_failure = getattr(exc, "gamma_beta_diagnostics", None)
        if isinstance(producer_failure, dict):
            preparation["producer_diagnostics"] = producer_failure
        diagnostics = getattr(exc, "research_diagnostics", None)
        diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        diagnostics["beta_preparation"] = preparation
        diagnostics.setdefault("failure_context", {"stage": "causal_beta_preparation"})
        exc.research_diagnostics = diagnostics
        raise


def _model_settings_scope(model_spec: Any, spec: dict[str, Any], *, completed: bool) -> dict[str, Any]:
    """Distinguish the GUI's shared selections from each engine's real route."""
    role = getattr(model_spec, "role", "portfolio")
    pre_gamma = model_spec.id in PRE_GAMMA_IDS
    gamma_policy = pre_gamma_policy(model_spec.id) if pre_gamma else None
    requested = {
        "construction": str(spec.get("construction") or "phase15"),
        "risk_model": str(spec.get("risk_model") or "v203"),
        "phase3_enabled": bool(spec.get("phase3_enabled", True)),
        "evaluation_period": str(spec.get("evaluation_period") or "1y"),
        "risk_free_mode": str(spec.get("risk_free_mode") or "manual_constant"),
        "risk_free_annual_pct": float(spec.get("risk_free_annual_pct", 0.0)),
        "universe_size": len(spec.get("universe") or []),
    }
    if role == "portfolio":
        route = {"construction": requested["construction"], "risk_model": requested["risk_model"],
                 "phase3_enabled": requested["phase3_enabled"], "portfolio_basis": "requested_stock_universe"}
        limitations = []
    elif role == "macro_regime":
        route = {"construction": "standalone_macro_benchmark_exposure", "risk_model": "model_c_v204_exposure_mapping",
                 "phase3_enabled": False, "portfolio_basis": "selected_model_benchmark"}
        limitations = ["The standalone Beta row applies its macro exposure to the model benchmark; it is not a 50-stock Alpha/Phase 2 strategy.",
                       "The Universal construction, risk and Phase 3 dropdowns are not applied inside the standalone Beta row."]
    else:
        use_v205 = pre_gamma and requested["risk_model"].strip().lower() == "v205"
        route = {"construction": "native_qpo", "risk_model": ("v205_state_overlay_only_post_qpo" if gamma_policy["risk_scope"] == "state_overlay_only" else "v205_full_supervisor_post_qpo") if use_v205 else "qrm_full_qrs_v1a",
                 "phase3_enabled": False, "portfolio_basis": "requested_stock_universe",
                 "gamma_enabled": pre_gamma, "gamma_decision_lag_sessions": 1 if pre_gamma else 0}
        if gamma_policy:
            route["pre_gamma_variant"] = gamma_policy["variant"]
            route["qpo_overrides"] = gamma_policy["qpo_overrides"]
            route["replay_rejection_policy"] = str(spec.get("pre_gamma_rejection_policy") or "abort")
            requested["pre_gamma_rejection_policy"] = route["replay_rejection_policy"]
            requested["pre_gamma_beta_source"] = str(spec.get("pre_gamma_beta_source") or "supplied")
            route["pre_gamma_beta_source"] = requested["pre_gamma_beta_source"]
            route["beta_conditioning"] = "causal_research_records" if requested["pre_gamma_beta_source"] == "causal_research" else "explicit_supplied_PIT_records"
            route["risk205_research_candidate_mode"] = use_v205 and requested["pre_gamma_beta_source"] == "causal_research"
        limitations = ["Native QPO construction is used; the Universal Phase 2 construction and Phase 3 dropdowns are not applied."]
        if use_v205 and gamma_policy["risk_scope"] == "state_overlay_only":
            limitations.append("This explicitly selected variant applies only the V2.0.5 state overlay to native QPO, not the inherited full V2.0.3 supervisor. It is not labeled full Risk205.")
        elif use_v205:
            limitations.append("Full Risk V2.0.5 is requested after QPO; infeasible books fail explicitly rather than switching to state-overlay-only.")
        elif not pre_gamma:
            limitations.append("This preserves the existing QPO app route: the Universal Risk dropdown is not applied.")
        else:
            limitations.append("Pre-Gamma's additional Universal risk integration supports V2.0.5 only; this selection uses QPO's native QRM/Full-QRS risk path.")
        if pre_gamma and route["replay_rejection_policy"] == "cash_on_qrm_rejection":
            limitations.append("Explicit forced-cash research policy: typed QRM portfolio rejections may execute cash with actual turnover costs. This is a different policy from the original strict variant; data, causality and forecast-contract failures still block.")
        if pre_gamma and route["risk205_research_candidate_mode"]:
            limitations.append("Explicit causal Beta research conditioning and research Risk205 overlay: calibrated research artifacts may be used without production-frozen status; identity, historical cutoffs, data quality and calibration gates remain required.")
    if gamma_policy and gamma_policy["qpo_overrides"].get("gamma_construction_policy"):
        route["construction"] = gamma_policy["qpo_overrides"]["gamma_construction_policy"]
        route["risk_model"] = "v205_" + gamma_policy["risk_scope"] + "_candidate_level_final_qrm"
        route["quantum_execution"] = False
        limitations = [item for item in limitations if not item.startswith(("Native QPO construction", "Full Risk V2.0.5 is requested after", "This explicitly selected variant applies only"))]
        limitations.insert(0,"Bounded classical reference construction uses causal Gamma, QScan seeds and original Full-QRS/QRM, with candidate-level Risk205 and final gates; no quantum/global-optimum claim. Universal Phase 2/3 dropdowns do not apply.")
    return {"requested": requested, "routing_policy": route,
            "execution_status": "completed" if completed else "blocked",
            "applied": route if completed else None, "limitations": limitations}


def _blocked_model_result(model_spec: Any, spec: dict[str, Any], error: Exception | str) -> dict[str, Any]:
    report: dict[str, Any] = {}
    if model_spec.id in PRE_GAMMA_IDS:
        diagnostics = getattr(error, "research_diagnostics", None)
        diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        context = dict(diagnostics.get("control_preparation") or {})
        context.setdefault("gamma_config", dict(spec.get("pre_gamma_config") or {}))
        context.setdefault("gamma_beta_records_supplied", len(spec.get("pre_gamma_beta_records") or []))
        context.setdefault("fundamentals_mode", str(spec.get("fundamentals_mode") or "local_pit"))
        context.setdefault("replay_rejection_policy", str(spec.get("pre_gamma_rejection_policy") or "abort"))
        context.setdefault("pre_gamma_beta_source", str(spec.get("pre_gamma_beta_source") or "supplied"))
        context.setdefault("pre_gamma_beta_preparation", spec.get("_pre_gamma_beta_preparation") or diagnostics.get("beta_preparation"))
        diagnostics["control_preparation"] = context
        method = str(diagnostics.get("selected_covariance_method") or "shrinkage")
        selected = (diagnostics.get("method_results") or {}).get(method) or {}
        report = {"preparation_diagnostics": diagnostics,
                  "pre_gamma_sleeve_coverage": _pre_gamma_sleeve_coverage(diagnostics, selected, context),
                  "diagnostic_status": "blocked; preparation observations are not a completed performance result"}
    return {"model": _control_model_identity(model_spec, spec), "status": "blocked", "backtestable": False,
            "error": {"type": type(error).__name__ if isinstance(error, Exception) else "PrerequisiteUnavailable", "message": str(error)},
            "headline": {"sessions": 0}, "benchmarks": [], "report": report,
            "settings_scope": _model_settings_scope(model_spec, spec, completed=False)}


def _pre_gamma_sleeve_coverage(raw: dict[str, Any], selected: dict[str, Any], qspec: dict[str, Any]) -> dict[str, Any]:
    diagnostics = (raw.get("market_data_provenance") or {}).get("gamma") or {}
    rebalances = selected.get("rebalances") or raw.get("rebalances") or []
    metadata = [((row.get("covariance_provenance") or {}).get("gamma") or {}).get("forecast_metadata") or {} for row in rebalances]
    reported = [row for row in metadata if row]
    config = dict(qspec.get("gamma_config") or {})
    supplied = int(qspec.get("gamma_beta_records_supplied", len(qspec.get("gamma_beta_records") or [])))
    generation = diagnostics.get("optional_sleeve_use") or {}
    beta_generated, sec_generated = generation.get("beta_rows"), generation.get("sec_rows")
    beta_status = "absent_no_records_supplied" if supplied == 0 else "observed" if beta_generated else "absent_in_generated_forecasts" if beta_generated == 0 else "not_evaluated"
    sec_status = "observed" if sec_generated else "absent_in_generated_forecasts" if sec_generated == 0 else "not_evaluated"
    sleeves = diagnostics.get("sleeves") or {}
    missing_sleeves = ["beta"] if beta_status.startswith("absent") else []
    missing_sleeves.extend(name for name in ("sec_quality", "sec_value") if (sleeves.get(name) or {}).get("status") == "unavailable")
    if not sleeves and sec_status.startswith("absent"):
        missing_sleeves.append("sec")
    failure_context = raw.get("failure_context") or {}
    failed_metadata = (failure_context.get("forecast_metadata") or {}).get("forecast_metadata") or {}
    return {
        "forecast_generation": diagnostics,
        "completed_rebalances_before_failure": raw.get("completed_rebalances"),
        "failed_decision_sleeves": ({"decision_date": failure_context.get("decision_date"),
            "beta_status": failed_metadata.get("beta_status"), "sec_status": failed_metadata.get("sec_status"),
            "sec_quality_observed_symbols": failed_metadata.get("sec_quality_observed_symbols"),
            "sec_value_observed_symbols": failed_metadata.get("sec_value_observed_symbols"),
            "not_a_scored_rebalance": True} if failed_metadata else None),
        "feature_readiness": {"all_features_ready": False if missing_sleeves else None,
                              "missing_sleeves": missing_sleeves,
                              "note": "Preparation and scored usage are reported separately. Observing some SEC/Beta inputs does not establish complete all-feature coverage or strategy validity."},
        "scored_rebalances_with_sleeve_metadata": len(reported),
        "scored_rebalances_inspected": len(rebalances),
        "beta": {"requested": True, "required": bool(config.get("require_beta", False)),
                 "source": str(qspec.get("pre_gamma_beta_source") or "supplied"),
                 "preparation": qspec.get("pre_gamma_beta_preparation"),
                 "producer_diagnostics": (qspec.get("pre_gamma_beta_preparation") or {}).get("producer_diagnostics"),
                 "records_supplied": supplied, "coverage_status": beta_status,
                 "used_generated_forecasts": beta_generated,
                 "records_accepted": diagnostics.get("beta_records_accepted"),
                 "records_rejected": diagnostics.get("beta_records_rejected"),
                 "used_scored_rebalances": sum(row.get("beta_status") == "PIT_supplied" for row in reported),
                 "latest_status": reported[-1].get("beta_status") if reported else "not_reported",
                 "ordinary_oos_csv_auto_converted": False,
                 "requirement": "explicit ALFRED PIT source/vintage and training/calibration/selection cutoffs"},
        "sec": {"requested": qspec.get("fundamentals_mode") == "sec_pit" or bool(qspec.get("gamma_sec_cache_directory")),
                "required": bool(config.get("require_sec", False)), "coverage_status": sec_status,
                "used_generated_forecasts": sec_generated,
                "quality": sleeves.get("sec_quality", {"status": "not_reported"}),
                "value": sleeves.get("sec_value", {"status": "not_reported"}),
                "requirement": sleeves.get("sec_requirement"),
                "cache_directory": qspec.get("gamma_sec_cache_directory"),
                "source": diagnostics.get("sec_source"),
                "used_scored_rebalances": sum(row.get("sec_status") in {"available", "partial"} for row in reported),
                "fully_covered_scored_rebalances": sum(row.get("sec_status") == "available" for row in reported),
                "latest_status": reported[-1].get("sec_status") if reported else "not_reported",
                "latest_observed_symbols": reported[-1].get("sec_observed_symbols", []) if reported else [],
                "network_used_by_gamma": False,
                "value_price_basis": "unadjusted historical Close required; adjusted prices never relabeled as raw"},
    }


def _clean_universe(values: list[str]) -> list[str]:
    symbols: list[str] = []
    for value in values:
        symbol = normalize_ticker(value)
        if symbol not in symbols:
            symbols.append(symbol)
    if len(symbols) < 2:
        raise ValueError("Use at least two research symbols")
    return symbols


def _selected_models(spec: dict[str, Any]) -> list[str]:
    raw = spec.get("models")
    if raw is None:
        raw = [spec.get("alpha_model") or "tba8"]
    if isinstance(raw, str):
        raw = [raw]
    selected: list[str] = []
    for value in raw:
        key = str(value or "").strip().lower()
        if not key or key in selected:
            continue
        get_model_spec(key)
        selected.append(key)
    if not selected:
        raise ValueError("Select at least one registered model")
    if len(selected) > _MAX_MODELS:
        raise ValueError(f"Use no more than {_MAX_MODELS} models in one interactive comparison")
    return selected


def _risk_free(index: pd.DatetimeIndex, annual_pct: float) -> pd.Series:
    """Legacy constant helper retained for tests/older callers."""
    return build_control_risk_free(index, mode="manual_constant", annual_pct=annual_pct)[0]


def _fetch_histories(*, user_id: int, symbols: list[str], provider: str, period: str,
                     minimum_bars: int, force_refresh: bool = False) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, Any]]]:
    repository = get_market_repository()
    keys = {} if provider == "yfinance" else decrypted_credentials(user_id)
    histories: dict[str, pd.DataFrame] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        try:
            item = repository.get_history(
                symbol, period=period, minimum_bars=minimum_bars,
                allow_api=provider != "cache_only", force_refresh=force_refresh,
                provider_preference=provider, provider_api_keys=keys,
                allow_platform_provider_keys=False,
            )
        except Exception as exc:
            raise ValueError(f"{symbol}: {exc}") from exc
        frame = item.history.copy().sort_index()
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not set(required).issubset(frame.columns):
            raise ValueError(f"{symbol}: market history is missing required OHLCV fields")
        idx = pd.DatetimeIndex(frame.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        frame.index = idx.normalize()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        numeric = frame[required].apply(pd.to_numeric, errors="coerce")
        finite_mask = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
        complete_mask = numeric.notna().all(axis=1).to_numpy() & finite_mask
        raw_rows = int(len(frame))
        frame = frame.loc[complete_mask].copy()
        dropped_rows = raw_rows - int(len(frame))
        if frame.empty:
            raise ValueError(f"{symbol}: provider returned no complete OHLCV sessions")
        histories[symbol] = frame
        item_metadata = asdict(item.metadata)
        cache_summary = repository.cache_summary(symbol) if hasattr(repository, "cache_summary") else {}
        adjusted = cache_summary.get("all_adjusted")
        item_metadata["adjusted"] = (bool(adjusted) if adjusted is not None else None)
        item_metadata["interactive_rows_received"] = raw_rows
        item_metadata["interactive_incomplete_rows_excluded"] = dropped_rows
        item_metadata["interactive_index_normalized"] = True
        frame.attrs["oryntra_metadata"] = item_metadata
        frame.attrs["price_basis"] = "adjusted_close" if item_metadata.get("adjusted") is True else "unadjusted_close"
        histories[symbol] = frame
        metadata[symbol] = item_metadata
    return histories, metadata


def _aligned_index(histories: dict[str, pd.DataFrame], symbols: list[str]) -> pd.DatetimeIndex:
    common: pd.DatetimeIndex | None = None
    for symbol in symbols:
        idx = pd.DatetimeIndex(histories[symbol].index).tz_localize(None).normalize()
        common = idx if common is None else common.intersection(idx)
    if common is None:
        return pd.DatetimeIndex([])
    return common.sort_values().drop_duplicates()


def _compact_report(report: dict[str, Any]) -> dict[str, Any]:
    risk = report.get("risk_supervisor") or {}
    factor = report.get("factor_model") or {}
    optimizer = report.get("portfolio_optimizer") or {}
    phase3 = report.get("phase3") or {}
    return {
        "engine": report.get("engine"), "engine_version": report.get("engine_version"),
        "config_fingerprint": report.get("config_fingerprint"), "dataset_fingerprint": report.get("dataset_fingerprint"),
        "universe": report.get("universe"), "performance": (report.get("results") or [{}])[0],
        "execution": report.get("execution"), "portfolio_risk": report.get("portfolio_risk"),
        "latest_signals": report.get("latest_signals"),
        "risk_latest": (risk.get("target_audit") or [])[-1] if risk.get("target_audit") else None,
        "factor_latest": (factor.get("decision_audit") or [])[-1] if factor.get("decision_audit") else None,
        "optimizer_latest": (optimizer.get("decision_audit") or [])[-1] if optimizer.get("decision_audit") else None,
        "phase3_latest": (phase3.get("decision_audit") or [])[-1] if phase3.get("decision_audit") else None,
        "methodology": report.get("methodology"),
    }


def _strategy_returns(report: dict[str, Any]) -> pd.Series:
    return pd.Series(
        {pd.Timestamp(row["date"]): float(row["net_return"]) for row in report.get("daily_returns") or []},
        dtype=float,
    ).sort_index()


def _headline(strategy_returns: pd.Series, risk_free: pd.Series) -> dict[str, Any]:
    rf = risk_free.reindex(strategy_returns.index)
    excess = strategy_returns - rf
    vol = float(excess.std(ddof=0))
    sharpe = float(np.sqrt(252) * excess.mean() / vol) if vol > 1e-12 else None
    total = float((1.0 + strategy_returns).prod() - 1.0) if len(strategy_returns) else None
    annual = float((1.0 + total) ** (252.0 / len(strategy_returns)) - 1.0) if total is not None and len(strategy_returns) else None
    curve = (1.0 + strategy_returns).cumprod()
    # Match QPO's reporting convention: initial NAV=1 is a real high-water
    # mark, so a loss in the very first scored session must count as drawdown.
    drawdown = curve.div(np.maximum(1.0, curve.cummax())).sub(1.0) if len(curve) else pd.Series(dtype=float)
    return {
        "sessions": int(len(strategy_returns)),
        "total_return_pct": None if total is None else 100.0 * total,
        "annualized_return_pct": None if annual is None else 100.0 * annual,
        "strategy_excess_sharpe": sharpe,
        "max_drawdown_pct": None if drawdown.empty else 100.0 * float(drawdown.min()),
    }


def _average_positions(compacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not compacts:
        return []
    totals: dict[str, float] = {}
    for compact in compacts:
        for row in (compact.get("portfolio_risk") or {}).get("latest_positions") or []:
            totals[str(row["symbol"])] = totals.get(str(row["symbol"]), 0.0) + float(row["weight_pct"])
    n = float(len(compacts))
    return [
        {"symbol": symbol, "weight_pct": weight / n}
        for symbol, weight in sorted(totals.items(), key=lambda item: abs(item[1]), reverse=True)
        if abs(weight / n) > 1e-8
    ]


def _budget_positions(compact: dict[str, Any], histories: dict[str, pd.DataFrame], budget: float, as_of: str) -> list[dict[str, Any]]:
    if not math.isfinite(float(budget)) or float(budget) <= 0:
        return []
    rows = []
    for item in (compact.get("portfolio_risk") or {}).get("latest_positions") or []:
        symbol = str(item.get("symbol") or "").upper()
        weight_pct = float(item.get("weight_pct") or 0.0)
        if symbol not in histories or weight_pct <= 0:
            continue
        frame = histories[symbol].loc[:pd.Timestamp(as_of)]
        if frame.empty:
            continue
        price = float(frame.iloc[-1]["Close"])
        notional = float(budget) * weight_pct / 100.0
        whole_shares = math.floor(notional / price) if price > 0 else 0
        rows.append({"symbol": symbol, "weight_pct": weight_pct, "reference_close": price,
                     "target_notional": notional, "whole_shares": int(whole_shares),
                     "whole_share_notional": float(whole_shares * price)})
    return rows


def _benchmark_interval_returns(
    histories: dict[str, pd.DataFrame], definition: dict[str, Any], index: pd.DatetimeIndex, *, interval: str
) -> pd.Series:
    """Build weighted benchmark returns for a causal sub-session interval."""
    result = pd.Series(0.0, index=index, dtype=float)
    components = definition.get("components") or {}
    if not components:
        raise ValueError("Benchmark definition has no components")
    for symbol, weight in components.items():
        symbol = str(symbol).upper()
        frame = histories.get(symbol)
        if frame is None:
            raise ValueError(f"Benchmark history is missing {symbol}")
        aligned = frame.reindex(index)
        if interval == "open_to_close":
            values = aligned["Close"].div(aligned["Open"]).sub(1.0)
        elif interval == "prior_close_to_open":
            values = aligned["Open"].div(aligned["Close"].shift(1)).sub(1.0)
            values.iloc[0] = 0.0
        else:
            raise ValueError(f"Unknown benchmark interval: {interval}")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"Benchmark {definition.get('spec', '')} has incomplete {interval} history for {symbol}")
        result = result.add(values * float(weight), fill_value=0.0)
    result.name = f"{definition.get('id')}_{interval}"
    return result


def _beta_exposure_series(
    beta_states: pd.DataFrame, sessions: pd.DatetimeIndex, *, require_calibrated: bool = True
) -> tuple[pd.Series, dict[str, Any]]:
    """Translate Model C probabilities into its bounded standalone research exposure.

    This is intentionally independent of Alpha/TBA/Minerva.  It uses the same
    bounded Beta macro scalar used by Universal Risk V2.0.4, but removes the
    production-freeze requirement only inside the interactive research path.
    """
    states = beta_states.copy().sort_index()
    states.index = pd.DatetimeIndex(states.index).tz_localize(None).normalize()
    control = V204BetaControlConfig(
        require_production_frozen=False, require_calibrated=require_calibrated,
        research_candidate_mode=True, minimum_additional_gross_change=0.0,
    )
    exposure = pd.Series(1.0, index=sessions, dtype=float, name="beta_model_c_exposure")
    persistence = 0
    previous = np.array([1.0], dtype=float)
    available = triggered = blocked = 0
    reasons: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    quality_scales: list[float] = []
    neutral_sources: dict[str, int] = {}
    first_state = None
    last_state = None
    for session in sessions:
        eligible = states.loc[states.index <= session]
        if eligible.empty:
            reasons["beta_state_unavailable"] = reasons.get("beta_state_unavailable", 0) + 1
            continue
        row = eligible.iloc[-1].to_dict()
        as_of = pd.Timestamp(eligible.index[-1]).normalize()
        first_state = as_of if first_state is None else first_state
        last_state = as_of
        row["as_of_session"] = str(as_of.date())
        for key, value in (states.attrs or {}).items():
            row.setdefault(key, value)
        modifier = beta_regime_modifier_v204(
            row, control=control, previous_persistence_rebalances=persistence,
            current_session=session, v203_predictive_triggered=False,
        )
        available += int(bool(modifier.get("available")))
        persistence = int(modifier.get("persistence_rebalances", 0) or 0)
        candidate, audit = apply_beta_overlay_v204(
            np.array([1.0], dtype=float), modifier, current_weights=previous, control=control,
        )
        current = float(candidate[0])
        exposure.loc[session] = current
        previous = candidate
        if modifier.get("triggered"):
            triggered += 1
        if modifier.get("reason") == "beta_gate_blocked":
            blocked += 1
            for blocker in modifier.get("blockers") or []:
                key = str(blocker)
                blocker_counts[key] = blocker_counts.get(key, 0) + 1
        if modifier.get("research_data_quality_scale") is not None:
            try:
                quality_scales.append(float(modifier.get("research_data_quality_scale")))
            except Exception:
                pass
        source = modifier.get("neutral_stress_source")
        if source:
            key = str(source)
            neutral_sources[key] = neutral_sources.get(key, 0) + 1
        reason = str(audit.get("reason") or modifier.get("reason") or audit.get("selected") or "none")
        reasons[reason] = reasons.get(reason, 0) + 1
    return exposure, {
        "sessions": int(len(sessions)),
        "state_available_sessions": int(available),
        "state_coverage_pct": (100.0 * available / len(sessions)) if len(sessions) else 0.0,
        "triggered_sessions": int(triggered),
        "blocked_sessions": int(blocked),
        "minimum_exposure": float(exposure.min()) if len(exposure) else None,
        "mean_exposure": float(exposure.mean()) if len(exposure) else None,
        "first_state": None if first_state is None else str(first_state.date()),
        "last_state": None if last_state is None else str(last_state.date()),
        "reason_counts": reasons,
        "blocker_counts": blocker_counts,
        "mean_research_data_quality_scale": float(np.mean(quality_scales)) if quality_scales else None,
        "neutral_stress_sources": neutral_sources,
        "mapping": "Model C calibrated probabilities -> candidate-prior-relative bounded research V2.0.4 gross scalar, attenuated by historical data quality; no Alpha/TBA/Minerva forecast or weights consumed",
        "research_overlay_mode": "continuous_above_candidate_prior_neutral_stress_quality_scaled",
        "production_thresholds_unchanged": True,
    }


def _beta_strategy_returns(
    histories: dict[str, pd.DataFrame], definition: dict[str, Any], sessions: pd.DatetimeIndex,
    beta_states: pd.DataFrame, risk_free: pd.Series,
) -> tuple[pd.Series, pd.Series, dict[str, Any]]:
    """Create a causal, full-session standalone Model C research return stream.

    Previous exposure earns prior-close->current-open movement; the newly known
    current-open Beta exposure earns current-open->close movement.  This keeps
    Beta independent while making its macro timing comparable in the normal
    model matrix.
    """
    exposure, audit = _beta_exposure_series(beta_states, sessions)
    overnight = _benchmark_interval_returns(histories, definition, sessions, interval="prior_close_to_open")
    intraday = _benchmark_interval_returns(histories, definition, sessions, interval="open_to_close")
    previous_exposure = exposure.shift(1).fillna(1.0)
    rf = risk_free.reindex(sessions).fillna(0.0)
    # Exact two-stage close->open then open->close account factor.  The daily cash
    # accrual is assigned to the open->close stage; no future information enters.
    stage_overnight = 1.0 + previous_exposure * overnight
    stage_intraday = 1.0 + exposure * intraday + (1.0 - exposure) * rf
    strategy = stage_overnight.mul(stage_intraday).sub(1.0).rename("beta_model_c_strategy_return")
    audit.update({
        "return_convention": "prior exposure earns prior-close->current-open; current-open Beta decision sets exposure for current-open->close; residual cash earns the declared daily risk-free rate",
        "benchmark_spec": definition.get("spec"),
        "causal": True,
    })
    return strategy, exposure, audit


def _beta_model_result(
    model_spec, status: dict[str, Any], *, histories: dict[str, pd.DataFrame],
    model_benchmark: dict[str, Any], comparison_returns_full: dict[str, pd.Series],
    comparisons: list[dict[str, Any]], common: pd.DatetimeIndex, evaluation_index: pd.DatetimeIndex,
    beta_states: pd.DataFrame, risk_free: pd.Series, portfolio_budget: float,
) -> tuple[dict[str, Any], pd.Series, dict[str, Any]]:
    if beta_states is None or beta_states.empty:
        raise ValueError("Beta / Model C has no research OOS state panel. Train the research candidate in Models first.")
    full_returns, exposure, audit = _beta_strategy_returns(
        histories, model_benchmark, common, beta_states, risk_free,
    )
    returns = full_returns.reindex(evaluation_index)
    comparison_returns = {key: value.reindex(returns.index) for key, value in comparison_returns_full.items()}
    latest_exposure = float(exposure.reindex(evaluation_index).iloc[-1])
    latest_positions = [
        {"symbol": str(symbol).upper(), "weight_pct": 100.0 * latest_exposure * float(weight)}
        for symbol, weight in (model_benchmark.get("components") or {}).items()
    ]
    budget_positions = []
    for row in latest_positions:
        symbol = row["symbol"]
        frame = histories.get(symbol)
        if frame is None or frame.loc[:evaluation_index[-1]].empty:
            continue
        price = float(frame.loc[:evaluation_index[-1]].iloc[-1]["Close"])
        notional = portfolio_budget * float(row["weight_pct"]) / 100.0
        budget_positions.append({
            **row, "reference_close": price, "target_notional": notional,
            "whole_shares": int(math.floor(notional / price)) if price > 0 else 0,
            "whole_share_notional": float((math.floor(notional / price) if price > 0 else 0) * price),
        })
    compact = {
        "engine": "cqc_beta_model_c", "engine_version": "1.2.2-research-matrix",
        "model_role": "macro_regime", "beta": status,
        "portfolio_risk": {"latest_positions": latest_positions},
        "budget_positions": budget_positions, "portfolio_budget": portfolio_budget,
        "beta_strategy": audit, "latest_signals": {},
        "methodology": {
            "note": "Standalone Beta research row. Model C does not consume Alpha/TBA/Minerva forecasts or portfolio weights; its bounded macro exposure is applied only to the selected model benchmark so Beta can be compared in the ordinary research matrix.",
            "production_separation": "Interactive research may consume the trained candidate. Production V2.0.4 remains separately gated and fail-closed.",
        },
    }
    result = {
        "model": model_spec.public_dict(),
        "headline": _headline(returns, risk_free),
        "benchmarks": evaluate_many(returns, comparison_returns, risk_free, comparisons),
        "report": compact,
        "backtestable": True,
    }
    return result, returns, compact


def _qoptima_control_model_result(
    user_id: int,
    model_spec: Any,
    spec: dict[str, Any],
    *,
    universe: list[str],
    comparisons: list[dict[str, Any]],
    comparison_returns_full: dict[str, pd.Series],
    risk_free: pd.Series,
    portfolio_budget: float,
    histories: dict[str, pd.DataFrame],
    evaluation_end: str,
    evaluation_index: pd.DatetimeIndex | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], pd.Series, dict[str, Any]]:
    """Adapt QOptima's self-contained QPO walk-forward into Control's model row."""
    pre_gamma = model_spec.id in PRE_GAMMA_IDS
    gamma_policy = pre_gamma_policy(model_spec.id) if pre_gamma else None
    if pre_gamma and spec.get("pre_gamma_beta_source") == "causal_research" and not spec.get("_pre_gamma_beta_preparation"):
        raise ValueError("Causal Pre-Gamma Beta generation must complete before entering the QPO adapter")
    if gamma_policy and gamma_policy["requires_v205"] and str(spec.get("risk_model") or "v203").strip().lower() != "v205":
        raise ValueError(f"{model_spec.label} requires Risk V2.0.5 selected; refusing an unlabeled risk-policy change")
    if len(universe) < 5:
        raise ValueError("QOptima V1A-QPO needs at least five symbols in the Backtest universe")
    period = str(spec.get("evaluation_period") or "1y").strip().lower()
    rf_eval = risk_free.dropna()
    annual_rf = 0.0
    if len(rf_eval):
        daily = float(rf_eval.mean())
        annual_rf = float((1.0 + daily) ** 252 - 1.0) if daily > -1 else 0.0
    # QPO loads its own adjusted-price panel. Bind that replay to Control's
    # shared evaluation window rather than silently scoring later sessions
    # for which the comparison benchmark or risk-free series may be absent.
    # run_qoptima_backtest_overlay retains lookback history before this start.
    qspec = {
        "universe": list(universe),
        "provider": str(spec.get("provider") or "auto"),
        "evaluation_period": period,
        "evaluation_end": evaluation_end,
        "holdout_sessions": 0,
        "lookback_sessions": 252,
        "rebalance_sessions": 21,
        "max_name_weight": 0.30,
        "risk_aversion": 3.0,
        "turnover_penalty": 0.20,
        "risk_free_rate": annual_rf,
        "transaction_cost_bps": 5.0,
        "require_adjusted_prices": True,
        "covariance_methods": ["shrinkage"],
        "run_covariance_benchmarks": False,
        "selected_covariance_method": "shrinkage",
        "covariance_parameters": {"estimator": "ledoit_wolf"},
        "alpha_values": [1.0],
        "run_alpha_frontier": False,
        "true_qoptima_replay": True,
        "replay_bits_per_asset": 2,
        "replay_qscan_assets": 6,
        "replay_backend": "exact",
        "seed": 560205,
        "full_qrs_primary_qaoa": True,
        "full_qrs_beta": 0.10,
        "full_qrs_bootstrap_reps": 256,
        "full_qrs_quantile_confidence": 0.90,
        "full_qrs_exact_variable_cap": 12,
        "full_qrs_qaoa_reps": 2,
        "full_qrs_qaoa_evaluations": 48,
        "full_qrs_decision_mode": "exact_reference",
        "simulator_audit_every_rebalances": 6,
        "run_ablations": False,
        "run_component_ablations": False,
        "fragility_downside_weight": 0.0,
        "fragility_correlation_weight": 0.0,
        "fragility_regime_weight": 0.0,
        "start_from_cash": True,
        "target_gross_override": 1.0,
        "v1a_enabled": True,
        "v1a_auto_train": True,
        "v1a_initial_train_sessions": 1512,
        "v1a_refit_sessions": 252,
        "v1a_training_max_epochs": 300,
        "v1a_training_patience": 35,
        "v1a_gate_b_block_sessions": 21,
        "v1a_gate_b_bootstrap_replications": 1000,
        "v1a_gate_b_alpha": 0.05,
        "v1a_gate_b_effect_size": 0.0,
    }
    if evaluation_index is not None and len(evaluation_index):
        qspec["evaluation_start"] = str(pd.Timestamp(evaluation_index[0]).date())
    if pre_gamma:
        from .qoptima.gamma_integration import validate_gamma_config
        qspec.update(gamma_enabled=True, gamma_decision_lag_sessions=1,
                     replay_rejection_policy=_pre_gamma_rejection_policy(spec),
                     gamma_config=validate_gamma_config(spec.get("pre_gamma_config")), gamma_min_error_records=2,
                     gamma_beta_records=list(spec.get("pre_gamma_beta_records") or []),
                     gamma_risk205_enabled=str(spec.get("risk_model") or "v203").strip().lower() == "v205",
                     gamma_risk205_scope=gamma_policy["risk_scope"], gamma_risk205_config={})
        qspec.update(gamma_policy["qpo_overrides"])
        qspec["gamma_config"].update(gamma_policy.get("gamma_config_overrides", {}))
        if spec.get("pre_gamma_beta_source") == "causal_research":
            qspec["gamma_risk205_config"] = {
                "v205_research_candidate_mode": True,
                "v204_require_production_frozen_beta": False,
                "v204_require_calibrated_beta": True,
            }
        if str(spec.get("fundamentals_mode") or "local_pit").lower() == "sec_pit":
            from .sec_pit import cache_root
            qspec["gamma_sec_cache_directory"] = str(cache_root())
            # Preserve the existing history's price-basis metadata. Gamma may
            # use cached quality facts, but value ratios remain unavailable
            # unless a history explicitly attests unadjusted historical Close.
            qspec["gamma_sec_histories"] = histories
        # A retry keeps the same server-owned namespace, while the engine must
        # validate its complete prepared-input manifest before loading state.
        qspec["replay_checkpoint_directory"] = str(_pre_gamma_replay_directory(
            user_id, model_spec.id, spec, universe, qspec.get("evaluation_start"), evaluation_end))
        qspec["replay_checkpoint_resume"] = True
    try:
        if progress_callback is None:
            raw = run_qoptima_backtest_overlay(user_id, qspec)
        else:
            raw = run_qoptima_backtest_overlay(user_id, qspec,
                progress_callback=lambda event: _notify_research_progress(progress_callback, event))
    except Exception as exc:
        if pre_gamma:
            # Keep the original exception and its engine diagnostics. A failed
            # replay must not erase observed preparation or become cash here.
            diagnostics = getattr(exc, "research_diagnostics", None)
            diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
            diagnostics["control_preparation"] = {
                "gamma_config": qspec["gamma_config"],
                "gamma_beta_records_supplied": len(qspec["gamma_beta_records"]),
                "gamma_sec_cache_directory": qspec.get("gamma_sec_cache_directory"),
                "fundamentals_mode": str(spec.get("fundamentals_mode") or "local_pit"),
                "replay_rejection_policy": qspec["replay_rejection_policy"],
                "pre_gamma_beta_source": str(spec.get("pre_gamma_beta_source") or "supplied"),
                "pre_gamma_beta_preparation": spec.get("_pre_gamma_beta_preparation"),
                "gamma_risk205_config": qspec["gamma_risk205_config"],
                "replay_checkpoint_directory": qspec["replay_checkpoint_directory"],
                "replay_checkpoint_resume": qspec["replay_checkpoint_resume"],
            }
            exc.research_diagnostics = diagnostics
        raise
    method = str(raw.get("selected_covariance_method") or "shrinkage")
    selected = (raw.get("method_results") or {}).get(method) or {}
    rows = ((selected.get("daily_returns") or {}).get("qoptima") or [])
    return_dates = pd.DatetimeIndex([pd.Timestamp(item["date"]) for item in rows])
    if return_dates.tz is not None:
        return_dates = return_dates.tz_localize(None)
    returns = pd.Series([float(item["net_return"]) for item in rows], index=return_dates.normalize(), dtype=float).sort_index()
    if returns.index.has_duplicates:
        raise ValueError("QOptima V1A-QPO produced duplicate daily return sessions; cannot score an ambiguous path")
    if not np.isfinite(returns.to_numpy()).all():
        raise ValueError("QOptima V1A-QPO produced non-finite realized daily returns")
    returns = returns.loc[:pd.Timestamp(evaluation_end).normalize()]
    if evaluation_index is not None and len(evaluation_index):
        returns = returns.loc[pd.Timestamp(evaluation_index[0]).normalize():]
    if returns.empty:
        raise ValueError("QOptima V1A-QPO produced no realized daily return path")
    latest_rebalance = (raw.get("rebalances") or [])[-1] if raw.get("rebalances") else {}
    latest_positions = [
        {"symbol": str(row.get("symbol")), "weight_pct": float(row.get("weight", 0.0)) * 100.0}
        for row in (latest_rebalance.get("qoptima_target_weights") or [])
        if float(row.get("weight", 0.0)) > 1e-12
    ]
    compact = {
        "engine": "CQC_PRE_GAMMA" if pre_gamma else "CQC_QOPTIMA",
        "engine_version": "0.4.3-feature-qa" if pre_gamma else "0.4-V1A-QRS-v1.6",
        "qoptima_model": True,
        "performance": raw.get("true_qoptima_walkforward"),
        "portfolio_risk": {"latest_positions": latest_positions},
        "latest_positions": latest_positions,
        "risk_latest": {
            "model": ("Pre-Gamma QPO + Risk205 state overlay only" if qspec.get("gamma_risk205_scope") == "state_overlay_only" else "Pre-Gamma QPO + full Risk V2.0.5") if pre_gamma and qspec["gamma_risk205_enabled"] else "QOptima QRM + Full-QRS",
            "note": ("Explicit state-overlay-only variant; inherited full V2.0.3 supervision is not applied." if qspec.get("gamma_risk205_scope") == "state_overlay_only" else "Full Risk V2.0.5 is applied after native QPO, not as a jointly optimized Phase 2 strategy.") if pre_gamma and qspec["gamma_risk205_enabled"] else "Native QPO risk path; Universal construction is not applied inside this model row.",
        },
        "qoptima": {
            "v1a": raw.get("v1a"),
            "rebalance_count": raw.get("rebalance_count"),
            "selected_covariance_method": raw.get("selected_covariance_method"),
            "latest_rebalance": latest_rebalance,
            "research_only": raw.get("research_only"),
            "historical_qpu_execution_claimed": raw.get("real_qpu_executed", False),
            "gamma": (raw.get("market_data_provenance") or {}).get("gamma"),
            "forecast_contract": raw.get("forecast_contract"),
            "research_variant": selected.get("research_variant"),
            "evaluation_coverage": selected.get("evaluation_coverage"),
            "legacy_cash_reset_audit": selected.get("legacy_cash_reset_audit"),
            "rejection_policy_audit": selected.get("rejection_policy_audit"),
            "replay_checkpoint": selected.get("replay_checkpoint"),
            "latest_component_risk_diagnostics": (latest_rebalance.get("gamma_risk205") or {}).get("component_risk_diagnostics"),
            "run_manifest": raw.get("run_manifest"),
            "replay_rejection_policy": qspec.get("replay_rejection_policy") if pre_gamma else "legacy_native_behavior",
            "gamma_risk205_config": qspec.get("gamma_risk205_config") if pre_gamma else None,
        },
        "methodology": {
            "role": "qpo_optimizer",
            "path": "Causal Gamma forecasts -> V1A Full-QRS -> native exact QPO -> QRM" + ((" -> Risk205 state overlay only" if qspec.get("gamma_risk205_scope") == "state_overlay_only" else " -> full Risk V2.0.5") if qspec.get("gamma_risk205_enabled") else "") if pre_gamma else "QScan -> V1A Full-QRS -> exact finite-domain QPO -> QRM; selected local QAOA simulator audits only",
            "risk_supervisor_dropdown_scope": ("state_overlay_only_post_qpo" if qspec.get("gamma_risk205_scope") == "state_overlay_only" else "full_v205_post_qpo") if qspec.get("gamma_risk205_enabled") else "not_applied_to_qoptima_row",
            "construction_dropdown_scope": "not_applied_native_qpo_construction",
            "benchmark_evaluation": "Same daily excess-return CAPM/HAC regression as other Control rows; only finite aligned sessions are used, with exclusions and unavailable reasons disclosed.",
        },
    }
    if pre_gamma and qspec.get("gamma_construction_policy", "legacy_exact") != "legacy_exact":
        compact["engine_version"] = "0.5.0-model-repair"
        compact["risk_latest"]["model"] = "Pre-Gamma bounded classical joint Full-QRS reference + " + qspec["gamma_risk205_scope"]
        compact["methodology"]["path"] = "Causal Gamma -> QScan-seeded bounded classical candidate domain -> pre-QRM -> candidate-level Risk205 -> final QRM -> final Full-QRS selection"
        compact["methodology"]["construction_policy"] = qspec["gamma_construction_policy"]
        compact["methodology"]["risk_supervisor_dropdown_scope"] = "candidate_level_Risk205_then_final_QRM"
        compact["methodology"]["construction_dropdown_scope"] = "not_applied_bounded_classical_reference_construction"
        compact["methodology"]["quantum_execution"] = False
        compact["methodology"]["global_optimality_claim"] = False
        compact["risk_latest"]["note"] = "Risk205 is applied to candidate finalists with a frozen prior state; final gates and Full-QRS determine the winner. This is a separately named bounded classical research reference."
    if pre_gamma:
        compact["pre_gamma_sleeve_coverage"] = _pre_gamma_sleeve_coverage(raw, selected, {
            **qspec, "pre_gamma_beta_source": str(spec.get("pre_gamma_beta_source") or "supplied"),
            "pre_gamma_beta_preparation": spec.get("_pre_gamma_beta_preparation"),
        })
        compact["methodology"]["replay_rejection_policy"] = qspec["replay_rejection_policy"]
        compact["methodology"]["policy_label"] = "forced-cash research policy" if qspec["replay_rejection_policy"] == "cash_on_qrm_rejection" else "strict abort on rejection"
        compact["methodology"]["beta_conditioning"] = str(spec.get("pre_gamma_beta_source") or "supplied")
        if qspec.get("gamma_risk205_enabled") and spec.get("pre_gamma_beta_source") == "causal_research":
            compact["risk_latest"]["note"] += " Explicit research Risk205 overlay consumes calibrated causal Beta research records; production-frozen status is not claimed."
    compact["budget_positions"] = _budget_positions(compact, histories, portfolio_budget, evaluation_end)
    comparison_returns = {key: value.reindex(returns.index) for key, value in comparison_returns_full.items()}
    promotion = evaluate_promotion_gates(
        returns, next(iter(comparison_returns.values())).reindex(returns.index), risk_free.reindex(returns.index),
        universe_size=len(universe),
    ) if comparison_returns else {}
    row = {
        "model": _control_model_identity(model_spec, spec),
        "headline": _headline(returns, risk_free),
        "benchmarks": evaluate_many(returns, comparison_returns_full, risk_free, comparisons),
        "report": compact,
        "promotion_gates": promotion,
    }
    return row, returns, compact


def run_interactive_backtest(user_id: int, spec: dict[str, Any], *, progress_callback: Callable[[dict[str, Any]], None] | None = None,
                             row_checkpoint_callback: Callable[[dict[str, Any], dict[str, Any]], None] | None = None) -> dict[str, Any]:
    def emit(stage: str, message: str, **details) -> None:
        _notify_research_progress(progress_callback, {"stage": stage, "message": message, **details})

    universe = _clean_universe(list(spec.get("universe") or []))
    emit("shared_preparation", "Preparing shared Control inputs")
    selected_models = _selected_models(spec)
    evaluation_period = str(spec.get("evaluation_period") or "1y").strip().lower()
    if evaluation_period not in _EVAL_SESSIONS:
        raise ValueError("Choose 3mo, 6mo, 1y, 2y, 5y, or 10y")
    provider = str(spec.get("provider") or "auto").strip().lower()
    model_benchmark = parse_benchmark_spec(str(spec.get("model_benchmark") or "SPY/QQQ/IWM/VXUS"))
    comparisons = parse_benchmark_list(spec.get("comparison_benchmarks") or [model_benchmark["spec"]])
    all_symbols = list(universe)
    for definition in [model_benchmark, *comparisons]:
        for symbol in sorted(benchmark_symbols(definition)):
            if symbol not in all_symbols:
                all_symbols.append(symbol)
    analysis_symbols = list(all_symbols)
    beta_requested = any(getattr(get_model_spec(model_id), "role", "portfolio") == "macro_regime" for model_id in selected_models)
    qoptima_requested = any(getattr(get_model_spec(model_id), "role", "portfolio") == "qpo_optimizer" for model_id in selected_models)
    universal_portfolio_requested = any(getattr(get_model_spec(model_id), "role", "portfolio") == "portfolio" for model_id in selected_models)
    risk_key = str(spec.get("risk_model") or "v203").strip().lower()
    # The Universal Risk dropdown applies only to Universal portfolio-model rows.
    # Beta is a standalone macro row and QOptima is a self-contained QPO/QRM engine.
    # Do not train Beta/V1A risk artifacts merely because the UI risk selector is
    # set to V2.0.4/V2.0.5 when every selected model is non-Universal.
    beta_risk_requested = universal_portfolio_requested and risk_key in {"v204", "v205"}
    v205_risk_requested = universal_portfolio_requested and risk_key == "v205"
    if beta_requested or beta_risk_requested:
        for symbol in required_market_tickers():
            if symbol not in all_symbols:
                all_symbols.append(symbol)

    eval_sessions = _EVAL_SESSIONS[evaluation_period]
    v205_warmup = 1512 if v205_risk_requested else 0
    minimum_bars = min(5200, max(420, eval_sessions + 380, eval_sessions + v205_warmup + 32))
    fetch_period = "all" if v205_risk_requested else _FETCH_PERIOD[evaluation_period]
    emit("shared_market_data", "Loading shared stock and benchmark histories", symbols_total=len(analysis_symbols))
    histories, metadata = _fetch_histories(
        user_id=user_id, symbols=analysis_symbols, provider=provider,
        period=fetch_period, minimum_bars=minimum_bars,
        force_refresh=bool(spec.get("force_refresh", False)),
    )
    beta_market_error = None
    auxiliary_symbols = [symbol for symbol in all_symbols if symbol not in analysis_symbols]
    if auxiliary_symbols:
        try:
            extra_histories, extra_metadata = _fetch_histories(
                user_id=user_id, symbols=auxiliary_symbols, provider=provider,
                period=fetch_period, minimum_bars=minimum_bars,
                force_refresh=bool(spec.get("force_refresh", False)),
            )
            histories.update(extra_histories)
            metadata.update(extra_metadata)
        except Exception as exc:
            # Auxiliary Beta inputs do not belong to QPO's requested stock
            # universe. Preserve that row if Beta's separate prerequisites fail.
            beta_market_error = str(exc)
    common = _aligned_index(histories, analysis_symbols)
    required = eval_sessions + (1512 if v205_risk_requested else 253)
    if len(common) < required:
        raise ValueError(
            f"Only {len(common)} aligned sessions are available; this {evaluation_period} study needs at least {required}. "
            "Try a shorter period or a universe with longer shared history."
        )
    evaluation_index = common[-eval_sessions:]
    evaluation_start, evaluation_end = str(evaluation_index[0].date()), str(evaluation_index[-1].date())
    aligned = {symbol: histories[symbol].reindex(common).copy() for symbol in analysis_symbols}
    if any(frame[["Open", "High", "Low", "Close", "Volume"]].isna().any().any() for frame in aligned.values()):
        raise ValueError("Aligned market panel contains missing OHLCV values; no silent imputation is allowed")

    model_benchmark_returns = build_benchmark_returns(aligned, model_benchmark, common)
    # Older Control/API clients had only a manual annual-percent field. Preserve
    # that contract when risk_free_mode is omitted; Control 5.1 explicitly sends
    # fred_dgs3mo by default.
    risk_free_mode = str(spec.get("risk_free_mode") or "manual_constant").strip().lower()
    fred_api_key = None
    if risk_free_mode == "fred_dgs3mo":
        try:
            fred_api_key = decrypted_credentials(user_id).get("fred")
        except Exception:
            fred_api_key = None
    risk_free, risk_free_metadata = build_control_risk_free(
        common, mode=risk_free_mode, annual_pct=float(spec.get("risk_free_annual_pct", 0.0)),
        force_refresh=bool(spec.get("force_refresh_risk_free", False)),
        fred_api_key=fred_api_key,
    )
    comparison_returns_full = {definition["id"]: build_benchmark_returns(aligned, definition, common) for definition in comparisons}
    research_histories = {symbol: aligned[symbol] for symbol in universe}

    emit("shared_fundamentals", "Preparing point-in-time fundamental inputs")
    fundamentals_mode = str(spec.get("fundamentals_mode") or "local_pit").strip().lower()
    if fundamentals_mode not in {"off", "sec_pit", "local_pit"}:
        raise ValueError("fundamentals_mode must be off, sec_pit, or local_pit")
    if fundamentals_mode == "off":
        quality_panel = None
        corporate_metadata = {"mode": "off", "coverage_pct": 0.0, "facts_used": 0, "missing_values_preserved": True}
    elif fundamentals_mode == "sec_pit":
        from .sec_pit import cached_factor_panels
        sec_result = cached_factor_panels(universe, common, histories=research_histories)
        quality_panel = sec_result["quality"] if sec_result["quality"].notna().any().any() else None
        corporate_metadata = sec_result["metadata"]
    else:
        corporate_quality, corporate_metadata = get_corporate_repository().factor_panel(universe, common, fill_missing=False)
        quality_panel = corporate_quality if corporate_quality.notna().any().any() else None
        corporate_metadata = {"mode": "local_pit", **corporate_metadata}

    emit("shared_beta", "Preparing native Beta inputs when requested")
    beta_states = None
    beta_status: dict[str, Any] = {"requested": False}
    beta_prerequisite_error = None
    if beta_requested or beta_risk_requested:
        try:
            beta_states, beta_panel_status = research_beta_state_panel(common, user_id=user_id)
            beta_status = {"requested": True, **beta_panel_status}
        except Exception as exc:
            beta_prerequisite_error = str(exc)
            beta_status = {"requested": True, "error": str(exc)}
        if beta_market_error:
            beta_status["auxiliary_market_data_error"] = beta_market_error
        if beta_risk_requested and (beta_states is None or beta_states.empty):
            fred_note = "FRED key is saved" if beta_status.get("fred_key_configured") else "save the FRED key in Settings first"
            beta_prerequisite_error = (
                "Risk V2.0.4/2.0.5 research requires a trained Beta / Model C candidate with an audited OOS state panel; "
                f"{fred_note}, then sync/train Beta under Models. The selected Beta-state risk version will not silently fall back to V2.0.3 in the Research page."
            )
    if beta_requested:
        try:
            current_status = beta_current_status_from_histories(histories, universe, user_id=user_id)
            # Preserve the research-panel contract while enriching it with the current state.
            beta_status = {**beta_status, **current_status, "requested": True}
        except Exception as exc:
            beta_status["current_state_error"] = str(exc)
        if beta_states is not None and not beta_states.empty:
            beta_status["risk_v204_research_consumable"] = True

    emit("shared_v1a", "Preparing shared V1A schedule when requested")
    v1a_artifact_schedule: list[dict[str, Any]] = []
    v205_v1a_status: dict[str, Any] = {"requested": bool(v205_risk_requested), "artifact_count": 0}
    v205_prerequisite_error = None
    if v205_risk_requested and beta_prerequisite_error:
        v205_prerequisite_error = beta_prerequisite_error
        v205_v1a_status.update(status="blocked", reason=beta_prerequisite_error)
    elif v205_risk_requested:
        try:
            close_panel = pd.DataFrame({symbol: research_histories[symbol]["Close"] for symbol in universe}).reindex(common)
            state_returns = close_panel.pct_change(fill_method=None).iloc[1:].replace([np.inf, -np.inf], np.nan).dropna(how="any")
            v1a_artifact_schedule, schedule_meta = build_causal_v1a_artifact_schedule(
                state_returns,
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end,
                output_directory=None,
                horizon_sessions=21,
                initial_train_sessions=1512,
                refit_sessions=252,
                seed=2052026,
                max_epochs=300,
                patience=35,
                progress_callback=(lambda event: _notify_research_progress(progress_callback, {**event, "scope": "shared_v1a"})) if progress_callback is not None else None,
            )
            v205_v1a_status = {"requested": True, **schedule_meta}
        except Exception as exc:
            v205_prerequisite_error = str(exc)
            v205_v1a_status.update(status="blocked", reason=str(exc))

    portfolio_budget = float(spec.get("portfolio_budget") or 100_000.0)
    if not math.isfinite(portfolio_budget) or portfolio_budget < 100:
        raise ValueError("portfolio_budget must be at least 100")
    model_results: list[dict[str, Any]] = []
    series: dict[str, pd.Series] = {}
    compacts: list[dict[str, Any]] = []
    checkpoint_warnings: list[dict[str, Any]] = []
    shared_checkpoint_context = {
        "evaluation": {"start": evaluation_start, "end": evaluation_end, "sessions": int(eval_sessions)},
        "requested_models": list(selected_models), "risk_free": risk_free_metadata,
        "data": metadata, "point_in_time_corporate": corporate_metadata,
    }

    def checkpoint_row(row: dict[str, Any], context: dict[str, Any]) -> None:
        if row.get("status") != "blocked":
            row["status"] = "completed"
            row["settings_scope"] = _model_settings_scope(get_model_spec(row["model"]["id"]), spec, completed=True)
        if row_checkpoint_callback is not None:
            try:
                row_checkpoint_callback(deepcopy(row), deepcopy({**shared_checkpoint_context, **context}))
            except Exception as exc:
                # A checkpoint I/O failure cannot turn a computed row into a
                # different model outcome. Preserve and prominently report it.
                checkpoint_warnings.append({"model_id": row["model"]["id"], "type": type(exc).__name__, "message": str(exc)})
                emit("row_checkpoint_failed", "Could not persist this row checkpoint; see final checkpoint warnings", error_type=type(exc).__name__, **context)
    pre_gamma_spec = spec
    pre_gamma_beta_error = None
    if any(model_id in PRE_GAMMA_IDS for model_id in selected_models):
        try:
            emit("gamma_beta_preparation", "Preparing the explicitly selected Gamma Beta source")
            pre_gamma_spec = _prepare_control_pre_gamma_beta(spec, universe, evaluation_start, evaluation_end,
                progress_callback=progress_callback)
            emit("gamma_beta_prepared", "Gamma Beta source preparation returned")
        except Exception as exc:
            pre_gamma_beta_error = exc
    for model_index, model_id in enumerate(selected_models, start=1):
        model_spec = get_model_spec(model_id)
        model_run_spec = pre_gamma_spec if model_id in PRE_GAMMA_IDS else spec
        row_context = {"model_id": model_id, "model_index": model_index, "models_total": len(selected_models)}
        emit("model_started", "Starting model row", **row_context)
        def row_progress(event, context=row_context):
            _notify_research_progress(progress_callback, {**event, **context})
        try:
            if model_id in PRE_GAMMA_IDS and pre_gamma_beta_error is not None:
                raise pre_gamma_beta_error
            model_role = getattr(model_spec, "role", "portfolio")
            if model_role == "qpo_optimizer":
                q_row, q_returns, q_compact = _qoptima_control_model_result(
                    user_id, model_spec, model_run_spec, universe=universe, comparisons=comparisons,
                    comparison_returns_full=comparison_returns_full, risk_free=risk_free,
                    portfolio_budget=portfolio_budget, histories=research_histories, evaluation_end=evaluation_end,
                    evaluation_index=evaluation_index,
                    progress_callback=row_progress if progress_callback is not None else None,
                )
                model_results.append(q_row)
                series[model_id] = q_returns
                compacts.append(q_compact)
                checkpoint_row(q_row, row_context)
                emit("model_completed", "Model row returned successfully", **row_context)
                continue
            if model_role != "portfolio":
                beta_row, beta_returns, beta_compact = _beta_model_result(
                    model_spec, beta_status, histories=aligned, model_benchmark=model_benchmark,
                    comparison_returns_full=comparison_returns_full, comparisons=comparisons,
                    common=common, evaluation_index=evaluation_index, beta_states=beta_states,
                    risk_free=risk_free, portfolio_budget=portfolio_budget,
                )
                model_results.append(beta_row)
                series[model_id] = beta_returns
                compacts.append(beta_compact)
                checkpoint_row(beta_row, row_context)
                emit("model_completed", "Model row returned successfully", **row_context)
                continue
            if beta_risk_requested and beta_prerequisite_error:
                raise ValueError(beta_prerequisite_error)
            if v205_risk_requested and v205_prerequisite_error:
                raise ValueError(v205_prerequisite_error)
            config = build_control_config(
                alpha_model=model_id,
                construction=str(spec.get("construction") or "phase15"),
                risk_model=str(spec.get("risk_model") or "v203"),
                phase3_enabled=bool(spec.get("phase3_enabled", True)),
            )
            if beta_risk_requested:
                # Interactive research may consume the current trained Beta candidate.
                # Production/paper paths remain separately gated.
                risk_overrides = dict(
                    v204_require_production_frozen_beta=False,
                    v204_require_calibrated_beta=True,
                    v204_research_candidate_mode=True,
                    v204_beta_minimum_additional_gross_change=0.0,
                )
                if v205_risk_requested:
                    risk_overrides.update(v205_research_candidate_mode=True)
                config = replace(config, risk_supervisor=replace(config.risk_supervisor, **risk_overrides))
            report = run_universal(
                research_histories, config, model_benchmark_returns, risk_free,
                evaluation_start=evaluation_start, evaluation_end=evaluation_end,
                factor_quality_scores=quality_panel,
                beta_states=beta_states,
                v1a_artifact_schedule=(v1a_artifact_schedule if v205_risk_requested else None),
            )
            returns = _strategy_returns(report)
            series[model_id] = returns
            promotion = evaluate_promotion_gates(
                returns, model_benchmark_returns.reindex(returns.index), risk_free.reindex(returns.index),
                universe_size=len(universe),
            )
            compact = _compact_report(report)
            compact["promotion_gates"] = promotion
            compact["budget_positions"] = _budget_positions(compact, research_histories, portfolio_budget, evaluation_end)
            compact["portfolio_budget"] = portfolio_budget
            compacts.append(compact)
            comparison_returns = {key: value.reindex(returns.index) for key, value in comparison_returns_full.items()}
            model_row={
                "model": model_spec.public_dict(),
                "headline": _headline(returns, risk_free),
                "benchmarks": evaluate_many(returns, comparison_returns, risk_free, comparisons),
                "report": compact,
                "promotion_gates": promotion,
            }
            if bool(spec.get("_include_daily_returns_for_beta_gate", False)):
                causal_rows=report.get("causal_open_to_close_returns") or []
                if not causal_rows:
                    raise ValueError(f"{model_id}: causal open-to-close strategy returns are unavailable for the Beta gate")
                model_row["_beta_gate_daily_returns"]=causal_rows
            model_results.append(model_row)
            checkpoint_row(model_row, row_context)
            emit("model_completed", "Model row returned successfully", **row_context)
        except Exception as exc:
            model_results.append(_blocked_model_result(model_spec, model_run_spec, exc))
            checkpoint_row(model_results[-1], row_context)
            emit("model_blocked", "Model row returned a blocker; inspect final diagnostics", error_type=type(exc).__name__, **row_context)

    for model_row in model_results:
        if model_row.get("status") != "blocked":
            model_row["status"] = "completed"
            model_row["settings_scope"] = _model_settings_scope(get_model_spec(model_row["model"]["id"]), spec, completed=True)
    blocked_count = sum(row.get("status") == "blocked" for row in model_results)
    completed_count = len(model_results) - blocked_count

    ensemble: dict[str, Any] | None = None
    if len(series) > 1 and bool(spec.get("include_equal_weight_ensemble", True)):
        common_strategy = None
        for returns in series.values():
            common_strategy = returns.index if common_strategy is None else common_strategy.intersection(returns.index)
        if common_strategy is not None and len(common_strategy):
            matrix = pd.concat([returns.reindex(common_strategy) for returns in series.values()], axis=1)
            ensemble_returns = matrix.mean(axis=1)
            comparison_returns = {key: value.reindex(ensemble_returns.index) for key, value in comparison_returns_full.items()}
            ensemble = {
                "id": "equal_weight_strategy_ensemble",
                "label": "Equal-weight strategy ensemble",
                "constituent_models": list(series),
                "headline": _headline(ensemble_returns, risk_free),
                "benchmarks": evaluate_many(ensemble_returns, comparison_returns, risk_free, comparisons),
                "latest_positions": _average_positions(compacts),
                "methodology": "Arithmetic mean of completed models on their common realized sessions. Settings apply according to each row's declared engine scope; this does not alter or retrain constituents. Blocked models are excluded and listed separately.",
            }

    result: dict[str, Any] = {
        "status": "blocked" if not completed_count else "completed_with_model_errors" if blocked_count else "completed",
        "completed_model_count": completed_count,
        "blocked_model_count": blocked_count,
        "checkpoint_warnings": checkpoint_warnings,
        "error": "No requested model completed; inspect each blocked row for its unchanged prerequisite/error." if not completed_count else None,
        "mode": "interactive_multi_model_backtest" if len(selected_models) > 1 else "interactive_current_data_backtest",
        "configuration": {
            "models": selected_models,
            "alpha_model": selected_models[0],
            "construction": str(spec.get("construction") or "phase15"),
            "risk_model": str(spec.get("risk_model") or "v203"),
            "phase3_enabled": bool(spec.get("phase3_enabled", True)),
            "provider": provider, "evaluation_period": evaluation_period,
            "model_benchmark": model_benchmark,
            "risk_free_mode": risk_free_mode,
            "risk_free_annual_pct": float(spec.get("risk_free_annual_pct", 0.0)),
            "fundamentals_mode": fundamentals_mode,
            "portfolio_budget": portfolio_budget,
            "beta_research_candidate_used_by_v204": bool(beta_risk_requested and beta_states is not None and not beta_states.empty),
            "beta_research_row_enabled": bool(beta_requested),
            "fred_key_configured_for_beta": bool(beta_status.get("fred_key_configured", False)) if (beta_requested or beta_risk_requested) else None,
            "fred_key_configured_for_risk_free": bool(fred_api_key) if risk_free_mode == "fred_dgs3mo" else None,
            "v204_research_candidate_mode": bool(beta_risk_requested),
            "v205_research_candidate_mode": bool(v205_risk_requested),
            "v205_v1a_artifact_count": len(v1a_artifact_schedule),
            "qoptima_model_row_enabled": bool(qoptima_requested),
            "pre_gamma_rejection_policy": str(spec.get("pre_gamma_rejection_policy") or "abort"),
            "pre_gamma_beta_source": str(spec.get("pre_gamma_beta_source") or "supplied"),
        },
        "evaluation": {"start": evaluation_start, "end": evaluation_end, "sessions": int(eval_sessions)},
        "beta_research": {
            "requested": bool(beta_requested),
            "risk_v204_requested": bool(beta_risk_requested),
            "fred_key_configured": bool(beta_status.get("fred_key_configured", False)) if (beta_requested or beta_risk_requested) else None,
            "candidate_state_panel_rows": 0 if beta_states is None else int(len(beta_states)),
            "risk_v204_research_consumable": bool(beta_status.get("risk_v204_research_consumable", False)) if (beta_requested or beta_risk_requested) else False,
            "production_v204_consumable": bool(beta_status.get("risk_v204_consumable", False)) if (beta_requested or beta_risk_requested) else False,
            "risk_v205_requested": bool(v205_risk_requested),
        },
        "v1a_risk_v205": v205_v1a_status,
        "models": model_results,
        "ensemble": ensemble,
        "data": metadata,
        "risk_free": risk_free_metadata,
        "point_in_time_corporate": corporate_metadata,
        "beta": beta_status,
        "qualification": (
            "Convenience backtest on current/cached provider data. Static input universe and repeated operator use can introduce selection bias; "
            "use frozen point-in-time protocols for confirmatory evidence. Comparison requests share the selected universe, benchmark window and global settings; native QPO and standalone Beta retain their declared engine-specific construction/risk scopes. Inspect each row settings_scope and completion status."
        ),
    }
    emit("matrix_completed", "Control matrix returned", completed_model_count=completed_count, blocked_model_count=blocked_count)
    # Backward-compatible single-model fields for Control 4.x clients.
    if len(model_results) == 1:
        single = model_results[0]
        result["benchmarks"] = single["benchmarks"]
        result["report"] = single["report"]
        result["evaluation"] = {**result["evaluation"], **single["headline"]}
    return result


def run_beta_incremental_backtest(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    """Run the preregistered Beta incremental portfolio study on real Control returns.

    The causal contract is fixed: Beta is known at the NYSE regular-session open,
    while the Beta gate uses Oryntra Universal's separately measured causal open-to-close
    return for that same trading session (prior-close signal -> current-open transaction ->
    current-close mark). The ordinary close-to-close performance return is not used for this gate. Consensus data are not required to run this research test;
    they remain a separate production-freeze gate.
    """
    svc=beta_service(user_id)
    status=svc.artifact_status()
    if not status.trained or not status.candidate_diagnostics_match:
        raise ValueError("Train a current Beta candidate before running the incremental portfolio backtest")
    if not status.probability_quality_ready:
        raise ValueError("Beta holdout probability gate must pass before incremental portfolio backtesting")
    if not status.robustness_ready:
        raise ValueError("Beta hyperparameter robustness gate must pass before incremental portfolio backtesting")

    universe=_clean_universe(list(spec.get("universe") or []))
    if len(universe)<12:
        raise ValueError("Beta incremental backtest requires at least 12 research symbols")
    period=str(spec.get("evaluation_period") or "5y").strip().lower()
    if period not in {"2y","5y","10y"}:
        raise ValueError("Beta incremental backtest period must be 2y, 5y, or 10y")
    provider=str(spec.get("provider") or "auto").strip().lower()
    fundamentals=str(spec.get("fundamentals_mode") or "sec_pit").strip().lower()
    construction=str(spec.get("construction") or "phase15").strip().lower()
    phase3=bool(spec.get("phase3_enabled",True))
    benchmark=str(spec.get("model_benchmark") or "SPY/QQQ/IWM/VXUS")

    bt_spec={
        "universe":universe,
        "models":["tba8","alpha_v12_alt"],
        "include_equal_weight_ensemble":False,
        "construction":construction,
        "risk_model":"v203",
        "phase3_enabled":phase3,
        "provider":provider,
        "evaluation_period":period,
        "model_benchmark":benchmark,
        "comparison_benchmarks":[benchmark],
        "risk_free_mode":"fred_dgs3mo",
        "risk_free_annual_pct":0.0,
        "fundamentals_mode":fundamentals,
        "portfolio_budget":float(spec.get("portfolio_budget") or 100000.0),
        "force_refresh":bool(spec.get("force_refresh",False)),
        "force_refresh_risk_free":bool(spec.get("force_refresh_risk_free",False)),
        "_include_daily_returns_for_beta_gate":True,
    }
    report=run_interactive_backtest(user_id,bt_spec)
    series={}
    summaries={}
    for row in report.get("models") or []:
        mid=str((row.get("model") or {}).get("id") or "")
        daily=row.pop("_beta_gate_daily_returns",None)
        if mid in {"tba8","alpha_v12_alt"} and daily:
            sr=pd.Series({pd.Timestamp(x["date"]):float(x["net_return"]) for x in daily},dtype=float).sort_index()
            series[mid]=sr
            summaries[mid]=row.get("headline") or {}
    if "tba8" not in series:
        raise ValueError("Incremental study could not obtain Model A / TBA8 realized returns")
    returns=pd.concat(series,axis=1).sort_index().dropna(how="all")
    model=svc.load_model()
    holdout_start=pd.Timestamp(model.holdout_start).normalize()
    pre=int((returns.index<holdout_start).sum()); post=int((returns.index>=holdout_start).sum())
    if pre<252 or post<60:
        raise ValueError(f"Incremental study needs >=252 pre-holdout and >=60 holdout strategy sessions; found {pre} and {post}. Use a longer period/universe history.")
    rf,rf_meta=build_control_risk_free(returns.index,mode="fred_dgs3mo",annual_pct=0.0,force_refresh=bool(spec.get("force_refresh_risk_free",False)))
    study_metadata={
        "source":"Oryntra Control interactive Universal backtest",
        "models":["tba8","alpha_v12_alt"],
        "risk_model":"v203",
        "construction":construction,
        "phase3_enabled":phase3,
        "evaluation_period":period,
        "evaluation_start":str(returns.index.min().date()),
        "evaluation_end":str(returns.index.max().date()),
        "pre_holdout_sessions":pre,
        "holdout_sessions":post,
        "beta_holdout_start":str(holdout_start.date()),
        "strategy_execution_timing":"prior completed-close signal -> current session open transaction -> current session close mark",
        "risk_free":rf_meta,
        "consensus_required_for_this_research_backtest":False,
    }
    evidence=svc.run_conditioned_research(
        returns,return_convention="open_to_close_same_session",risk_free_daily=rf,
        bootstrap_reps=int(spec.get("bootstrap_reps") or 500),study_metadata=study_metadata,
    )
    promotion=evidence.get("promotion_incremental") or {}
    policy=evidence.get("policy_evaluation") or {}
    result={
        "status":"completed",
        "study":"beta_incremental_portfolio_backtest_v1",
        "artifact_id":status.candidate_artifact_id,
        "alignment":promotion.get("alignment") or study_metadata,
        "backtest_context":study_metadata,
        "model_headlines":summaries,
        "promotion_incremental":promotion,
        "policy_evaluation":policy,
        "artifact_status":evidence.get("artifact_status") or svc.artifact_status().to_dict(),
        "research_backtest_complete":True,
        "incremental_gate_passed":bool(promotion.get("pass_any",False)),
        "production_consensus_still_required":not bool((policy.get("gates") or {}).get("consensus_surprise_coverage",False)),
    }
    def _clean_json(v):
        if isinstance(v,dict): return {str(k):_clean_json(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)): return [_clean_json(x) for x in v]
        if isinstance(v,(np.floating,float)):
            return float(v) if np.isfinite(v) else None
        if isinstance(v,(np.integer,)): return int(v)
        if isinstance(v,(pd.Timestamp,)): return v.isoformat()
        return v
    return _clean_json(result)


def run_current_market_snapshot(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    snapshot_spec = dict(spec)
    snapshot_spec["evaluation_period"] = str(spec.get("evaluation_period") or "3mo")
    result = run_interactive_backtest(user_id, snapshot_spec)
    snapshots: list[dict[str, Any]] = []
    for model in result["models"]:
        compact = model["report"]
        snapshots.append({
            "model": model["model"], "headline": model["headline"], "benchmarks": model["benchmarks"],
            "status": model.get("status"), "error": model.get("error"), "settings_scope": model.get("settings_scope"),
            "latest_positions": (compact.get("portfolio_risk") or {}).get("latest_positions") or [],
            "latest_signals": compact.get("latest_signals") or {}, "risk": compact.get("risk_latest"),
            "factor": compact.get("factor_latest"), "optimizer": compact.get("optimizer_latest"),
            "phase3": compact.get("phase3_latest"),
            "beta": compact.get("beta"),
        })
    out = {
        "status": result["status"], "error": result.get("error"), "completed_model_count": result.get("completed_model_count"),
        "blocked_model_count": result.get("blocked_model_count"), "mode": "current_market_snapshot", "as_of": result["evaluation"]["end"],
        "configuration": result["configuration"], "models": snapshots, "ensemble": result.get("ensemble"),
        "data": result.get("data"), "qualification": result["qualification"],
    }
    if len(snapshots) == 1:
        single = snapshots[0]
        out.update({
            "benchmarks": single["benchmarks"], "latest_positions": single["latest_positions"],
            "latest_signals": single["latest_signals"], "risk": single["risk"], "factor": single["factor"],
            "optimizer": single["optimizer"], "phase3": single["phase3"],
        })
    return out
