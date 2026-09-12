"""Subscriber-only, research-only portfolio decision ledger.

The browser supplies a completed daily OHLCV window for one run.  This route
stores the resulting hypothetical directives and reproducibility metadata, not
the price history and never an instruction to a broker.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..database import get_connection
from ..internal_access import require_cqc_internal_operator
from ..universal_engine import ENGINE_ID, ENGINE_VERSION, UniversalConfig, portfolio_targets
from ..universal_position_policy import PositionPolicyConfig
from ..universal_research import run_universal
from .universal import History, parse_histories

router = APIRouter()
_run_slots = asyncio.Semaphore(1)
def _default_configuration() -> UniversalConfig:
    # These are research defaults, not claimed profitable settings.  The
    # lifecycle is explicit here because Portfolio Lab is its intended ledger.
    return UniversalConfig(position_policy=PositionPolicyConfig(enabled=True))


class PortfolioDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="Portfolio Lab", min_length=1, max_length=80)
    histories: list[History] = Field(min_length=2, max_length=24)
    configuration: UniversalConfig = Field(default_factory=_default_configuration)


def _aligned_histories(items: list[History]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    histories = parse_histories(items)
    common = None
    for frame in histories.values():
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None or len(common) < 254:
        raise ValueError("Portfolio Lab needs 254 overlapping completed daily sessions for every symbol.")
    common = common.sort_values()
    aligned = {symbol: frame.loc[common].copy() for symbol, frame in histories.items()}
    prices = pd.DataFrame({symbol: frame["Close"] for symbol, frame in aligned.items()}, index=common)
    if prices.isna().any().any() or (prices <= 0).any().any():
        raise ValueError("Portfolio Lab needs complete, positive daily closes for every selected symbol.")
    return aligned, prices


def _directive_rows(report: dict, targets: pd.DataFrame, prices: pd.DataFrame, configuration: UniversalConfig) -> list[dict]:
    latest, prior = targets.iloc[-1], targets.iloc[-2]
    states = {row["symbol"]: row for row in report["position_policy_execution"]["open_states"]}
    rows = []
    for symbol in targets.columns:
        target, before = float(latest[symbol]), float(prior[symbol])
        if target > before + 1e-6:
            action = "BUY"
            rationale = "The completed-close target weight increased; any simulated fill is next session's open."
        elif target < before - 1e-6:
            action = "SELL" if target <= 1e-6 else "TRIM"
            rationale = "The completed-close target weight decreased; this is a hypothetical next-session adjustment."
        else:
            action = "HOLD"
            rationale = "The completed-close target weight did not change enough to create a new research directive."
        price = float(prices.iloc[-1][symbol])
        state = states.get(symbol, {})
        rows.append({
            "symbol": str(symbol), "action": action,
            "target_weight_pct": round(target * 100, 4), "prior_weight_pct": round(before * 100, 4),
            "reference_close": round(price, 6),
            "estimated_target_shares": round(target * configuration.initial_equity / price, 4),
            "rationale": rationale,
            "lifecycle": state,
        })
    return rows


def _record_run(user_id: int, request: PortfolioDecisionRequest, report: dict, directives: list[dict]) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO research_portfolio_runs
               (user_id, label, engine_id, engine_version, configuration_json, dataset_fingerprint, as_of_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, request.label.strip(), ENGINE_ID, ENGINE_VERSION,
             json.dumps(asdict(request.configuration), sort_keys=True), report["dataset_fingerprint"], report["universe"]["end"]),
        )
        run_id = int(cursor.lastrowid)
        conn.executemany(
            """INSERT INTO research_portfolio_directives
               (run_id, symbol, action, target_weight, prior_weight, reference_price, estimated_shares, rationale, lifecycle_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(run_id, row["symbol"], row["action"], row["target_weight_pct"], row["prior_weight_pct"],
              row["reference_close"], row["estimated_target_shares"], row["rationale"], json.dumps(row["lifecycle"], sort_keys=True))
             for row in directives],
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def _run_decision(request: PortfolioDecisionRequest) -> tuple[dict, list[dict]]:
    histories, prices = _aligned_histories(request.histories)
    report = run_universal(histories, request.configuration)
    targets = portfolio_targets(prices, request.configuration)
    return report, _directive_rows(report, targets, prices, request.configuration)


@router.post("/run-upload")
async def run_upload(payload: PortfolioDecisionRequest, request: Request):
    """CQC-internal hypothetical research ledger; unavailable to subscribers."""
    user = require_cqc_internal_operator(request)
    try:
        async with _run_slots:
            report, directives = await asyncio.to_thread(_run_decision, payload)
        run_id = await asyncio.to_thread(_record_run, user["id"], payload, report, directives)
        return {
            "ok": True, "run_id": run_id, "label": payload.label.strip(),
            "as_of": report["universe"]["end"], "engine": {"id": ENGINE_ID, "version": ENGINE_VERSION},
            "configuration_fingerprint": report["config_fingerprint"], "dataset_fingerprint": report["dataset_fingerprint"],
            "directives": directives,
            "execution_note": "Research-only: directives are hypothetical, use completed-close information, and model next-session execution. No broker order was created.",
            "position_policy": report["position_policy"],
            "position_policy_execution": {
                "status": report["position_policy_execution"].get("status", "active_research_ledger"),
                "event_count": report["position_policy_execution"].get("event_count", 0),
                "open_states": report["position_policy_execution"].get("open_states", []),
            },
            "raw_market_data_persisted": False,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs")
async def runs(request: Request):
    """Return CQC-internal research-ledger records, never raw bars."""
    user = require_cqc_internal_operator(request)
    conn = get_connection()
    try:
        run_rows = conn.execute(
            """SELECT id, label, engine_id, engine_version, configuration_json, dataset_fingerprint, as_of_date, created_at
               FROM research_portfolio_runs WHERE user_id=? ORDER BY id DESC LIMIT 20""", (user["id"],)
        ).fetchall()
        result = []
        for row in run_rows:
            directives = conn.execute(
                """SELECT symbol, action, target_weight, prior_weight, reference_price, estimated_shares, rationale, lifecycle_json
                   FROM research_portfolio_directives WHERE run_id=? ORDER BY symbol""", (row["id"],)
            ).fetchall()
            result.append({
                "id": row["id"], "label": row["label"], "engine": {"id": row["engine_id"], "version": row["engine_version"]},
                "dataset_fingerprint": row["dataset_fingerprint"],
                "configuration_fingerprint": hashlib.sha256(str(row["configuration_json"]).encode()).hexdigest(),
                "as_of": row["as_of_date"], "created_at": row["created_at"],
                "directives": [{**dict(item), "lifecycle": json.loads(item["lifecycle_json"])} for item in directives],
            })
        return {"runs": result, "raw_market_data_persisted": False}
    finally:
        conn.close()
