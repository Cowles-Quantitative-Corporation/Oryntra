"""Server-side CQC internal-research authorization.

Membership is provisioned directly in the service database; it is never inferred
from a browser flag, subscription, or an email string.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Request

from .database import get_connection
from .routes.auth import require_current_user


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
