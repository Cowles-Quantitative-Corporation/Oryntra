"""Private Oryntra Control API for CQC research operations.

All API endpoints require server-provisioned internal-operator access.  The
control plane never grants product entitlements and never submits broker orders.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..database import get_connection
from ..internal_access import require_control_operator
from ..phase4_control import registry_payload
from ..phase4_jobs import get_job, list_jobs, submit_job
from ..automation_orchestrator import run_candidate_automation, list_alerts, acknowledge_alert, recent_automation_runs
from ..phase4_scheduler import run_automation_sweep, scheduler_status
from ..phase4_ledger import (
    create_candidate, get_candidate, list_candidates, record_simulated_fill, set_candidate_status,
)

router = APIRouter()
_cycle_lock = asyncio.Lock()


class CandidateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=120)
    alpha_model: str = "tba8"
    construction: str = "phase15"
    risk_model: str = "v203"
    phase3_enabled: bool = True
    benchmark: str = "spy_qqq_equal"
    universe: list[str] = Field(min_length=2, max_length=40)
    initial_nav: float = Field(default=1_000_000.0, ge=100)


class CandidateStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite_id: str
    bars_csv: str | None = None
    risk_free_csv: str | None = None
    declared_years: str | None = None
    evidence_label: str | None = None
    alpha_model: str = "tba8"
    construction: str = "phase15"
    risk_model: str = "v203"
    phase3_enabled: bool = True
    benchmark: str = "spy_qqq_equal"
    timeout_seconds: int = Field(default=7200, ge=60, le=21600)


class ManualFillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int
    fill_date: str
    fill_price: float = Field(gt=0)
    filled_shares: float | None = Field(default=None, gt=0)
    fees: float = Field(default=0.0, ge=0)


def _candidate_snapshot(candidate_id: int, user_id: int) -> dict[str, Any]:
    candidate = get_candidate(candidate_id, user_id=user_id)
    conn = get_connection()
    try:
        nav = conn.execute("SELECT * FROM phase4_nav WHERE candidate_id=? ORDER BY as_of_date DESC LIMIT 1", (candidate_id,)).fetchone()
        decision = conn.execute("SELECT id,as_of_date,dataset_fingerprint,created_at FROM phase4_decisions WHERE candidate_id=? ORDER BY as_of_date DESC,id DESC LIMIT 1", (candidate_id,)).fetchone()
        orders = conn.execute(
            """SELECT o.id,o.symbol,o.side,o.target_weight,o.intended_notional,o.intended_shares,o.scheduled_for,o.status
                 FROM phase4_orders o JOIN phase4_decisions d ON d.id=o.decision_id
                WHERE d.candidate_id=? ORDER BY o.id DESC LIMIT 100""", (candidate_id,),
        ).fetchall()
        reconciliation = conn.execute(
            """SELECT as_of_date,symbol,approved_weight,actual_weight,weight_error,status,reason_json
                 FROM phase4_reconciliations WHERE candidate_id=? ORDER BY id DESC LIMIT 100""", (candidate_id,),
        ).fetchall()
        nav_history = conn.execute("SELECT as_of_date,nav,cash,gross_exposure,net_return,benchmark_return FROM phase4_nav WHERE candidate_id=? ORDER BY as_of_date DESC LIMIT 250", (candidate_id,)).fetchall()
    finally:
        conn.close()
    return {
        "candidate": candidate,
        "latest_nav": dict(nav) if nav else None,
        "latest_decision": dict(decision) if decision else None,
        "orders": [dict(row) for row in orders],
        "reconciliation": [{**dict(row), "reason": json.loads(row["reason_json"])} for row in reconciliation],
        "nav_history": [dict(row) for row in reversed(nav_history)],
    }



@router.get("/registry")
def registry(request: Request):
    require_control_operator(request)
    return registry_payload()


@router.get("/overview")
def overview(request: Request):
    user = require_control_operator(request)
    return {
        "registry": registry_payload(),
        "candidates": list_candidates(user["id"]),
        "jobs": list_jobs(user["id"], 20),
        "automation": scheduler_status(),
        "alerts": list_alerts(user["id"], 20),
        "automation_runs": recent_automation_runs(user["id"], 10),
    }


@router.post("/candidates")
def new_candidate(payload: CandidateCreateRequest, request: Request):
    user = require_control_operator(request)
    try:
        return create_candidate(user_id=user["id"], **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/candidates/{candidate_id}")
def candidate(candidate_id: int, request: Request):
    user = require_control_operator(request)
    try:
        return _candidate_snapshot(candidate_id, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/status")
def candidate_status(candidate_id: int, payload: CandidateStatusRequest, request: Request):
    user = require_control_operator(request)
    try:
        return set_candidate_status(candidate_id, user["id"], payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/cycle")
async def candidate_cycle(candidate_id: int, request: Request):
    user = require_control_operator(request)
    try:
        async with _cycle_lock:
            return await asyncio.to_thread(run_candidate_automation, candidate_id, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/fills")
def manual_fill(payload: ManualFillRequest, request: Request):
    require_control_operator(request)
    try:
        return record_simulated_fill(**payload.model_dump(), source="manual_paper_fill")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/automation")
def automation_status(request: Request):
    require_control_operator(request)
    return scheduler_status()


@router.post("/automation/sweep")
async def automation_sweep(request: Request):
    require_control_operator(request)
    return await asyncio.to_thread(run_automation_sweep, force=True)


@router.get("/alerts")
def automation_alerts(request: Request):
    user = require_control_operator(request)
    return {"alerts": list_alerts(user["id"], 200)}


@router.post("/alerts/{alert_id}/ack")
def automation_alert_ack(alert_id: int, request: Request):
    user = require_control_operator(request)
    acknowledge_alert(user["id"], alert_id)
    return {"ok": True}


@router.get("/automation/runs")
def automation_runs(request: Request):
    user = require_control_operator(request)
    return {"runs": recent_automation_runs(user["id"], 100)}


@router.post("/jobs")
def run_job(payload: JobRequest, request: Request):
    user = require_control_operator(request)
    spec = {key: value for key, value in payload.model_dump().items() if key != "suite_id" and value is not None}
    try:
        return submit_job(user["id"], payload.suite_id, spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs")
def jobs(request: Request):
    user = require_control_operator(request)
    return {"jobs": list_jobs(user["id"])}


@router.get("/jobs/{job_id}")
def job(job_id: int, request: Request):
    user = require_control_operator(request)
    try:
        return get_job(user["id"], job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
