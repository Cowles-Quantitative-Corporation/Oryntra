"""Private IBKR Client Portal Gateway boundary for CQC proprietary operations.

No broker call happens in DISABLED mode.  Research evidence remains in the
Phase 4 ledger; this module only records broker-side operational audit data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from .database import get_connection

IBKR_MODES = {"DISABLED", "READ_ONLY", "PAPER", "LIVE"}
LIVE_CONFIRMATION = "ENABLE LIVE IBKR TRANSMISSION"
DEFAULT_BASE_URL = "https://localhost:5000/v1/api"


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_gateway_url(value: str) -> str:
    parsed = urlparse(str(value or DEFAULT_BASE_URL).strip().rstrip("/"))
    # Client Portal Gateway is local.  Do not turn a private control setting
    # into an arbitrary outbound-request capability.
    if parsed.scheme != "https" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.port not in {5000, 5001}:
        raise ValueError("IBKR Gateway URL must be local HTTPS on port 5000 or 5001")
    if not parsed.path.rstrip("/").endswith("/v1/api"):
        raise ValueError("IBKR Gateway URL must end with /v1/api")
    return parsed.geturl()


def ensure_ibkr_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS ibkr_settings (
          user_id INTEGER PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'DISABLED', account_id TEXT,
          base_url TEXT NOT NULL DEFAULT 'https://localhost:5000/v1/api', verify_ssl INTEGER NOT NULL DEFAULT 0,
          paper_transmit_enabled INTEGER NOT NULL DEFAULT 0, live_transmit_enabled INTEGER NOT NULL DEFAULT 0,
          live_confirmation TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ibkr_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, candidate_id INTEGER,
          mode TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL, message TEXT,
          payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
        );
        """)
        conn.commit()
    finally: conn.close()


def _raw_settings(user_id: int) -> dict[str, Any]:
    ensure_ibkr_schema(); conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM ibkr_settings WHERE user_id=?", (int(user_id),)).fetchone()
        return dict(row) if row else {"user_id": int(user_id), "mode": "DISABLED", "account_id": None, "base_url": DEFAULT_BASE_URL, "verify_ssl": 0, "paper_transmit_enabled": 0, "live_transmit_enabled": 0, "live_confirmation": None}
    finally: conn.close()


def ibkr_settings(user_id: int) -> dict[str, Any]:
    raw = _raw_settings(user_id)
    return {"user_id": int(user_id), "mode": raw["mode"], "account_id": raw["account_id"], "base_url": raw["base_url"], "verify_ssl": bool(raw["verify_ssl"]), "paper_transmit_enabled": bool(raw["paper_transmit_enabled"]), "live_transmit_enabled": bool(raw["live_transmit_enabled"]), "live_confirmation_set": raw.get("live_confirmation") == LIVE_CONFIRMATION, "live_gate_environment": _enabled("ORYNTRA_ALLOW_LIVE_IBKR")}


def save_ibkr_settings(user_id: int, *, mode: str, account_id: str | None = None, base_url: str = DEFAULT_BASE_URL,
                       verify_ssl: bool = False, paper_transmit_enabled: bool = False,
                       live_transmit_enabled: bool = False, live_confirmation: str | None = None) -> dict[str, Any]:
    clean_mode = str(mode or "DISABLED").upper()
    if clean_mode not in IBKR_MODES: raise ValueError("IBKR mode must be DISABLED, READ_ONLY, PAPER, or LIVE")
    clean_account = str(account_id or "").strip() or None
    if clean_mode in {"PAPER", "LIVE"} and not clean_account: raise ValueError("An IBKR account is required for PAPER or LIVE mode")
    url = _validate_gateway_url(base_url)
    ensure_ibkr_schema(); conn = get_connection()
    try:
        conn.execute("""INSERT INTO ibkr_settings(user_id,mode,account_id,base_url,verify_ssl,paper_transmit_enabled,live_transmit_enabled,live_confirmation,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode,account_id=excluded.account_id,base_url=excluded.base_url,verify_ssl=excluded.verify_ssl,paper_transmit_enabled=excluded.paper_transmit_enabled,live_transmit_enabled=excluded.live_transmit_enabled,live_confirmation=excluded.live_confirmation,updated_at=excluded.updated_at""", (int(user_id), clean_mode, clean_account, url, int(bool(verify_ssl)), int(bool(paper_transmit_enabled)), int(bool(live_transmit_enabled)), str(live_confirmation or "").strip() or None, _now()))
        conn.commit()
    finally: conn.close()
    return ibkr_settings(user_id)


def _audit(user_id: int, *, mode: str, action: str, status: str, message: str | None = None, payload: Any = None, candidate_id: int | None = None) -> None:
    ensure_ibkr_schema(); conn = get_connection()
    try:
        conn.execute("INSERT INTO ibkr_audit(user_id,candidate_id,mode,action,status,message,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)", (int(user_id), candidate_id, mode, action, status, message, json.dumps(payload or {}, sort_keys=True, default=str), _now())); conn.commit()
    finally: conn.close()


def _request(settings: dict[str, Any], method: str, path: str, *, payload: Any = None) -> Any:
    url = f"{_validate_gateway_url(settings['base_url'])}/{path.lstrip('/')}"
    with httpx.Client(verify=bool(settings.get("verify_ssl")), timeout=20) as client:
        response = client.request(method, url, json=payload); response.raise_for_status()
        return response.json() if response.content else None


def ibkr_connection_status(user_id: int) -> dict[str, Any]:
    settings = _raw_settings(user_id); mode = str(settings["mode"]).upper()
    if mode == "DISABLED": return {"mode": mode, "connected": False, "authenticated": False, "reason": "ibkr_disabled", "settings": ibkr_settings(user_id)}
    try:
        status = _request(settings, "POST", "/iserver/auth/status") or {}
        result = {"mode": mode, "connected": bool(status.get("connected")), "authenticated": bool(status.get("authenticated")), "settings": ibkr_settings(user_id)}
        _audit(user_id, mode=mode, action="connection_status", status="OK", payload=result); return result
    except Exception as exc:
        _audit(user_id, mode=mode, action="connection_status", status="ERROR", message=str(exc)); return {"mode": mode, "connected": False, "authenticated": False, "error": str(exc), "settings": ibkr_settings(user_id)}


def ibkr_audit_log(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    ensure_ibkr_schema(); conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM ibkr_audit WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), max(1, min(500, int(limit))))).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"] or "{}")} for row in rows]
    finally: conn.close()
