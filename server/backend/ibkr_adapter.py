"""Private Interactive Brokers adapter for CQC automation.

The adapter targets IBKR's local Client Portal Gateway Web API by default.
Broker state is deliberately separate from the immutable Phase 4 research
ledger: approved research orders may be mirrored to IBKR, but broker fills can
never rewrite simulated prospective evidence.

Safety model:
- DISABLED: no broker network calls.
- READ_ONLY: account/position/order synchronization only.
- PAPER: previews are allowed; transmission requires an explicit per-user gate.
- LIVE: transmission requires a per-user gate, exact confirmation phrase, and
  ORYNTRA_IBKR_LIVE_ENABLED=true (legacy ORYNTRA_ALLOW_LIVE_IBKR is accepted).
- A global kill switch, configurable notional/weight caps, duplicate/idempotency
  checks, account allowlisting, schedule checks, and regular-hours gate fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone
import json
import math
import os
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from .database import get_connection

IBKR_MODES = {"DISABLED", "READ_ONLY", "PAPER", "LIVE"}
LIVE_CONFIRMATION = "ENABLE LIVE IBKR TRANSMISSION"
DEFAULT_BASE_URL = "https://localhost:5000/v1/api"


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def _live_environment_enabled() -> bool:
    return _env_bool("ORYNTRA_IBKR_LIVE_ENABLED", False) or _env_bool("ORYNTRA_ALLOW_LIVE_IBKR", False)


def _allowed_accounts() -> set[str]:
    return {x.strip() for x in os.getenv("ORYNTRA_IBKR_ACCOUNT_ALLOWLIST", "").split(",") if x.strip()}


def _regular_market_hours_now() -> bool:
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.weekday() < 5 and dt_time(9, 30) <= now.time().replace(tzinfo=None) <= dt_time(16, 0)


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def _pretrade_validate_local_orders(orders: list[dict[str, Any]]) -> None:
    max_notional = _float_env("ORYNTRA_IBKR_MAX_ORDER_NOTIONAL", 250_000.0)
    max_batch = _float_env("ORYNTRA_IBKR_MAX_BATCH_NOTIONAL", 1_000_000.0)
    max_weight = min(1.0, _float_env("ORYNTRA_IBKR_MAX_POSITION_WEIGHT", 0.20))
    total = 0.0
    for order in orders:
        notional = float(order.get("intended_notional") or 0.0)
        target = float(order.get("target_weight") or 0.0)
        shares = float(order.get("intended_shares") or 0.0)
        if not all(math.isfinite(x) for x in (notional, target, shares)) or notional < 0 or shares <= 0:
            raise ValueError("IBKR pre-trade validation rejected a non-finite/invalid local order")
        if notional > max_notional + 1e-9:
            raise ValueError(f"IBKR order notional cap exceeded ({notional:.2f} > {max_notional:.2f})")
        if target > max_weight + 1e-12:
            raise ValueError(f"IBKR position-weight cap exceeded ({target:.4f} > {max_weight:.4f})")
        total += notional
    if total > max_batch + 1e-9:
        raise ValueError(f"IBKR batch notional cap exceeded ({total:.2f} > {max_batch:.2f})")


def ensure_ibkr_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ibkr_settings (
                user_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'DISABLED',
                account_id TEXT,
                gateway_host TEXT NOT NULL DEFAULT 'localhost',
                gateway_port INTEGER NOT NULL DEFAULT 5000,
                client_id INTEGER NOT NULL DEFAULT 0,
                base_url TEXT NOT NULL DEFAULT 'https://localhost:5000/v1/api',
                verify_ssl INTEGER NOT NULL DEFAULT 0,
                paper_transmit_enabled INTEGER NOT NULL DEFAULT 0,
                live_transmit_enabled INTEGER NOT NULL DEFAULT 0,
                live_confirmation TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS ibkr_order_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                candidate_id INTEGER,
                local_order_id INTEGER NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                conid INTEGER,
                ibkr_order_id TEXT,
                state TEXT NOT NULL DEFAULT 'STAGED',
                request_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS ibkr_account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                candidate_id INTEGER,
                account_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                positions_json TEXT NOT NULL DEFAULT '[]',
                ledger_json TEXT NOT NULL DEFAULT '{}',
                orders_json TEXT NOT NULL DEFAULT '{}',
                captured_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS ibkr_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                candidate_id INTEGER,
                account_id TEXT,
                mode TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_ibkr_audit_user_created ON ibkr_audit(user_id,id DESC);
            CREATE INDEX IF NOT EXISTS idx_ibkr_links_candidate ON ibkr_order_links(candidate_id,local_order_id);
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ibkr_settings)").fetchall()}
        if "gateway_host" not in columns:
            conn.execute("ALTER TABLE ibkr_settings ADD COLUMN gateway_host TEXT NOT NULL DEFAULT 'localhost'")
        if "gateway_port" not in columns:
            conn.execute("ALTER TABLE ibkr_settings ADD COLUMN gateway_port INTEGER NOT NULL DEFAULT 5000")
        if "client_id" not in columns:
            conn.execute("ALTER TABLE ibkr_settings ADD COLUMN client_id INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def ibkr_settings(user_id: int) -> dict[str, Any]:
    ensure_ibkr_schema()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM ibkr_settings WHERE user_id=?", (int(user_id),)).fetchone()
    finally:
        conn.close()
    if row:
        out = dict(row)
        out["verify_ssl"] = bool(out["verify_ssl"])
        out["paper_transmit_enabled"] = bool(out["paper_transmit_enabled"])
        out["live_transmit_enabled"] = bool(out["live_transmit_enabled"])
        out["live_gate_environment"] = _live_environment_enabled()
        out["live_confirmation_set"] = out.pop("live_confirmation", None) == LIVE_CONFIRMATION
        return out
    return {
        "user_id": int(user_id), "mode": "DISABLED", "account_id": None,
        "gateway_host": "localhost", "gateway_port": 5000, "client_id": 0,
        "base_url": DEFAULT_BASE_URL, "verify_ssl": False,
        "paper_transmit_enabled": False, "live_transmit_enabled": False,
        "updated_at": None, "live_gate_environment": _live_environment_enabled(),
        "live_confirmation_set": False,
    }


def save_ibkr_settings(
    user_id: int, *, mode: str, account_id: str | None = None,
    gateway_host: str = "localhost", gateway_port: int = 5000, client_id: int = 0,
    base_url: str = DEFAULT_BASE_URL, verify_ssl: bool = False,
    paper_transmit_enabled: bool = False, live_transmit_enabled: bool = False,
    live_confirmation: str | None = None,
) -> dict[str, Any]:
    ensure_ibkr_schema()
    clean_mode = str(mode or "DISABLED").upper().strip()
    if clean_mode not in IBKR_MODES:
        raise ValueError("IBKR mode must be DISABLED, READ_ONLY, PAPER, or LIVE")
    clean_account = str(account_id or "").strip() or None
    clean_host = str(gateway_host or "localhost").strip()
    if not clean_host or "://" in clean_host or any(ch.isspace() for ch in clean_host):
        raise ValueError("IBKR gateway_host must be a hostname or IP address")
    clean_port = int(gateway_port)
    if not (1 <= clean_port <= 65535):
        raise ValueError("IBKR gateway_port must be within 1..65535")
    clean_client_id = int(client_id)
    if not (0 <= clean_client_id <= 999999):
        raise ValueError("IBKR client_id must be within 0..999999")
    clean_url = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if not clean_url.startswith(("https://localhost", "https://127.0.0.1", "https://api.ibkr.com")):
        raise ValueError("IBKR base URL must be localhost/127.0.0.1 gateway or api.ibkr.com")
    if clean_mode in {"PAPER", "LIVE"} and not clean_account:
        raise ValueError("Choose an IBKR account before enabling PAPER or LIVE mode")
    # A live setting may be stored for future activation, but transmission still
    # requires the environment gate and exact phrase at execution time.
    confirmation = str(live_confirmation or "").strip() or None
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO ibkr_settings
               (user_id,mode,account_id,gateway_host,gateway_port,client_id,base_url,verify_ssl,paper_transmit_enabled,
                live_transmit_enabled,live_confirmation,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 mode=excluded.mode, account_id=excluded.account_id, gateway_host=excluded.gateway_host,
                 gateway_port=excluded.gateway_port, client_id=excluded.client_id, base_url=excluded.base_url,
                 verify_ssl=excluded.verify_ssl, paper_transmit_enabled=excluded.paper_transmit_enabled,
                 live_transmit_enabled=excluded.live_transmit_enabled,
                 live_confirmation=excluded.live_confirmation, updated_at=excluded.updated_at""",
            (int(user_id), clean_mode, clean_account, clean_host, clean_port, clean_client_id, clean_url, 1 if verify_ssl else 0,
             1 if paper_transmit_enabled else 0, 1 if live_transmit_enabled else 0,
             confirmation, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return ibkr_settings(user_id)


def _audit(user_id: int, *, action: str, status: str, mode: str, account_id: str | None = None,
           candidate_id: int | None = None, message: str | None = None, payload: Any = None) -> None:
    ensure_ibkr_schema()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO ibkr_audit(user_id,candidate_id,account_id,mode,action,status,message,payload_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (int(user_id), candidate_id, account_id, mode, action, status, message,
             _json(payload if payload is not None else {}), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def ibkr_audit_log(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    ensure_ibkr_schema()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM ibkr_audit WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (int(user_id), max(1, min(500, int(limit)))),
        ).fetchall()
        result = []
        for row in rows:
            out = dict(row)
            try:
                out["payload"] = json.loads(out.pop("payload_json") or "{}")
            except Exception:
                out["payload"] = {}
            result.append(out)
        return result
    finally:
        conn.close()


@dataclass
class IBKRClient:
    base_url: str = DEFAULT_BASE_URL
    verify_ssl: bool = False
    timeout_seconds: float = 20.0
    transport: Any | None = None

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                 json_body: Any = None) -> Any:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if self.transport is not None:
            return self.transport(method.upper(), url, params=params, json=json_body)
        with httpx.Client(verify=self.verify_ssl, timeout=self.timeout_seconds) as client:
            response = client.request(method.upper(), url, params=params, json=json_body)
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()

    def auth_status(self) -> dict[str, Any]:
        response = self._request("POST", "/iserver/auth/status")
        return response if isinstance(response, dict) else {"authenticated": False, "connected": False}

    def tickle(self) -> Any:
        return self._request("POST", "/tickle")

    def trading_accounts(self) -> dict[str, Any]:
        response = self._request("GET", "/iserver/accounts")
        return response if isinstance(response, dict) else {"accounts": []}

    def portfolio_accounts(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/portfolio/accounts")
        return response if isinstance(response, list) else []

    def positions(self, account_id: str) -> list[dict[str, Any]]:
        self.portfolio_accounts()  # required IBKR preflight
        response = self._request("GET", f"/portfolio2/{account_id}/positions")
        return response if isinstance(response, list) else []

    def ledger(self, account_id: str) -> dict[str, Any]:
        self.portfolio_accounts()
        response = self._request("GET", f"/portfolio/{account_id}/ledger")
        return response if isinstance(response, dict) else {}

    def live_orders(self) -> dict[str, Any]:
        self.trading_accounts()  # required order-monitoring preflight
        response = self._request("GET", "/iserver/account/orders", params={"force": "true"})
        return response if isinstance(response, dict) else {"orders": response if isinstance(response, list) else []}

    def resolve_stock(self, symbol: str) -> dict[str, Any]:
        clean = str(symbol).upper().strip()
        response = self._request("GET", "/iserver/secdef/search", params={"symbol": clean})
        choices = response if isinstance(response, list) else []
        exact = [item for item in choices if str(item.get("symbol", "")).upper() == clean]
        for item in exact + choices:
            sections = item.get("sections") or []
            has_stock = any("STK" in str(section.get("secType", "")) for section in sections)
            if has_stock or not sections:
                conid = item.get("conid")
                if conid is not None:
                    return {**item, "conid": int(conid)}
        raise ValueError(f"IBKR could not resolve a stock contract for {clean}")

    def market_snapshot(self, conid: int) -> Any:
        self.trading_accounts()
        # Required preflight before /whatif; the second request retrieves fields.
        self._request("GET", "/iserver/marketdata/snapshot", params={"conids": str(int(conid)), "fields": "31,84,86"})
        return self._request("GET", "/iserver/marketdata/snapshot", params={"conids": str(int(conid)), "fields": "31,84,86"})

    def preview_order(self, account_id: str, order: dict[str, Any]) -> Any:
        self.market_snapshot(int(order["conid"]))
        return self._request("POST", f"/iserver/account/{account_id}/orders/whatif", json_body={"orders": [order]})

    def place_order(self, account_id: str, order: dict[str, Any]) -> Any:
        self.trading_accounts()
        return self._request("POST", f"/iserver/account/{account_id}/orders", json_body={"orders": [order]})


def _client(settings: dict[str, Any], transport: Any | None = None) -> IBKRClient:
    return IBKRClient(
        base_url=str(settings.get("base_url") or DEFAULT_BASE_URL),
        verify_ssl=bool(settings.get("verify_ssl", False)), transport=transport,
    )


def _raw_settings(user_id: int) -> dict[str, Any]:
    ensure_ibkr_schema()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM ibkr_settings WHERE user_id=?", (int(user_id),)).fetchone()
        return dict(row) if row else {
            "user_id": int(user_id), "mode": "DISABLED", "account_id": None,
            "gateway_host": "localhost", "gateway_port": 5000, "client_id": 0,
            "base_url": DEFAULT_BASE_URL, "verify_ssl": 0, "paper_transmit_enabled": 0,
            "live_transmit_enabled": 0, "live_confirmation": None,
        }
    finally:
        conn.close()


def _transmission_allowed(settings: dict[str, Any]) -> tuple[bool, str]:
    mode = str(settings.get("mode") or "DISABLED").upper()
    account = str(settings.get("account_id") or "").strip()
    if _env_bool("ORYNTRA_IBKR_KILL_SWITCH", False):
        return False, "global_kill_switch_active"
    allowlist = _allowed_accounts()
    if allowlist and account not in allowlist:
        return False, "account_not_allowlisted"
    if mode == "PAPER":
        if not bool(settings.get("paper_transmit_enabled")):
            return False, "paper_transmission_gate_disabled"
    elif mode == "LIVE":
        if not bool(settings.get("live_transmit_enabled")):
            return False, "live_transmission_gate_disabled"
        if str(settings.get("live_confirmation") or "") != LIVE_CONFIRMATION:
            return False, "live_confirmation_missing"
        if not _live_environment_enabled():
            return False, "live_environment_gate_disabled"
    else:
        return False, "mode_does_not_allow_transmission"
    if not _env_bool("ORYNTRA_IBKR_ALLOW_OUTSIDE_RTH", False) and not _regular_market_hours_now():
        return False, "outside_regular_market_hours"
    return True, "transmission_gate_enabled"


def ibkr_connection_status(user_id: int, *, transport: Any | None = None) -> dict[str, Any]:
    settings = _raw_settings(user_id)
    mode = str(settings.get("mode") or "DISABLED").upper()
    if mode == "DISABLED":
        return {"mode": mode, "connected": False, "authenticated": False, "reason": "ibkr_disabled", "settings": ibkr_settings(user_id)}
    client = _client(settings, transport)
    try:
        auth = client.auth_status()
        accounts = client.trading_accounts() if auth.get("authenticated") else {"accounts": []}
        result = {
            "mode": mode, "connected": bool(auth.get("connected")), "authenticated": bool(auth.get("authenticated")),
            "competing": bool(auth.get("competing", False)), "message": auth.get("message"),
            "accounts": accounts.get("accounts", []), "selected_account": settings.get("account_id"),
            "settings": ibkr_settings(user_id),
        }
        _audit(user_id, action="connection_status", status="OK", mode=mode, account_id=settings.get("account_id"), payload=result)
        return result
    except Exception as exc:
        _audit(user_id, action="connection_status", status="ERROR", mode=mode, account_id=settings.get("account_id"), message=str(exc))
        return {"mode": mode, "connected": False, "authenticated": False, "error": str(exc), "settings": ibkr_settings(user_id)}


def sync_ibkr_account(user_id: int, *, candidate_id: int | None = None, transport: Any | None = None) -> dict[str, Any]:
    settings = _raw_settings(user_id)
    mode = str(settings.get("mode") or "DISABLED").upper()
    if mode == "DISABLED":
        return {"mode": mode, "skipped": True, "reason": "ibkr_disabled"}
    account = str(settings.get("account_id") or "").strip()
    client = _client(settings, transport)
    auth = client.auth_status()
    if not auth.get("authenticated"):
        raise ValueError("IBKR Client Portal Gateway is not authenticated")
    trading = client.trading_accounts()
    accounts = [str(value) for value in trading.get("accounts", [])]
    if not account:
        if len(accounts) == 1:
            account = accounts[0]
        else:
            raise ValueError("Select an IBKR account in Oryntra Control")
    if accounts and account not in accounts:
        raise ValueError("Configured IBKR account is not available in the authenticated session")
    positions = client.positions(account)
    ledger = client.ledger(account)
    orders = client.live_orders()
    snapshot = {
        "mode": mode, "account_id": account, "positions": positions, "ledger": ledger,
        "orders": orders, "captured_at": _now(), "authenticated": True,
    }
    ensure_ibkr_schema()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO ibkr_account_snapshots
               (user_id,candidate_id,account_id,mode,positions_json,ledger_json,orders_json,captured_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (int(user_id), candidate_id, account, mode, _json(positions), _json(ledger), _json(orders), snapshot["captured_at"]),
        )
        conn.commit()
    finally:
        conn.close()
    _audit(user_id, action="account_sync", status="OK", mode=mode, account_id=account, candidate_id=candidate_id,
           payload={"position_count": len(positions), "order_count": len(orders.get("orders", [])) if isinstance(orders, dict) else None})
    return snapshot


def _order_payload(local_order: dict[str, Any], conid: int) -> dict[str, Any]:
    quantity = float(local_order.get("intended_shares") or 0.0)
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("Local order has no positive intended share quantity")
    return {
        "acctId": None,  # filled immediately before request
        "conid": int(conid),
        "orderType": "MKT",
        "side": str(local_order["side"]).upper(),
        "tif": "DAY",
        "quantity": quantity,
        "outsideRTH": False,
        "referrer": f"ORYNTRA-PHASE4-{int(local_order['id'])}",
    }


def mirror_phase4_orders(
    user_id: int, candidate_id: int, orders: list[dict[str, Any]], *, transport: Any | None = None,
) -> dict[str, Any]:
    """Stage/preview/transmit newly approved Phase 4 orders without mutating its ledger."""
    settings = _raw_settings(user_id)
    mode = str(settings.get("mode") or "DISABLED").upper()
    if mode in {"DISABLED", "READ_ONLY"}:
        return {"mode": mode, "staged": 0, "submitted": 0, "skipped": True, "reason": "non_transmitting_mode"}
    account = str(settings.get("account_id") or "").strip()
    if not account:
        raise ValueError("IBKR order mirroring requires a configured account")
    _pretrade_validate_local_orders(orders)
    client = _client(settings, transport)
    auth = client.auth_status()
    if not auth.get("authenticated"):
        raise ValueError("IBKR Client Portal Gateway is not authenticated")
    allowed, gate_reason = _transmission_allowed(settings)
    open_orders = client.live_orders()
    open_rows = open_orders.get("orders", []) if isinstance(open_orders, dict) else (open_orders if isinstance(open_orders, list) else [])
    results: list[dict[str, Any]] = []
    for local in orders:
        local_id = int(local["id"])
        ensure_ibkr_schema()
        conn = get_connection()
        try:
            existing = conn.execute("SELECT * FROM ibkr_order_links WHERE local_order_id=?", (local_id,)).fetchone()
        finally:
            conn.close()
        if existing:
            results.append({"local_order_id": local_id, "state": "DUPLICATE_SKIPPED", "ibkr_order_id": existing["ibkr_order_id"]})
            continue
        contract = client.resolve_stock(str(local["symbol"]))
        payload = _order_payload(local, int(contract["conid"]))
        payload["acctId"] = account
        idempotency_key = f"ORYNTRA-{int(candidate_id)}-{local_id}"
        payload["cOID"] = idempotency_key
        remote_duplicate = next((row for row in open_rows if str(row.get("cOID") or row.get("order_ref") or row.get("orderRef") or "") in {idempotency_key, payload["referrer"]}), None)
        if remote_duplicate is not None:
            results.append({"local_order_id": local_id, "state": "REMOTE_DUPLICATE_SKIPPED", "idempotency_key": idempotency_key})
            continue
        preview = client.preview_order(account, payload)
        response: Any = {"preview": preview}
        state = "PREVIEWED"
        ibkr_order_id = None
        scheduled = datetime.fromisoformat(str(local.get("scheduled_for") or "")[:10]).date() if local.get("scheduled_for") else None
        today_et = datetime.now(ZoneInfo("America/New_York")).date()
        max_order_age_days = max(0, int(_float_env("ORYNTRA_IBKR_MAX_ORDER_AGE_DAYS", 3)))
        stale_order = scheduled is not None and (today_et - scheduled).days > max_order_age_days
        order_allowed = allowed and (scheduled is None or scheduled <= today_et) and not stale_order
        order_gate = gate_reason
        if allowed and scheduled is not None and scheduled > today_et:
            order_gate = "order_not_scheduled_yet"
        elif allowed and stale_order:
            order_gate = "stale_local_order"
        if order_allowed:
            submitted = client.place_order(account, payload)
            response = {"preview": preview, "submit": submitted}
            state = "SUBMITTED"
            if isinstance(submitted, list) and submitted:
                first = submitted[0] if isinstance(submitted[0], dict) else {}
                # Do not auto-acknowledge IBKR confirmation/reply prompts.  Such
                # orders remain awaiting operator acknowledgement by design.
                if first.get("id") and not first.get("order_id"):
                    state = "REPLY_REQUIRED"
                ibkr_order_id = first.get("order_id") or first.get("orderId")
        else:
            state = f"PREVIEW_ONLY:{order_gate}"
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO ibkr_order_links
                   (user_id,candidate_id,local_order_id,account_id,mode,conid,ibkr_order_id,state,request_json,response_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(user_id), int(candidate_id), local_id, account, mode, int(contract["conid"]),
                 str(ibkr_order_id) if ibkr_order_id is not None else None, state, _json(payload), _json(response), _now(), _now()),
            )
            conn.commit()
        finally:
            conn.close()
        _audit(user_id, candidate_id=candidate_id, account_id=account, mode=mode,
               action="order_mirror", status="OK", message=state,
               payload={"local_order_id": local_id, "symbol": local["symbol"], "gate": order_gate, "idempotency_key": idempotency_key})
        results.append({"local_order_id": local_id, "symbol": local["symbol"], "state": state, "ibkr_order_id": ibkr_order_id})
    return {
        "mode": mode, "account_id": account, "gate": gate_reason,
        "staged": len(results), "submitted": sum(1 for item in results if item["state"] == "SUBMITTED"),
        "results": results,
    }


def latest_ibkr_links(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    ensure_ibkr_schema()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM ibkr_order_links WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (int(user_id), max(1, min(500, int(limit)))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _recursive_numeric(value: Any, keys: set[str]) -> float | None:
    """Best-effort extraction for IBKR ledger/account payloads across gateway versions."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().replace("_", "") in keys:
                try:
                    number = float(item)
                    if math.isfinite(number):
                        return number
                except (TypeError, ValueError):
                    pass
        for item in value.values():
            found = _recursive_numeric(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _recursive_numeric(item, keys)
            if found is not None:
                return found
    return None


def _position_symbol(row: dict[str, Any]) -> str | None:
    for key in ("ticker", "symbol", "contractDesc", "description"):
        raw = str(row.get(key) or "").strip().upper()
        if raw:
            # contractDesc may include spaces/exchange text; the first token is
            # the useful equity symbol for target reconciliation.
            return raw.split()[0]
    return None


def _position_market_value(row: dict[str, Any]) -> float | None:
    for key in ("mktValue", "marketValue", "market_value"):
        try:
            value = float(row.get(key))
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            pass
    return None


def reconcile_ibkr_target(
    user_id: int, candidate_id: int, *, snapshot: dict[str, Any] | None = None,
    transport: Any | None = None, tolerance: float = 0.02,
) -> dict[str, Any]:
    """Compare the latest approved Phase 4 target with observed IBKR weights.

    This is an operational diagnostic only.  It never writes broker positions
    back into the immutable Phase 4 research ledger.
    """
    from .phase4_ledger import latest_target_weights

    snap = snapshot or sync_ibkr_account(user_id, candidate_id=candidate_id, transport=transport)
    if snap.get("skipped"):
        return {"status": "unavailable", "reason": snap.get("reason"), "rows": []}
    target = latest_target_weights(candidate_id)
    ledger = snap.get("ledger") or {}
    nlv = _recursive_numeric(ledger, {"netliquidationvalue", "netliquidation", "netliq", "nlv"})
    if nlv is None or not math.isfinite(nlv) or abs(nlv) <= 1e-9:
        return {
            "status": "unavailable", "reason": "ibkr_net_liquidation_not_available",
            "account_id": snap.get("account_id"), "rows": [],
        }
    actual: dict[str, float] = {}
    unrecognized: list[dict[str, Any]] = []
    for row in snap.get("positions") or []:
        if not isinstance(row, dict):
            continue
        symbol = _position_symbol(row)
        market_value = _position_market_value(row)
        if not symbol or market_value is None:
            unrecognized.append({"row": row})
            continue
        actual[symbol] = actual.get(symbol, 0.0) + market_value / float(nlv)

    symbols = sorted(set(target) | set(actual))
    rows = []
    max_error = 0.0
    for symbol in symbols:
        approved = float(target.get(symbol, 0.0))
        observed = float(actual.get(symbol, 0.0))
        error = observed - approved
        max_error = max(max_error, abs(error))
        rows.append({
            "symbol": symbol, "approved_weight": approved, "broker_weight": observed,
            "weight_error": error, "status": "MATCH" if abs(error) <= tolerance else "DIFFERENCE",
        })
    status = "matched" if max_error <= tolerance else "difference"
    result = {
        "status": status, "account_id": snap.get("account_id"), "net_liquidation": float(nlv),
        "tolerance": float(tolerance), "max_abs_weight_error": max_error, "rows": rows,
        "unrecognized_positions": unrecognized, "research_ledger_unchanged": True,
    }
    _audit(user_id, action="target_reconciliation", status="OK", mode=str(snap.get("mode") or "READ_ONLY"),
           account_id=snap.get("account_id"), candidate_id=candidate_id,
           payload={"status": status, "max_abs_weight_error": max_error, "row_count": len(rows)})
    return result


def ibkr_keepalive_sweep(*, transport: Any | None = None) -> dict[str, Any]:
    """Tickle every configured non-disabled IBKR gateway session.

    Client Portal sessions can expire quickly, so the private runtime calls this
    on a short cadence independent of the once-per-hour research sweep.
    """
    ensure_ibkr_schema()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT user_id FROM ibkr_settings WHERE UPPER(mode) != 'DISABLED' ORDER BY user_id"
        ).fetchall()
    finally:
        conn.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        user_id = int(row["user_id"])
        settings = _raw_settings(user_id)
        mode = str(settings.get("mode") or "DISABLED").upper()
        try:
            response = _client(settings, transport).tickle()
            _audit(user_id, action="keepalive", status="OK", mode=mode, account_id=settings.get("account_id"))
            results.append({"user_id": user_id, "mode": mode, "ok": True, "response": response})
        except Exception as exc:
            _audit(user_id, action="keepalive", status="ERROR", mode=mode, account_id=settings.get("account_id"), message=str(exc))
            results.append({"user_id": user_id, "mode": mode, "ok": False, "error": str(exc)})
    return {
        "configured_sessions": len(results),
        "success_count": sum(1 for item in results if item["ok"]),
        "error_count": sum(1 for item in results if not item["ok"]),
        "results": results,
        "at": _now(),
    }
