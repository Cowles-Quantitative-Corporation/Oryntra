"""Execution-neutral runner for frozen Phase 4 prospective candidates.

This module owns the *paper/shadow* cycle used by both the private API and the
local desktop scheduler.  It deliberately has no brokerage adapter.
"""
from __future__ import annotations

import json

import pandas as pd

from .database import get_connection
from .corporate_repository import get_corporate_repository
from .control_beta import frozen_beta_state_panel
from .sec_pit import cached_factor_panels
from .market_repository import get_market_repository
from .provider_credentials import decrypted_credentials
from .phase4_control import code_fingerprint
from .phase4_ledger import (
    get_candidate,
    mark_portfolio,
    pending_orders,
    reconcile_latest,
    record_simulated_fill,
    run_completed_close_decision,
)
from .universal_engine import UniversalConfig


def _frozen_config(candidate: dict) -> UniversalConfig:
    manifest = candidate.get("manifest") or {}
    configuration = manifest.get("configuration")
    if not isinstance(configuration, dict):
        raise ValueError("Frozen candidate manifest is missing its configuration")
    config = UniversalConfig(**configuration)
    if config.fingerprint != candidate.get("config_fingerprint"):
        raise ValueError(
            "Frozen candidate configuration fingerprint no longer matches its manifest; "
            "archive this candidate and freeze a new one."
        )
    return config


def validate_frozen_candidate(candidate: dict) -> UniversalConfig:
    """Fail closed if a prospective candidate is no longer truly frozen."""
    current_code = code_fingerprint()
    frozen_code = str(candidate.get("code_fingerprint") or "")
    if not frozen_code or current_code != frozen_code:
        raise ValueError(
            "Frozen candidate code fingerprint differs from the current research engine. "
            "Prospective evidence cannot continue across a code change; freeze a new candidate."
        )
    manifest = candidate.get("manifest") or {}
    if str(manifest.get("code_fingerprint") or frozen_code) != frozen_code:
        raise ValueError("Frozen candidate manifest/code fingerprint mismatch")
    return _frozen_config(candidate)


def _benchmark_returns(histories: dict[str, pd.DataFrame], components: dict[str, float], index: pd.DatetimeIndex) -> pd.Series:
    result = pd.Series(0.0, index=index, dtype=float)
    for symbol, weight in components.items():
        if symbol not in histories:
            raise ValueError(f"Frozen benchmark history is missing {symbol}")
        result = result.add(
            histories[symbol].reindex(index)["Close"].pct_change(fill_method=None).fillna(0.0) * float(weight),
            fill_value=0.0,
        )
    return result


def run_candidate_cycle(candidate_id: int, user_id: int) -> dict:
    """Advance one candidate using only its frozen manifest and paper fills."""
    candidate = get_candidate(candidate_id, user_id=user_id)
    if candidate["status"] in {"PAUSED", "ARCHIVED"}:
        raise ValueError("Candidate is paused or archived")
    config = validate_frozen_candidate(candidate)
    manifest = candidate["manifest"]
    benchmark = manifest.get("benchmark") or {}
    components = benchmark.get("components") or {}
    if not components:
        raise ValueError("Frozen candidate manifest is missing benchmark components")
    if str(manifest.get("benchmark_id") or "") != str(candidate.get("benchmark_id") or ""):
        raise ValueError("Frozen candidate benchmark identifier no longer matches its manifest")

    symbols = sorted(set(candidate["universe"]) | set(components))
    minimum = max(800, config.ridge_training_sessions + 30 if config.alpha_model == "walk_forward_ridge" else (config.alpha_v12.anchor_training_sessions + config.alpha_v12.horizon_sessions + 30 if config.alpha_model == "alpha_v12" and config.alpha_v12.include_tba_anchor else 300))
    repository = get_market_repository()
    provider = str(manifest.get("provider") or "auto").strip().lower()
    provider_keys = {} if provider == "yfinance" else decrypted_credentials(user_id)
    histories: dict[str, pd.DataFrame] = {}
    metadata: dict[str, dict] = {}
    for symbol in symbols:
        item = repository.get_history(
            symbol, period="all", minimum_bars=minimum, allow_api=provider != "cache_only",
            provider_preference=provider, provider_api_keys=provider_keys,
            allow_platform_provider_keys=False,
        )
        histories[symbol] = item.history.copy()
        metadata[symbol] = item.metadata.__dict__

    common = None
    for symbol in symbols:
        common = histories[symbol].index if common is None else common.intersection(histories[symbol].index)
    if common is None or len(common) < minimum:
        raise ValueError("Candidate universe does not have enough aligned completed daily history")
    common = common.sort_values()
    latest = common[-1]
    latest_date = str(latest.date())

    fills = []
    for order in pending_orders(candidate_id, through_date=latest_date):
        symbol = str(order["symbol"])
        frame = histories[symbol]
        eligible = frame.loc[(frame.index >= pd.Timestamp(order["scheduled_for"])) & (frame.index <= latest)]
        if eligible.empty:
            continue
        fill_day = eligible.index[0]
        fill = record_simulated_fill(
            order_id=int(order["id"]),
            fill_date=str(fill_day.date()),
            fill_price=float(eligible.iloc[0]["Open"]),
        )
        fills.append(fill)

    close_prices = {symbol: float(histories[symbol].loc[latest, "Close"]) for symbol in candidate["universe"]}
    benchmark_returns = _benchmark_returns(histories, components, common)
    mark = mark_portfolio(
        candidate_id=candidate_id,
        as_of_date=latest_date,
        close_prices=close_prices,
        benchmark_return=float(benchmark_returns.loc[latest]),
    )
    reconciliation = reconcile_latest(candidate_id=candidate_id)

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM phase4_decisions WHERE candidate_id=? AND as_of_date=?",
            (candidate_id, latest_date),
        ).fetchone()
    finally:
        conn.close()

    # Optional fundamentals are explicitly mode-controlled and point-in-time.
    quality_panel = None
    fundamentals_mode = str(manifest.get("fundamentals_mode") or "sec_pit").strip().lower()
    fundamental_metadata = {"mode": fundamentals_mode, "coverage_pct": 0.0, "facts_used": 0}
    needs_quality = bool(
        getattr(config, "alpha_model", "") in {"alpha_v1", "alpha_v12"}
        or getattr(getattr(config, "factor_model", None), "enabled", False)
    )
    if needs_quality and fundamentals_mode == "sec_pit":
        sec_result = cached_factor_panels(candidate["universe"], common, histories={s: histories[s] for s in candidate["universe"]})
        quality_panel = sec_result["quality"] if sec_result["quality"].notna().any().any() else None
        fundamental_metadata = sec_result["metadata"]
    elif needs_quality and fundamentals_mode == "local_pit":
        quality_panel, fundamental_metadata = get_corporate_repository().factor_panel(candidate["universe"], common, fill_missing=False)
        fundamental_metadata = {"mode": "local_pit", **fundamental_metadata}
    elif fundamentals_mode == "off":
        fundamental_metadata = {"mode": "off", "coverage_pct": 0.0, "facts_used": 0}

    beta_states = None
    beta_metadata = {"requested": False}
    if str(candidate.get("risk_model_id") or "").lower() == "v204":
        beta_states, beta_metadata = frozen_beta_state_panel(common)
        beta_metadata = {"requested": True, **beta_metadata}

    decision = None
    if not existing:
        research_histories = {symbol: histories[symbol].reindex(common) for symbol in candidate["universe"]}
        decision_result = run_completed_close_decision(
            candidate_id=candidate_id,
            user_id=user_id,
            histories=research_histories,
            benchmark_returns=benchmark_returns,
            factor_quality_scores=quality_panel,
            beta_states=beta_states,
        )
        decision = decision_result["decision"]

    return {
        "candidate_id": candidate_id,
        "latest_completed_session": latest_date,
        "data": metadata,
        "fundamentals": fundamental_metadata,
        "beta": beta_metadata,
        "fills_recorded": fills,
        "mark": mark,
        "reconciliation": reconciliation,
        "new_decision": decision,
        "decision_already_recorded": bool(existing),
        "frozen_code_verified": True,
        "execution_boundary": "immutable simulated paper ledger; broker mirroring is handled separately by the gated automation adapter",
    }
