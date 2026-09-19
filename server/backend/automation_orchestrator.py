"""Private Stage 5 post-close automation for frozen Phase 4 candidates.

It records the immutable research cycle first.  There is intentionally no
broker adapter in this module: broker connectivity must remain a separately
reviewed, explicitly enabled operational boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from .database import get_connection
from .phase4_ledger import get_candidate
from .phase4_runner import run_candidate_cycle


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def ensure_automation_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS automation_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          candidate_id INTEGER, started_at TEXT NOT NULL, finished_at TEXT,
          status TEXT NOT NULL DEFAULT 'RUNNING', summary_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS automation_alerts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          candidate_id INTEGER, severity TEXT NOT NULL, code TEXT NOT NULL,
          message TEXT NOT NULL, context_json TEXT NOT NULL DEFAULT '{}',
          acknowledged INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_runs_user_created ON automation_runs(user_id,id DESC);
        CREATE INDEX IF NOT EXISTS idx_automation_alerts_user_created ON automation_alerts(user_id,id DESC);
        """)
        conn.commit()
    finally:
        conn.close()


def emit_alert(user_id: int, *, candidate_id: int | None, code: str, message: str,
               severity: str = "WARNING", context: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_automation_schema()
    severity = severity.upper() if severity.upper() in {"INFO", "WARNING", "ERROR", "CRITICAL"} else "WARNING"
    conn = get_connection()
    try:
        row = conn.execute("INSERT INTO automation_alerts(user_id,candidate_id,severity,code,message,context_json,created_at) VALUES (?,?,?,?,?,?,?)", (int(user_id), candidate_id, severity, str(code)[:80], str(message)[:1000], json.dumps(context or {}, sort_keys=True, default=str), _now()))
        conn.commit()
        return {"id": int(row.lastrowid), "severity": severity, "code": code, "message": message}
    finally:
        conn.close()


def list_alerts(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    ensure_automation_schema()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM automation_alerts WHERE user_id=? AND acknowledged=0 ORDER BY id DESC LIMIT ?", (int(user_id), max(1, min(500, int(limit))))).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["acknowledged"] = bool(item["acknowledged"])
            item["context"] = json.loads(item.get("context_json") or "{}")
            output.append(item)
        return output
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


def recent_automation_runs(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    ensure_automation_schema()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM automation_runs WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), max(1, min(200, int(limit))))).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["summary"] = json.loads(item.get("summary_json") or "{}")
            output.append(item)
        return output
    finally:
        conn.close()


def run_candidate_automation(candidate_id: int, user_id: int) -> dict[str, Any]:
    """Advance one frozen paper/shadow candidate and persist operational evidence."""
    ensure_automation_schema()
    conn = get_connection()
    try:
        row = conn.execute("INSERT INTO automation_runs(user_id,candidate_id,started_at,status) VALUES (?,?,?,'RUNNING')", (int(user_id), int(candidate_id), _now()))
        run_id = int(row.lastrowid); conn.commit()
    finally:
        conn.close()
    try:
        candidate = get_candidate(candidate_id, user_id=user_id)
        cycle = run_candidate_cycle(candidate_id, user_id)
        alerts: list[dict[str, Any]] = []
        stale = [symbol for symbol, item in (cycle.get("data") or {}).items() if str(item.get("freshness") or "").lower() not in {"", "fresh", "current"}]
        if stale: alerts.append(emit_alert(user_id, candidate_id=candidate_id, code="DATA_STALE", message="One or more market histories are stale.", context={"symbols": stale}))
        if not cycle.get("frozen_code_verified", False): alerts.append(emit_alert(user_id, candidate_id=candidate_id, code="MODEL_FINGERPRINT_CHANGED", message="Frozen-code verification did not pass.", severity="CRITICAL"))
        summary = {"run_id": run_id, "candidate_id": candidate_id, "candidate_label": candidate.get("label"), "cycle": cycle, "alerts": alerts, "research_ledger_immutable": True, "broker": {"mode": "DISABLED", "reason": "Stage 5 research automation has no broker transmission path."}}
        conn = get_connection()
        try:
            conn.execute("UPDATE automation_runs SET status='PASSED',finished_at=?,summary_json=? WHERE id=?", (_now(), json.dumps(summary, sort_keys=True, default=str), run_id)); conn.commit()
        finally: conn.close()
        return summary
    except Exception as exc:
        alert = emit_alert(user_id, candidate_id=candidate_id, code="MODEL_FAILURE", message=str(exc), severity="CRITICAL")
        conn = get_connection()
        try:
            conn.execute("UPDATE automation_runs SET status='FAILED',finished_at=?,summary_json=? WHERE id=?", (_now(), json.dumps({"error": str(exc), "alert": alert}, sort_keys=True), run_id)); conn.commit()
        finally: conn.close()
        raise
