from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from ..database import get_connection
from .scan import run_portfolio_burst

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qoptima-job")
_PRESET_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qoptima-preset")


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


class QOptimaCancelled(RuntimeError):
    pass


def _cancel_requested(job_id: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM qoptima_jobs WHERE id=?", (int(job_id),)).fetchone()
        return bool(row and str(row[0]) == "CANCEL_REQUESTED")
    finally:
        conn.close()


def request_cancel(user_id: int, job_id: int) -> dict[str, Any]:
    ensure_schema()
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM qoptima_jobs WHERE id=? AND user_id=?", (int(job_id), int(user_id))).fetchone()
        if not row:
            raise ValueError("QOptima job not found")
        status = str(row[0])
        if status in {"PASSED", "FAILED", "CANCELLED"}:
            return get_job(user_id, job_id)
        conn.execute(
            "UPDATE qoptima_jobs SET status='CANCEL_REQUESTED',progress_json=? WHERE id=? AND user_id=?",
            (json.dumps({"stage":"cancel_requested","message":"Cancellation requested; stopping at next cooperative checkpoint."}, separators=(",",":")), int(job_id), int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_job(user_id, job_id)


def ensure_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS qoptima_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_type TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'QUEUED',
                result_json TEXT,
                progress_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_qoptima_jobs_user ON qoptima_jobs(user_id,id DESC);
            """
        )
        cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(qoptima_jobs)").fetchall()}
        if "progress_json" not in cols:
            conn.execute("ALTER TABLE qoptima_jobs ADD COLUMN progress_json TEXT")
        conn.commit()
    finally:
        conn.close()


def _decode(row) -> dict[str, Any]:
    result = dict(row)
    result["spec"] = json.loads(result.pop("spec_json") or "{}")
    raw = result.pop("result_json")
    result["result"] = json.loads(raw) if raw else None
    progress_raw = result.pop("progress_json", None)
    result["progress"] = json.loads(progress_raw) if progress_raw else None
    return result


def get_job(user_id: int, job_id: int) -> dict[str, Any]:
    ensure_schema()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM qoptima_jobs WHERE id=? AND user_id=?", (int(job_id), int(user_id))).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError("QOptima job not found")
    return _decode(row)


def list_jobs(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    ensure_schema()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM qoptima_jobs WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), max(1, min(200, int(limit))))).fetchall()
    finally:
        conn.close()
    return [_decode(row) for row in rows]


def _set_progress(job_id: int, payload: dict[str, Any], *, force: bool = False) -> None:
    # Keep database writes lightweight during long statevector searches.
    now = time.time()
    last = getattr(_set_progress, "_last", {})
    previous = float(last.get(job_id, 0.0))
    if not force and now - previous < 2.5:
        return
    last[job_id] = now
    _set_progress._last = last
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE qoptima_jobs SET progress_json=? WHERE id=?",
            (json.dumps(payload, separators=(",", ":"), default=str), int(job_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _run(job_id: int, user_id: int, spec: dict[str, Any]) -> None:
    if _cancel_requested(job_id):
        conn = get_connection()
        try:
            conn.execute("UPDATE qoptima_jobs SET status='CANCELLED',finished_at=? WHERE id=?", (_now(), int(job_id)))
            conn.commit()
        finally:
            conn.close()
        return
    conn = get_connection()
    try:
        conn.execute("UPDATE qoptima_jobs SET status='RUNNING',started_at=? WHERE id=?", (_now(), int(job_id)))
        conn.commit()
    finally:
        conn.close()
    _set_progress(job_id, {"stage": "starting", "message": "QOptima job starting", "percent": 1.0}, force=True)
    try:
        def progress(payload: dict[str, Any]) -> None:
            if _cancel_requested(job_id):
                raise QOptimaCancelled("QOptima cancellation requested")
            _set_progress(job_id, payload)

        if _cancel_requested(job_id):
            raise QOptimaCancelled("QOptima cancellation requested")
        result = run_portfolio_burst(user_id, spec, progress_callback=progress)
        if _cancel_requested(job_id):
            raise QOptimaCancelled("QOptima cancellation requested")
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE qoptima_jobs SET status='PASSED',result_json=?,progress_json=?,finished_at=? WHERE id=?",
                (
                    json.dumps(result, separators=(",", ":"), default=str),
                    json.dumps({"stage": "complete", "message": "QOptima complete", "percent": 100.0}, separators=(",", ":")),
                    _now(),
                    int(job_id),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except QOptimaCancelled as exc:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE qoptima_jobs SET status='CANCELLED',error=?,progress_json=?,finished_at=? WHERE id=?",
                (
                    str(exc),
                    json.dumps({"stage":"cancelled","message":"QOptima job cancelled cooperatively."}, separators=(",",":")),
                    _now(), int(job_id),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE qoptima_jobs SET status='FAILED',error=?,progress_json=?,finished_at=? WHERE id=?",
                (
                    f"{type(exc).__name__}: {exc}",
                    json.dumps({"stage": "failed", "message": f"{type(exc).__name__}: {exc}"}, separators=(",", ":")),
                    _now(),
                    int(job_id),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _run_preset_backtest(job_id: int, user_id: int, spec: dict[str, Any]) -> None:
    from .preset_backtest import run_qpo_preset_backtest, json_safe
    if _cancel_requested(job_id):
        conn = get_connection()
        try:
            conn.execute("UPDATE qoptima_jobs SET status='CANCELLED',finished_at=? WHERE id=?", (_now(), int(job_id)))
            conn.commit()
        finally:
            conn.close()
        return
    conn = get_connection()
    try:
        conn.execute("UPDATE qoptima_jobs SET status='RUNNING',started_at=? WHERE id=?", (_now(), int(job_id)))
        conn.commit()
    finally:
        conn.close()
    _set_progress(job_id, {"stage":"starting","message":"QPO preset backtest starting","percent":1.0}, force=True)
    try:
        def progress(payload: dict[str, Any]) -> None:
            if _cancel_requested(job_id):
                raise QOptimaCancelled("QPO preset backtest cancellation requested")
            _set_progress(job_id, json_safe(payload))

        result = json_safe(run_qpo_preset_backtest(user_id, spec, progress_callback=progress))
        encoded = json.dumps(result, separators=(",", ":"), allow_nan=False)
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE qoptima_jobs SET status='PASSED',result_json=?,progress_json=?,finished_at=? WHERE id=?",
                (encoded, json.dumps({"stage":"complete","message":"QPO preset backtest complete","percent":100.0}, separators=(",", ":")), _now(), int(job_id)),
            )
            conn.commit()
        finally:
            conn.close()
    except QOptimaCancelled as exc:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE qoptima_jobs SET status='CANCELLED',error=?,progress_json=?,finished_at=? WHERE id=?",
                (str(exc), json.dumps({"stage":"cancelled","message":str(exc),"percent":0.0}, separators=(",", ":")), _now(), int(job_id)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        tb = traceback.format_exc()
        tail = tb[-8000:]
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE qoptima_jobs SET status='FAILED',error=?,progress_json=?,finished_at=? WHERE id=?",
                (
                    f"{type(exc).__name__}: {exc}\n{tail}",
                    json.dumps({"stage":"failed","message":f"{type(exc).__name__}: {exc}","percent":0.0,"traceback_tail":tail}, separators=(",", ":"), default=str),
                    _now(), int(job_id),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def submit_preset_backtest(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    from .preset_backtest import json_safe
    safe_spec=json_safe(spec)
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO qoptima_jobs(user_id,job_type,spec_json,status,progress_json) VALUES (?,?,?,'QUEUED',?)",
            (
                int(user_id),
                "qpo_preset_backtest",
                json.dumps(safe_spec, separators=(",", ":"), allow_nan=False),
                json.dumps({"stage":"queued","message":"QPO preset backtest queued","percent":0.0}, separators=(",", ":")),
            ),
        )
        job_id=int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    _PRESET_EXECUTOR.submit(_run_preset_backtest, job_id, int(user_id), dict(safe_spec))
    return get_job(user_id, job_id)


def submit_portfolio_burst(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    mode = str(spec.get("compute_depth") or "validation").lower()
    job_type = "qscan_qpo_qrm_deep_research" if mode == "deep" else "qscan_qpo_qrm_burst"
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO qoptima_jobs(user_id,job_type,spec_json,status,progress_json) VALUES (?,?,?,'QUEUED',?)",
            (
                int(user_id),
                job_type,
                json.dumps(spec, separators=(",", ":"), default=str),
                json.dumps({"stage": "queued", "message": "Queued", "percent": 0.0}, separators=(",", ":")),
            ),
        )
        job_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    _EXECUTOR.submit(_run, job_id, int(user_id), dict(spec))
    return get_job(user_id, job_id)
