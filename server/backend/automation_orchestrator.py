"""CQC private automation orchestration for prospective research and IBKR mirroring.

This layer coordinates the already-frozen Phase 4 research ledger with optional
Interactive Brokers read-only/paper/live mirroring.  It never permits broker
activity to rewrite the immutable research evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from typing import Any

from .database import get_connection
from .ibkr_adapter import ibkr_settings, sync_ibkr_account, mirror_phase4_orders, reconcile_ibkr_target
from .phase4_ledger import get_candidate
from .phase4_runner import run_candidate_cycle


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False)


def ensure_automation_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS automation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                candidate_id INTEGER,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'RUNNING',
                summary_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS automation_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                candidate_id INTEGER,
                severity TEXT NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL,
                context_json TEXT NOT NULL DEFAULT '{}',
                acknowledged INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_automation_alerts_user_created
                ON automation_alerts(user_id,id DESC);
            CREATE INDEX IF NOT EXISTS idx_automation_runs_user_created
                ON automation_runs(user_id,id DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


def emit_alert(user_id: int, *, code: str, message: str, severity: str = "WARNING",
               candidate_id: int | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_automation_schema()
    sev = str(severity).upper()
    if sev not in {"INFO", "WARNING", "ERROR", "CRITICAL"}:
        sev = "WARNING"
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO automation_alerts
               (user_id,candidate_id,severity,code,message,context_json,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (int(user_id), candidate_id, sev, str(code)[:80], str(message)[:1000],
             _json(context or {}), _now()),
        )
        conn.commit()
        return {"id": int(cur.lastrowid), "severity": sev, "code": code, "message": message}
    finally:
        conn.close()


def list_alerts(user_id: int, limit: int = 100, *, include_acknowledged: bool = False) -> list[dict[str, Any]]:
    ensure_automation_schema()
    conn = get_connection()
    try:
        clause = "" if include_acknowledged else "AND acknowledged=0"
        rows = conn.execute(
            f"SELECT * FROM automation_alerts WHERE user_id=? {clause} ORDER BY id DESC LIMIT ?",
            (int(user_id), max(1, min(500, int(limit)))),
        ).fetchall()
        result = []
        for row in rows:
            out = dict(row)
            try:
                out["context"] = json.loads(out.pop("context_json") or "{}")
            except Exception:
                out["context"] = {}
            out["acknowledged"] = bool(out["acknowledged"])
            result.append(out)
        return result
    finally:
        conn.close()


def acknowledge_alert(user_id: int, alert_id: int) -> None:
    ensure_automation_schema()
    conn = get_connection()
    try:
        conn.execute("UPDATE automation_alerts SET acknowledged=1 WHERE id=? AND user_id=?", (int(alert_id), int(user_id)))
        conn.commit()
    finally:
        conn.close()


def _latest_decision_diagnostics(candidate_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT diagnostics_json FROM phase4_decisions WHERE candidate_id=? ORDER BY as_of_date DESC,id DESC LIMIT 2",
            (int(candidate_id),),
        ).fetchall()
    finally:
        conn.close()
    parsed = []
    for row in rows:
        try:
            parsed.append(json.loads(row["diagnostics_json"] or "{}"))
        except Exception:
            parsed.append({})
    return (parsed[0] if parsed else None, parsed[1] if len(parsed) > 1 else None)


def _risk_state(diag: dict[str, Any] | None) -> str | None:
    if not diag:
        return None
    risk = diag.get("risk_supervisor") or {}
    audit = risk.get("target_audit") or []
    if audit and isinstance(audit[-1], dict):
        return str(audit[-1].get("state") or audit[-1].get("v203_state") or "") or None
    return None


def _drawdown(candidate_id: int) -> float | None:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT nav FROM phase4_nav WHERE candidate_id=? ORDER BY as_of_date", (int(candidate_id),)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    values = [float(row["nav"]) for row in rows if float(row["nav"]) > 0]
    if not values:
        return None
    peak = max(values)
    return values[-1] / peak - 1.0


def _check_cycle_alerts(user_id: int, candidate_id: int, cycle: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    metadata = cycle.get("data") or {}
    stale = []
    for symbol, item in metadata.items():
        freshness = str(item.get("freshness") or "").lower()
        age = item.get("age_days")
        if freshness not in {"fresh", "current", ""} or (age is not None and int(age) > 4):
            stale.append({"symbol": symbol, "freshness": freshness, "age_days": age})
    if stale:
        alerts.append(emit_alert(user_id, candidate_id=candidate_id, code="DATA_STALE",
                                 message="One or more market-data histories are stale.", context={"symbols": stale}))

    decision = cycle.get("new_decision") or {}
    orders = decision.get("orders") or []
    if orders:
        one_way = sum(abs(float(order.get("weight_delta") or 0.0)) for order in orders) / 2.0
        threshold = float(os.getenv("ORYNTRA_ALERT_TURNOVER_THRESHOLD", "0.50"))
        if one_way > threshold:
            alerts.append(emit_alert(user_id, candidate_id=candidate_id, code="UNUSUAL_TURNOVER",
                                     message=f"Proposed one-way turnover {one_way:.1%} exceeds {threshold:.1%}.",
                                     context={"one_way_turnover": one_way, "threshold": threshold}))
        weights = decision.get("approved_weights") or {}
        gross = sum(float(v) for v in weights.values())
        if gross > 1.000001 or any(float(v) < -1e-12 for v in weights.values()):
            alerts.append(emit_alert(user_id, candidate_id=candidate_id, severity="CRITICAL",
                                     code="CONSTRAINT_VIOLATION", message="Approved target violates long-only/gross constraints.",
                                     context={"gross": gross, "weights": weights}))

    current_diag, prior_diag = _latest_decision_diagnostics(candidate_id)
    current_state, prior_state = _risk_state(current_diag), _risk_state(prior_diag)
    if current_state and prior_state and current_state != prior_state:
        alerts.append(emit_alert(user_id, candidate_id=candidate_id, code="RISK_STATE_CHANGE",
                                 message=f"Risk state changed from {prior_state} to {current_state}.",
                                 context={"previous": prior_state, "current": current_state}))

    reconciliation = cycle.get("reconciliation") or {}
    if reconciliation and reconciliation.get("status") not in {None, "matched"}:
        large = [row for row in reconciliation.get("rows", []) if abs(float(row.get("weight_error") or 0.0)) > 0.02]
        if large:
            alerts.append(emit_alert(user_id, candidate_id=candidate_id, code="RECONCILIATION_DIFFERENCE",
                                     message="Paper portfolio differs materially from the approved target.", context={"rows": large}))

    dd = _drawdown(candidate_id)
    dd_threshold = -abs(float(os.getenv("ORYNTRA_ALERT_DRAWDOWN_THRESHOLD", "0.10")))
    if dd is not None and dd <= dd_threshold:
        alerts.append(emit_alert(user_id, candidate_id=candidate_id, severity="ERROR", code="DRAWDOWN_THRESHOLD",
                                 message=f"Prospective drawdown reached {dd:.1%}.", context={"drawdown": dd, "threshold": dd_threshold}))
    return alerts


def run_candidate_automation(candidate_id: int, user_id: int, *, broker_transport: Any | None = None) -> dict[str, Any]:
    """Run one full private post-close automation iteration.

    Research/paper evidence is always advanced first. Broker synchronization and
    mirroring happen afterward and cannot alter that evidence.
    """
    ensure_automation_schema()
    started = _now()
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO automation_runs(user_id,candidate_id,started_at,status) VALUES (?,?,?,'RUNNING')",
            (int(user_id), int(candidate_id), started),
        )
        run_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    try:
        candidate = get_candidate(candidate_id, user_id=user_id)
        cycle = run_candidate_cycle(candidate_id, user_id)
        alerts = _check_cycle_alerts(user_id, candidate_id, cycle)
        broker = {"mode": ibkr_settings(user_id)["mode"], "skipped": True}
        mode = str(ibkr_settings(user_id)["mode"]).upper()
        if mode != "DISABLED":
            try:
                sync = sync_ibkr_account(user_id, candidate_id=candidate_id, transport=broker_transport)
                reconciliation = reconcile_ibkr_target(
                    user_id, candidate_id, snapshot=sync, transport=broker_transport,
                    tolerance=float(os.getenv("ORYNTRA_IBKR_RECONCILIATION_TOLERANCE", "0.02")),
                )
                mirror = {"skipped": True, "reason": "no_new_orders"}
                decision = cycle.get("new_decision") or {}
                if decision.get("orders"):
                    mirror = mirror_phase4_orders(user_id, candidate_id, decision["orders"], transport=broker_transport)
                if reconciliation.get("status") == "difference":
                    alerts.append(emit_alert(
                        user_id, candidate_id=candidate_id, severity="WARNING", code="BROKER_TARGET_DIFFERENCE",
                        message="Observed IBKR positions differ materially from the latest approved target.",
                        context={
                            "max_abs_weight_error": reconciliation.get("max_abs_weight_error"),
                            "tolerance": reconciliation.get("tolerance"),
                            "rows": [row for row in reconciliation.get("rows", []) if row.get("status") == "DIFFERENCE"],
                        },
                    ))
                broker = {"sync": sync, "reconciliation": reconciliation, "mirror": mirror, "mode": mode}
            except Exception as exc:
                alerts.append(emit_alert(user_id, candidate_id=candidate_id, severity="ERROR", code="BROKER_EXECUTION_FAILURE",
                                         message=str(exc), context={"mode": mode}))
                broker = {"mode": mode, "error": str(exc)}

        fundamentals = cycle.get("fundamentals") or {"status": "not_requested", "coverage_pct": 0.0}
        alpha_model = str(candidate["manifest"]["configuration"].get("alpha_model") or "")
        if alpha_model in {"alpha_v1", "alpha_v12"} and float(fundamentals.get("coverage_pct") or 0.0) < float(os.getenv("ORYNTRA_FUNDAMENTAL_COVERAGE_ALERT_PCT", "20")):
            alerts.append(emit_alert(
                user_id, candidate_id=candidate_id, severity="INFO", code="FUNDAMENTAL_COVERAGE_LOW",
                message=f"{alpha_model} is running primarily on price-based sleeves because point-in-time corporate coverage is limited.",
                context=fundamentals,
            ))
        summary = {
            "run_id": run_id, "candidate_id": candidate_id, "candidate_label": candidate.get("label"),
            "cycle": cycle, "broker": broker, "alerts": alerts,
            "fundamental_refresh": fundamentals,
            "research_ledger_immutable": True,
        }
        conn = get_connection()
        try:
            conn.execute("UPDATE automation_runs SET status='PASSED',finished_at=?,summary_json=? WHERE id=?", (_now(), _json(summary), run_id))
            conn.commit()
        finally:
            conn.close()
        return summary
    except Exception as exc:
        code = "MODEL_FINGERPRINT_CHANGED" if "fingerprint" in str(exc).lower() else "MODEL_FAILURE"
        alert = emit_alert(user_id, candidate_id=candidate_id, severity="CRITICAL", code=code, message=str(exc))
        conn = get_connection()
        try:
            conn.execute("UPDATE automation_runs SET status='FAILED',finished_at=?,summary_json=? WHERE id=?", (_now(), _json({"error": str(exc), "alert": alert}), run_id))
            conn.commit()
        finally:
            conn.close()
        raise


def recent_automation_runs(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    ensure_automation_schema()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM automation_runs WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), max(1, min(200, int(limit))))).fetchall()
        result = []
        for row in rows:
            out = dict(row)
            try:
                out["summary"] = json.loads(out.pop("summary_json") or "{}")
            except Exception:
                out["summary"] = {}
            result.append(out)
        return result
    finally:
        conn.close()
