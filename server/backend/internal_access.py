"""Server-side CQC internal-research authorization.

Membership is provisioned directly in the service database; it is never inferred
from a browser flag, subscription, or an email string.
"""
from __future__ import annotations

import hmac
import os
import secrets

from fastapi import HTTPException, Request

from .database import get_connection
from .routes.auth import require_current_user, _hash_password


def _enabled() -> bool:
    return os.getenv("ORYNTRA_CQC_INTERNAL_RESEARCH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def require_cqc_internal_operator(request: Request) -> dict:
    user = require_current_user(request)
    if not _enabled():
        raise HTTPException(status_code=404, detail="Not found")
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM internal_operator_access WHERE user_id=? AND status='ACTIVE' LIMIT 1",
            (user["id"],),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return user


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"}


def _local_control_user() -> dict:
    """Return a non-loginable principal for an explicitly local control plane."""
    email = "local-control@oryntra.internal"
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            salt, digest = _hash_password(secrets.token_urlsafe(64))
            cur = conn.execute(
                "INSERT INTO users(email,display_name,password_salt,password_hash,legal_version,legal_accepted_at) VALUES (?,?,?,?,?,datetime('now'))",
                (email, "Oryntra Control", salt, digest, "internal-control"),
            )
            user_id = int(cur.lastrowid)
            conn.execute("INSERT OR REPLACE INTO internal_operator_access(user_id,status,granted_at) VALUES (?, 'ACTIVE', datetime('now'))", (user_id,))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {"id": int(row["id"]), "email": str(row["email"]), "display_name": str(row["display_name"] or "Oryntra Control"), "local_control": True}
    finally:
        conn.close()


def require_control_operator(request: Request) -> dict:
    """Allow local control only with loopback, explicit mode, and a strong secret."""
    local_mode = os.getenv("ORYNTRA_CONTROL_LOCAL_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
    secret = os.getenv("ORYNTRA_CONTROL_LOCAL_SECRET", "").strip()
    supplied = request.headers.get("x-oryntra-control-secret", "").strip()
    if local_mode and _is_loopback(request) and len(secret) >= 32 and supplied and hmac.compare_digest(secret, supplied):
        return _local_control_user()
    return require_cqc_internal_operator(request)
