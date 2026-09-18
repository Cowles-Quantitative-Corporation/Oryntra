"""Narrow, opt-in owner debug controls.

This is intentionally not a frontend-only feature: it requires an authenticated
session, an explicit deployment switch, and one resolved client IP. Forwarded
IP headers are accepted only from a deployment-configured trusted proxy.
"""
from __future__ import annotations

import ipaddress
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..database import get_connection
from .auth import require_current_user

router = APIRouter()
PLANS = {
    "actual": None,
    "base": ("base", "Base"),
    "pro": ("pro", "Oryntra Pro"),
    "max_bundle": ("max_bundle", "Max Bundle"),
}


def _enabled() -> bool:
    return os.getenv("ORYNTRA_DEBUG_TOOLS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    networks = []
    for raw in os.getenv("ORYNTRA_DEBUG_TRUSTED_PROXY_CIDRS", "").split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else ""
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return None
    # A client can forge CF-Connecting-IP / X-Forwarded-For. Honor either only
    # when the TCP peer is a configured reverse proxy.
    if any(peer_address in network for network in _trusted_proxy_networks()):
        candidate = (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]).strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return None
    return str(peer_address)


def _require_debug_access(request: Request) -> dict:
    user = require_current_user(request)
    # Fail closed.  An owner-control deployment must explicitly name the one
    # allowed address; a source-code default must never accidentally expose it.
    allowed_ip = os.getenv("ORYNTRA_DEBUG_ALLOWED_IP", "").strip()
    if not _enabled() or _client_ip(request) != allowed_ip:
        # Do not reveal whether the tool exists or why it is inaccessible.
        raise HTTPException(status_code=404, detail="Not found")
    return user


class DebugPlanRequest(BaseModel):
    plan: str

    @field_validator("plan")
    @classmethod
    def valid_plan(cls, value: str) -> str:
        clean = str(value or "").strip().lower()
        if clean not in PLANS:
            raise ValueError("Choose actual, base, pro, or max_bundle.")
        return clean


def _active_subscription(conn, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT plan_code, plan_name FROM debug_subscription_overrides WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if row:
        return None if row["plan_code"] == "base" else {**dict(row), "status": "ACTIVE", "provider": "owner_debug"}
    row = conn.execute(
        "SELECT plan_code, plan_name, status, provider FROM subscriptions WHERE user_id=? AND status='ACTIVE' ORDER BY started_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def _test_override(conn, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT plan_code, plan_name FROM debug_subscription_overrides WHERE user_id=?",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


@router.get("/access")
async def access(request: Request):
    user = _require_debug_access(request)
    conn = get_connection()
    try:
        return {
            "enabled": True,
            "subscription": _active_subscription(conn, user["id"]),
            "test_override": _test_override(conn, user["id"]),
        }
    finally:
        conn.close()


@router.post("/subscription")
async def change_subscription(payload: DebugPlanRequest, request: Request):
    user = _require_debug_access(request)
    conn = get_connection()
    try:
        selection = PLANS[payload.plan]
        if selection is None:
            conn.execute("DELETE FROM debug_subscription_overrides WHERE user_id=?", (user["id"],))
        else:
            plan_code, plan_name = selection
            conn.execute(
                """
                INSERT INTO debug_subscription_overrides (user_id, plan_code, plan_name, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    plan_code=excluded.plan_code,
                    plan_name=excluded.plan_name,
                    updated_at=datetime('now')
                """,
                (user["id"], plan_code, plan_name),
            )
        conn.commit()
        return {
            "ok": True,
            "subscription": _active_subscription(conn, user["id"]),
            "test_override": _test_override(conn, user["id"]),
        }
    finally:
        conn.close()
