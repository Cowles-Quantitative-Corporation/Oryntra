"""Private Oryntra Control API for CQC research and automation operations."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..automation_orchestrator import acknowledge_alert, list_alerts, recent_automation_runs, run_candidate_automation
from ..control_settings import load_settings, save_settings
from ..database import get_connection
from ..ibkr_adapter import (
    LIVE_CONFIRMATION,
    ibkr_audit_log,
    ibkr_connection_status,
    ibkr_settings,
    latest_ibkr_links,
    reconcile_ibkr_target,
    save_ibkr_settings,
    sync_ibkr_account,
)
from ..internal_access import require_control_operator
from ..models.registry import get_model_spec
from ..phase4_control import registry_payload
from ..phase4_jobs import get_job, list_jobs, submit_job
from ..phase4_ledger import create_candidate, get_candidate, list_candidates, record_simulated_fill, set_candidate_status
from ..phase4_scheduler import run_automation_sweep, run_ibkr_keepalive, scheduler_status
from ..provider_credentials import credential_status, delete_credential, save_credential
from ..qoptima.portfolio import export_active_portfolio_payload

router = APIRouter()
_cycle_lock = asyncio.Lock()


class CandidateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=120)
    alpha_model: str = "tba8"
    construction: str = "phase15"
    risk_model: str = "v203"
    phase3_enabled: bool = True
    benchmark: str = "balanced_global_equal"
    comparison_benchmarks: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM", "VXUS", "SPY/QQQ/IWM/VXUS"], max_length=12)
    universe: list[str] = Field(min_length=2)
    initial_nav: float = Field(default=1_000_000.0, ge=100)
    fundamentals_mode: str = "sec_pit"
    status: str = "SHADOW"
    provider: str = "auto"


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
    benchmark: str = "balanced_global_equal"
    timeout_seconds: int = Field(default=7200, ge=60, le=21600)


class InteractiveResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: list[str] = Field(min_length=2)
    models: list[str] = Field(default_factory=lambda: ["tba8"], min_length=1, max_length=12)
    alpha_model: str | None = None  # compatibility with 4.x clients
    include_equal_weight_ensemble: bool = True
    construction: str = "phase15"
    risk_model: str = "v203"
    phase3_enabled: bool = True
    model_benchmark: str = "SPY/QQQ/IWM/VXUS"
    comparison_benchmarks: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM", "VXUS", "SPY/QQQ/IWM/VXUS"], min_length=1, max_length=12)
    provider: str = "auto"
    evaluation_period: str = "1y"
    risk_free_mode: str = "fred_dgs3mo"
    risk_free_annual_pct: float = 0.0
    fundamentals_mode: str = "sec_pit"
    portfolio_budget: float = Field(default=100_000.0, ge=100)
    force_refresh: bool = False
    force_refresh_risk_free: bool = False
    timeout_seconds: int = Field(default=7200, ge=60, le=21600)
    pre_gamma_config: dict[str, Any] = Field(default_factory=dict)
    pre_gamma_beta_records: list[dict[str, Any]] = Field(default_factory=list, max_length=100000)
    pre_gamma_rejection_policy: Literal["abort", "cash_on_qrm_rejection"] = "abort"
    pre_gamma_beta_source: Literal["supplied", "causal_research"] = "supplied"

    @model_validator(mode="after")
    def validate_pre_gamma_beta_source(self):
        if self.pre_gamma_beta_source == "causal_research" and self.pre_gamma_beta_records:
            raise ValueError("Choose causal_research generation or supplied Pre-Gamma Beta records, not both")
        return self

    @field_validator("pre_gamma_config")
    @classmethod
    def validate_pre_gamma_config(cls, value):
        from ..qoptima.gamma_integration import validate_gamma_config
        return validate_gamma_config(value)

    @field_validator("pre_gamma_beta_records")
    @classmethod
    def validate_pre_gamma_beta_records(cls, value):
        from ..qoptima.gamma_forecast import _validated_beta_records
        _accepted, rejected = _validated_beta_records(value)
        if rejected:
            raise ValueError(f"Pre-Gamma Beta input lacks valid causal provenance: {rejected[:3]}")
        return value


class ControlSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str | None = None
    automation_enabled: bool | None = None
    automation_hour_et: int | None = Field(default=None, ge=0, le=23)
    automation_interval_seconds: int | None = Field(default=None, ge=300, le=86400)
    default_alpha_model: str | None = None
    default_construction: str | None = None
    default_risk_model: str | None = None
    default_phase3_enabled: bool | None = None
    default_model_benchmark: str | None = None
    default_comparison_benchmarks: list[str] | None = Field(default=None, max_length=12)


class ProviderKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    api_key: str = Field(min_length=8, max_length=512)


class ManualFillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int
    fill_date: str
    fill_price: float = Field(gt=0)
    filled_shares: float | None = Field(default=None, gt=0)
    fees: float = Field(default=0.0, ge=0)


class IBKRSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "DISABLED"
    account_id: str | None = None
    gateway_host: str = "localhost"
    gateway_port: int = Field(default=5000, ge=1, le=65535)
    client_id: int = Field(default=0, ge=0, le=999999)
    base_url: str = "https://localhost:5000/v1/api"
    verify_ssl: bool = False
    paper_transmit_enabled: bool = False
    live_transmit_enabled: bool = False
    live_confirmation: str | None = None


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
        "settings": load_settings(),
        "providers": credential_status(user["id"]),
        "ibkr": ibkr_settings(user["id"]),
        "alerts": list_alerts(user["id"], 20),
        "automation_runs": recent_automation_runs(user["id"], 10),
    }


@router.get("/settings")
def settings(request: Request):
    user = require_control_operator(request)
    return {
        "settings": load_settings(), "automation": scheduler_status(),
        "providers": credential_status(user["id"]), "ibkr": ibkr_settings(user["id"]),
    }


@router.post("/settings")
def update_settings(payload: ControlSettingsRequest, request: Request):
    require_control_operator(request)
    changes = {key: value for key, value in payload.model_dump().items() if value is not None}
    if changes.get("default_alpha_model"):
        get_model_spec(str(changes["default_alpha_model"]))
    return {"settings": save_settings(changes), "automation": scheduler_status()}


@router.post("/providers")
def provider_key(payload: ProviderKeyRequest, request: Request):
    user = require_control_operator(request)
    try:
        return save_credential(user["id"], payload.provider, payload.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/providers/{provider}")
def remove_provider_key(provider: str, request: Request):
    user = require_control_operator(request)
    return delete_credential(user["id"], provider)


@router.post("/backtests")
def interactive_backtest(payload: InteractiveResearchRequest, request: Request):
    user = require_control_operator(request)
    spec = payload.model_dump()
    if payload.alpha_model and payload.models == ["tba8"]:
        spec["models"] = [payload.alpha_model]
    try:
        return submit_job(user["id"], "interactive_backtest", spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/market-snapshots")
def current_market(payload: InteractiveResearchRequest, request: Request):
    user = require_control_operator(request)
    spec = payload.model_dump()
    if payload.alpha_model and payload.models == ["tba8"]:
        spec["models"] = [payload.alpha_model]
    if spec.get("evaluation_period") not in {"3mo", "6mo"}:
        spec["evaluation_period"] = "3mo"
    try:
        return submit_job(user["id"], "current_market", spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates")
def new_candidate(payload: CandidateCreateRequest, request: Request):
    user = require_control_operator(request)
    try:
        model = get_model_spec(payload.alpha_model)
        if not model.supports_phase4:
            raise ValueError(f"Model {model.label} is not Phase 4 eligible")
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
            result = await asyncio.to_thread(run_candidate_automation, candidate_id, user["id"])
            cycle = result.get("cycle") or {}
            return {**result, "new_decision": cycle.get("new_decision"), "reconciliation": cycle.get("reconciliation"), "data": cycle.get("data")}
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


@router.get("/automation/runs")
def automation_runs(request: Request):
    user = require_control_operator(request)
    return {"runs": recent_automation_runs(user["id"], 100)}


@router.get("/alerts")
def automation_alerts(request: Request):
    user = require_control_operator(request)
    return {"alerts": list_alerts(user["id"], 200)}


@router.post("/alerts/{alert_id}/ack")
def automation_alert_ack(alert_id: int, request: Request):
    user = require_control_operator(request)
    acknowledge_alert(user["id"], alert_id)
    return {"ok": True}


@router.get("/ibkr/settings")
def get_ibkr_settings(request: Request):
    user = require_control_operator(request)
    return ibkr_settings(user["id"])


@router.post("/ibkr/settings")
def update_ibkr_settings(payload: IBKRSettingsRequest, request: Request):
    user = require_control_operator(request)
    try:
        return save_ibkr_settings(user["id"], **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ibkr/status")
def get_ibkr_status(request: Request):
    user = require_control_operator(request)
    return ibkr_connection_status(user["id"])


@router.post("/ibkr/sync")
async def sync_ibkr(request: Request):
    user = require_control_operator(request)
    try:
        return await asyncio.to_thread(sync_ibkr_account, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ibkr/export-current-portfolio")
def export_ibkr_current_portfolio(request: Request):
    user = require_control_operator(request)
    return export_active_portfolio_payload(user["id"])


@router.post("/ibkr/keepalive")
def ibkr_keepalive(request: Request):
    require_control_operator(request)
    return run_ibkr_keepalive()


@router.get("/ibkr/reconcile/{candidate_id}")
async def ibkr_reconcile(candidate_id: int, request: Request):
    user = require_control_operator(request)
    try:
        return await asyncio.to_thread(reconcile_ibkr_target, user["id"], candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ibkr/audit")
def ibkr_audit(request: Request):
    user = require_control_operator(request)
    return {"rows": ibkr_audit_log(user["id"]), "live_confirmation_phrase": LIVE_CONFIRMATION}


@router.get("/ibkr/orders")
def ibkr_orders(request: Request):
    user = require_control_operator(request)
    return {"rows": latest_ibkr_links(user["id"])}


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
