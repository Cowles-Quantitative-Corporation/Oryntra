"""Small local scheduler for Phase 4 prospective shadow/paper candidates.

The scheduler is enabled only by the private desktop/runtime environment. It
advances the immutable research/paper cycle and may mirror approved orders to
IBKR only when the separately configured broker transmission gates allow it.
"""
from __future__ import annotations

from datetime import datetime
import os
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from .database import get_connection
from .automation_orchestrator import run_candidate_automation as _run_candidate_automation
from .ibkr_adapter import ibkr_keepalive_sweep

# Compatibility alias retained for existing scheduler tests and callers.
run_candidate_cycle = _run_candidate_automation

_ET = ZoneInfo("America/New_York")
_STATUS: dict[str, Any] = {
    "running": False,
    "last_sweep_at": None,
    "last_success_count": 0,
    "last_error_count": 0,
    "last_errors": [],
    "ibkr_last_keepalive_at": None,
    "ibkr_keepalive_success_count": 0,
    "ibkr_keepalive_error_count": 0,
}
_LOCK = threading.Lock()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def scheduler_status() -> dict[str, Any]:
    with _LOCK:
        out = dict(_STATUS)
    out.update({
        "enabled": _env_bool("ORYNTRA_PHASE4_AUTO_CYCLE", False),
        "interval_seconds": max(300, int(os.getenv("ORYNTRA_PHASE4_AUTO_INTERVAL_SECONDS", "3600"))),
        "earliest_hour_et": max(0, min(23, int(os.getenv("ORYNTRA_PHASE4_AUTO_HOUR_ET", "18")))),
        "execution_boundary": "immutable Phase 4 paper ledger plus optional separately gated IBKR mirroring",
        "ibkr_keepalive_enabled": _env_bool("ORYNTRA_IBKR_KEEPALIVE", True),
        "ibkr_keepalive_seconds": max(30, int(os.getenv("ORYNTRA_IBKR_KEEPALIVE_SECONDS", "60"))),
    })
    return out


def _active_candidates() -> list[tuple[int, int]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id,user_id FROM phase4_candidates WHERE status IN ('SHADOW','PAPER') ORDER BY id"
        ).fetchall()
        return [(int(row["id"]), int(row["user_id"])) for row in rows]
    finally:
        conn.close()


def _eligible_now() -> bool:
    now = datetime.now(_ET)
    if now.weekday() >= 5:
        return False
    return now.hour >= scheduler_status()["earliest_hour_et"]


def run_automation_sweep(*, force: bool = False) -> dict[str, Any]:
    if not force and not _eligible_now():
        return {**scheduler_status(), "skipped": True, "skip_reason": "outside_post_close_window"}
    successes = 0
    errors: list[dict[str, Any]] = []
    for candidate_id, user_id in _active_candidates():
        try:
            run_candidate_cycle(candidate_id, user_id)
            successes += 1
        except Exception as exc:  # keep one candidate from blocking the others
            errors.append({"candidate_id": candidate_id, "type": type(exc).__name__, "error": str(exc)})
    with _LOCK:
        _STATUS.update({
            "last_sweep_at": datetime.now(_ET).isoformat(timespec="seconds"),
            "last_success_count": successes,
            "last_error_count": len(errors),
            "last_errors": errors[-20:],
        })
    return {**scheduler_status(), "skipped": False}


def run_ibkr_keepalive() -> dict[str, Any]:
    if not scheduler_status()["ibkr_keepalive_enabled"]:
        return {"skipped": True, "reason": "keepalive_disabled"}
    result = ibkr_keepalive_sweep()
    with _LOCK:
        _STATUS.update({
            "ibkr_last_keepalive_at": result.get("at"),
            "ibkr_keepalive_success_count": int(result.get("success_count", 0)),
            "ibkr_keepalive_error_count": int(result.get("error_count", 0)),
        })
    return {**result, "skipped": False}


class Phase4Scheduler:
    def __init__(self, interval_seconds: int | None = None):
        self.interval = max(300, int(interval_seconds or os.getenv("ORYNTRA_PHASE4_AUTO_INTERVAL_SECONDS", "3600")))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="oryntra-phase4-auto", daemon=True)

    def start(self):
        with _LOCK:
            _STATUS["running"] = True
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        with _LOCK:
            _STATUS["running"] = False

    def _loop(self):
        # Short initial delay lets database/startup work finish before the first broker/session check.
        if self._stop.wait(10):
            return
        next_sweep = 0.0
        next_keepalive = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if scheduler_status()["ibkr_keepalive_enabled"] and now >= next_keepalive:
                try:
                    run_ibkr_keepalive()
                except Exception as exc:
                    with _LOCK:
                        _STATUS["ibkr_keepalive_error_count"] = 1
                        _STATUS["last_errors"] = [{"type": type(exc).__name__, "error": str(exc), "source": "ibkr_keepalive"}]
                next_keepalive = now + scheduler_status()["ibkr_keepalive_seconds"]
            if _env_bool("ORYNTRA_PHASE4_AUTO_CYCLE", False) and now >= next_sweep:
                try:
                    run_automation_sweep(force=False)
                except Exception as exc:
                    with _LOCK:
                        _STATUS["last_errors"] = [{"type": type(exc).__name__, "error": str(exc), "source": "phase4_sweep"}]
                        _STATUS["last_error_count"] = 1
                next_sweep = now + self.interval
            waits = [5.0]
            if scheduler_status()["ibkr_keepalive_enabled"]:
                waits.append(max(1.0, next_keepalive - time.monotonic()))
            if _env_bool("ORYNTRA_PHASE4_AUTO_CYCLE", False):
                waits.append(max(1.0, next_sweep - time.monotonic()))
            if self._stop.wait(min(waits)):
                return


def start_phase4_scheduler() -> Phase4Scheduler | None:
    # The private runtime may need an IBKR keepalive even when automatic model
    # cycles are disabled.  With no configured broker session this thread is idle.
    if not _env_bool("ORYNTRA_PHASE4_AUTO_CYCLE", False) and not _env_bool("ORYNTRA_IBKR_KEEPALIVE", True):
        return None
    return Phase4Scheduler().start()
