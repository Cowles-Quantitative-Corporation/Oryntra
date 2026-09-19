"""Execution-neutral runner for frozen Phase 4 prospective candidates.

This module owns the *paper/shadow* cycle used by both the private API and the
local desktop scheduler.  It deliberately has no brokerage adapter.
"""
from __future__ import annotations

import json

import pandas as pd

from .database import get_connection
from .market_repository import get_market_repository
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
    minimum = max(800, config.ridge_training_sessions + 30 if config.alpha_model == "walk_forward_ridge" else 300)
    repository = get_market_repository()
    histories: dict[str, pd.DataFrame] = {}
    metadata: dict[str, dict] = {}
    for symbol in symbols:
        item = repository.get_history(symbol, period="all", minimum_bars=minimum, allow_api=True, provider_preference="auto")
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

    decision = None
    if not existing:
        research_histories = {symbol: histories[symbol].reindex(common) for symbol in candidate["universe"]}
        decision_result = run_completed_close_decision(
            candidate_id=candidate_id,
            user_id=user_id,
            histories=research_histories,
            benchmark_returns=benchmark_returns,
        )
        decision = decision_result["decision"]

    return {
        "candidate_id": candidate_id,
        "latest_completed_session": latest_date,
        "data": metadata,
        "fills_recorded": fills,
        "mark": mark,
        "reconciliation": reconciliation,
        "new_decision": decision,
        "decision_already_recorded": bool(existing),
        "frozen_code_verified": True,
        "execution_boundary": "simulated next-open paper fills only; no broker order submitted",
    }
