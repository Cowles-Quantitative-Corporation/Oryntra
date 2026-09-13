"""Narrow, server-to-server CQC Max entitlement relay.

Oryntra remains the billing authority for the bundle. Rule Mirror receives
only an access decision; it never receives an Oryntra session, password,
portfolio, or subscription record. The endpoint is disabled unless both
services share a high-entropy secret.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import APIRouter, HTTPException, Request

from ..database import get_connection

router = APIRouter()
_PRODUCT_BENEFITS = {
    "oryntra_pro": {"oryntra_pro"},
    "rulemirror_pro": {"rulemirror_pro"},
    "cqc_max": {"cqc_max", "oryntra_pro", "rulemirror_pro"},
}


def _secret() -> bytes:
    return os.getenv("CQC_ENTITLEMENT_SHARED_SECRET", "").strip().encode("utf-8")


def _signature(timestamp: str, subject: str) -> str:
    message = f"GET\n/api/cqc/entitlement\n{timestamp}\n{subject}".encode("utf-8")
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def subject_for_email(email: str) -> str | None:
    """Return the local-only opaque subject; never transmit the email itself."""
    secret = _secret()
    clean = email.strip().lower()
    if len(secret) < 32 or "@" not in clean:
        return None
    return hmac.new(secret, clean.encode("utf-8"), hashlib.sha256).hexdigest()


def active_products_for_subject(conn, subject: str) -> tuple[set[str], str | None]:
    rows = conn.execute(
        """
        SELECT product_code, expires_at FROM cqc_entitlements
         WHERE subject=? AND status='ACTIVE'
           AND (expires_at IS NULL OR expires_at > datetime('now'))
        """,
        (subject,),
    ).fetchall()
    products: set[str] = set()
    expires_at: str | None = None
    for row in rows:
        code = str(row["product_code"]).lower()
        products.update(_PRODUCT_BENEFITS.get(code, set()))
        if row["expires_at"] and (expires_at is None or row["expires_at"] > expires_at):
            expires_at = row["expires_at"]
    return products, expires_at


def active_products_for_email(conn, email: str) -> tuple[set[str], str | None]:
    subject = subject_for_email(email)
    return active_products_for_subject(conn, subject) if subject else (set(), None)


def _verify_service_request(request: Request) -> str:
    secret = _secret()
    if len(secret) < 32:
        raise HTTPException(status_code=404, detail="Not found.")
    subject = request.headers.get("x-cqc-subject", "").strip().lower()
    timestamp = request.headers.get("x-cqc-timestamp", "").strip()
    supplied = request.headers.get("x-cqc-signature", "").strip()
    if len(subject) != 64 or any(char not in "0123456789abcdef" for char in subject) or not timestamp or not supplied:
        raise HTTPException(status_code=401, detail="Invalid CQC entitlement request.")
    try:
        age = abs(int(time.time()) - int(timestamp))
    except ValueError as error:
        raise HTTPException(status_code=401, detail="Invalid CQC entitlement request.") from error
    max_age = max(30, min(900, int(os.getenv("CQC_ENTITLEMENT_MAX_CLOCK_SKEW_SECONDS", "300"))))
    if age > max_age or not hmac.compare_digest(supplied, _signature(timestamp, subject)):
        raise HTTPException(status_code=401, detail="Invalid CQC entitlement request.")
    return subject


@router.get("/entitlement")
def cqc_entitlement(request: Request):
    """Return only effective product access for a signed service caller."""
    subject = _verify_service_request(request)
    conn = get_connection()
    try:
        products, expires_at = active_products_for_subject(conn, subject)
    finally:
        conn.close()
    return {
        "products": sorted(products),
        "active": bool(products),
        "expires_at": expires_at,
    }
