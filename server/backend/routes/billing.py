"""Stripe Billing integration for Oryntra subscriptions.

The browser never receives a Stripe secret key. Signed-in users request a hosted
Checkout Session from this API; Stripe webhooks are the authority for granting
or revoking access. Checkout success query parameters are never treated as
proof of payment or trial status.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..database import get_connection
from .auth import require_current_user
from .cqc_entitlements import subject_for_email

router = APIRouter()

_STRIPE_API_BASE = "https://api.stripe.com/v1"
_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Live price IDs currently configured in the CQC Stripe account. Environment
# variables can replace them without a code deployment if pricing is changed.
_DEFAULT_PRO_PRICE = "price_1UIHgIAoOGrQDasxaSEIim8K"
_DEFAULT_MAX_PRICE = "price_1UII4RAoOGrQDasxOUPRTPBk"
_DEFAULT_PORTAL_CONFIGURATION = "bpc_1UIxYTAoOGrQDasxjX3GSp03"

_PLAN_NAMES = {
    "pro": "Oryntra Pro",
    "cqc_max": "CQC Max",
}


class CheckoutRequest(BaseModel):
    plan_code: str


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _trial_days() -> int:
    try:
        days = int(os.getenv("STRIPE_TRIAL_DAYS", "7"))
    except ValueError:
        days = 7
    return max(1, min(30, days))


def _normalize_plan(value: str) -> str:
    clean = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "oryntra_pro": "pro",
        "plus": "pro",
        "max": "cqc_max",
        "max_bundle": "cqc_max",
        "cqcmax": "cqc_max",
    }
    clean = aliases.get(clean, clean)
    if clean not in _PLAN_NAMES:
        raise HTTPException(status_code=400, detail="Unknown subscription plan.")
    return clean


def _plan_price(plan_code: str) -> str:
    if plan_code == "pro":
        return os.getenv("STRIPE_PRICE_ORYNTRA_PRO", _DEFAULT_PRO_PRICE).strip()
    return os.getenv("STRIPE_PRICE_CQC_MAX", _DEFAULT_MAX_PRICE).strip()


def _stripe_secret() -> str:
    secret = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail={"code": "STRIPE_NOT_CONFIGURED", "message": "Stripe billing is not configured on this server."},
        )
    return secret


def _public_base_url() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "https://oryntraai.com").strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if not parsed.netloc or (parsed.scheme != "https" and not local_http):
        raise HTTPException(status_code=503, detail="PUBLIC_BASE_URL must be an HTTPS URL.")
    return base


def _flatten_form(prefix: str, value: Any, pairs: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}[{key}]" if prefix else str(key)
            _flatten_form(child_prefix, child, pairs)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _flatten_form(f"{prefix}[{index}]", child, pairs)
    elif value is not None:
        if isinstance(value, bool):
            encoded = "true" if value else "false"
        else:
            encoded = str(value)
        pairs.append((prefix, encoded))


def _stripe_request(method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    secret = _stripe_secret()
    url = f"{_STRIPE_API_BASE}{path}"
    body = None
    headers = {
        "Authorization": f"Bearer {secret}",
        "Accept": "application/json",
    }
    if data is not None:
        pairs: list[tuple[str, str]] = []
        _flatten_form("", data, pairs)
        body = urllib.parse.urlencode(pairs).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            message = payload.get("error", {}).get("message") or "Stripe rejected the billing request."
        except Exception:
            message = "Stripe rejected the billing request."
        raise HTTPException(status_code=502, detail={"code": "STRIPE_ERROR", "message": message}) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "STRIPE_UNAVAILABLE", "message": "Stripe is temporarily unavailable. Try again shortly."},
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Stripe returned an invalid response.")
    return payload


def _stripe_get(resource: str, resource_id: str) -> dict[str, Any]:
    if not _ID_RE.match(resource_id or ""):
        raise HTTPException(status_code=400, detail="Invalid Stripe resource identifier.")
    return _stripe_request("GET", f"/{resource}/{urllib.parse.quote(resource_id, safe='')}")


def _unix_to_sql(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def _subscription_price_id(subscription: dict[str, Any]) -> str:
    try:
        items = subscription.get("items", {}).get("data", [])
        if items:
            price = items[0].get("price") or {}
            if isinstance(price, dict):
                return str(price.get("id") or "")
            return str(price or "")
    except (AttributeError, IndexError, TypeError):
        pass
    return ""


def _plan_from_subscription(subscription: dict[str, Any]) -> str | None:
    metadata = subscription.get("metadata") or {}
    raw = metadata.get("plan_code") if isinstance(metadata, dict) else None
    if raw:
        try:
            return _normalize_plan(str(raw))
        except HTTPException:
            pass
    price = _subscription_price_id(subscription)
    if price == os.getenv("STRIPE_PRICE_ORYNTRA_PRO", _DEFAULT_PRO_PRICE).strip():
        return "pro"
    if price == os.getenv("STRIPE_PRICE_CQC_MAX", _DEFAULT_MAX_PRICE).strip():
        return "cqc_max"
    return None


def _local_status(stripe_status: str) -> str:
    status = str(stripe_status or "").strip().lower()
    if status in {"active", "trialing"}:
        return "ACTIVE"
    if status == "past_due":
        return "PAST_DUE"
    if status == "unpaid":
        return "UNPAID"
    if status == "paused":
        return "PAUSED"
    if status in {"canceled", "cancelled"}:
        return "CANCELLED"
    if status == "incomplete":
        return "INCOMPLETE"
    if status == "incomplete_expired":
        return "EXPIRED"
    return (status or "INACTIVE").upper()


def _user_for_subscription(conn, subscription: dict[str, Any], fallback_user_id: int | None = None) -> int | None:
    metadata = subscription.get("metadata") or {}
    raw_user_id = metadata.get("oryntra_user_id") if isinstance(metadata, dict) else None
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        user_id = int(fallback_user_id) if fallback_user_id else 0
    if user_id:
        row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if row:
            return int(row["id"])
    customer_id = str(subscription.get("customer") or "")
    if customer_id:
        row = conn.execute(
            "SELECT user_id FROM billing_accounts WHERE provider='stripe' AND customer_ref=?",
            (customer_id,),
        ).fetchone()
        if row:
            return int(row["user_id"])
    return None


def _upsert_billing_account(conn, user_id: int, customer_id: str) -> None:
    if not customer_id:
        return
    conn.execute(
        """
        INSERT INTO billing_accounts (user_id, provider, customer_ref, created_at, updated_at)
        VALUES (?, 'stripe', ?, datetime('now'), datetime('now'))
        ON CONFLICT(user_id, provider) DO UPDATE SET
            customer_ref=excluded.customer_ref,
            updated_at=datetime('now')
        """,
        (user_id, customer_id),
    )


def _sync_cqc_entitlement(conn, user_id: int, plan_code: str, active: bool, provider_ref: str, expires_at: str | None) -> None:
    row = conn.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return
    subject = subject_for_email(str(row["email"]))
    if not subject:
        return
    # A Stripe plan change must not leave the prior bundle entitlement active.
    conn.execute(
        "UPDATE cqc_entitlements SET status='INACTIVE', updated_at=datetime('now') WHERE subject=? AND provider='stripe'",
        (subject,),
    )
    if not active:
        return
    product_code = "cqc_max" if plan_code == "cqc_max" else "oryntra_pro"
    conn.execute(
        """
        INSERT INTO cqc_entitlements (subject, product_code, status, provider, provider_ref, expires_at, updated_at)
        VALUES (?, ?, 'ACTIVE', 'stripe', ?, ?, datetime('now'))
        ON CONFLICT(subject, product_code) DO UPDATE SET
            status='ACTIVE', provider='stripe', provider_ref=excluded.provider_ref,
            expires_at=excluded.expires_at, updated_at=datetime('now')
        """,
        (subject, product_code, provider_ref, expires_at),
    )


def _sync_subscription(conn, subscription: dict[str, Any], fallback_user_id: int | None = None) -> dict[str, Any] | None:
    subscription_id = str(subscription.get("id") or "")
    if not subscription_id.startswith("sub_"):
        return None
    user_id = _user_for_subscription(conn, subscription, fallback_user_id)
    plan_code = _plan_from_subscription(subscription)
    if not user_id or not plan_code:
        return None

    customer_id = str(subscription.get("customer") or "")
    _upsert_billing_account(conn, user_id, customer_id)

    provider_status = str(subscription.get("status") or "")
    status = _local_status(provider_status)
    started_at = _unix_to_sql(subscription.get("start_date") or subscription.get("created"))
    trial_end = _unix_to_sql(subscription.get("trial_end"))
    current_period_end = _unix_to_sql(subscription.get("current_period_end")) or trial_end
    cancel_at_period_end = 1 if subscription.get("cancel_at_period_end") else 0

    # Only one Stripe subscription should control Oryntra access for an account.
    if status == "ACTIVE":
        conn.execute(
            """
            UPDATE subscriptions
               SET status='REPLACED', updated_at=datetime('now')
             WHERE user_id=? AND provider='stripe' AND provider_ref<>? AND status='ACTIVE'
            """,
            (user_id, subscription_id),
        )

    conn.execute(
        """
        INSERT INTO subscriptions
            (user_id, plan_code, plan_name, status, started_at, current_period_end,
             provider, provider_ref, provider_state, trial_end, cancel_at_period_end, updated_at)
        VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')), ?, 'stripe', ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(provider, provider_ref) DO UPDATE SET
            user_id=excluded.user_id,
            plan_code=excluded.plan_code,
            plan_name=excluded.plan_name,
            status=excluded.status,
            current_period_end=excluded.current_period_end,
            provider_state=excluded.provider_state,
            trial_end=excluded.trial_end,
            cancel_at_period_end=excluded.cancel_at_period_end,
            updated_at=datetime('now')
        """,
        (
            user_id,
            plan_code,
            _PLAN_NAMES[plan_code],
            status,
            started_at,
            current_period_end,
            subscription_id,
            provider_status,
            trial_end,
            cancel_at_period_end,
        ),
    )
    _sync_cqc_entitlement(
        conn,
        user_id,
        plan_code,
        status == "ACTIVE",
        subscription_id,
        current_period_end,
    )
    return {
        "user_id": user_id,
        "plan_code": plan_code,
        "plan_name": _PLAN_NAMES[plan_code],
        "status": status,
        "provider_state": provider_status,
        "trial_end": trial_end,
        "current_period_end": current_period_end,
        "cancel_at_period_end": bool(cancel_at_period_end),
    }


def _existing_stripe_customer(conn, user_id: int) -> str | None:
    row = conn.execute(
        "SELECT customer_ref FROM billing_accounts WHERE user_id=? AND provider='stripe'",
        (user_id,),
    ).fetchone()
    return str(row["customer_ref"]) if row and row["customer_ref"] else None


def _extract_invoice_subscription_id(invoice: dict[str, Any]) -> str:
    direct = invoice.get("subscription")
    if isinstance(direct, str):
        return direct
    parent = invoice.get("parent") or {}
    if isinstance(parent, dict):
        details = parent.get("subscription_details") or {}
        if isinstance(details, dict):
            sub = details.get("subscription")
            if isinstance(sub, str):
                return sub
            if isinstance(sub, dict):
                return str(sub.get("id") or "")
    return ""


def _verify_webhook_signature(raw: bytes, signature_header: str) -> None:
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe webhook signing secret is not configured.")
    timestamp = ""
    signatures: list[str] = []
    for component in signature_header.split(","):
        key, _, value = component.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1" and value:
            signatures.append(value)
    if not timestamp or not signatures:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.")
    try:
        event_time = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.") from exc
    tolerance = max(60, min(1800, int(os.getenv("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300"))))
    if abs(int(time.time()) - event_time) > tolerance:
        raise HTTPException(status_code=400, detail="Expired Stripe webhook signature.")
    signed_payload = timestamp.encode("ascii") + b"." + raw
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, supplied) for supplied in signatures):
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.")


@router.get("/status")
def billing_status():
    return {
        "configured": bool(os.getenv("STRIPE_SECRET_KEY", "").strip()),
        "trial_days": _trial_days(),
        "plans": {
            "pro": {"name": _PLAN_NAMES["pro"], "monthly_usd": 6.99},
            "cqc_max": {"name": _PLAN_NAMES["cqc_max"], "monthly_usd": 10.99},
        },
    }


@router.post("/checkout")
def create_checkout(req: CheckoutRequest, request: Request):
    user = require_current_user(request)
    plan_code = _normalize_plan(req.plan_code)
    conn = get_connection()
    try:
        active = conn.execute(
            """
            SELECT plan_code, provider_ref FROM subscriptions
             WHERE user_id=? AND provider='stripe' AND status='ACTIVE'
             ORDER BY started_at DESC LIMIT 1
            """,
            (user["id"],),
        ).fetchone()
        if active:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SUBSCRIPTION_ALREADY_ACTIVE",
                    "message": "You already have an active Stripe subscription. Use Manage billing to change or cancel it.",
                },
            )
        customer_id = _existing_stripe_customer(conn, user["id"])
    finally:
        conn.close()

    base = _public_base_url()
    metadata = {
        "oryntra_user_id": str(user["id"]),
        "plan_code": plan_code,
        "source": "oryntra_web",
    }
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": _plan_price(plan_code), "quantity": 1}],
        "client_reference_id": str(user["id"]),
        "metadata": metadata,
        "subscription_data": {
            "trial_period_days": _trial_days(),
            "trial_settings": {"end_behavior": {"missing_payment_method": "cancel"}},
            "metadata": metadata,
        },
        "payment_method_collection": "always",
        "automatic_tax": {"enabled": True},
        "submit_type": "subscribe",
        "success_url": f"{base}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/?checkout=cancelled",
        "origin_context": "web",
        "allow_promotion_codes": _env_bool("STRIPE_ALLOW_PROMOTION_CODES", False),
    }
    if _env_bool("STRIPE_MANAGED_PAYMENTS", True):
        params["managed_payments"] = {"enabled": True}
    if customer_id:
        params["customer"] = customer_id
        params["customer_update"] = {"address": "auto", "name": "auto"}
    else:
        params["customer_email"] = str(user.get("email") or "")

    session = _stripe_request("POST", "/checkout/sessions", params)
    url = str(session.get("url") or "")
    if not url.startswith("https://"):
        raise HTTPException(status_code=502, detail="Stripe did not return a checkout URL.")
    return {"url": url, "session_id": session.get("id"), "trial_days": _trial_days(), "plan_code": plan_code}


@router.get("/checkout-status")
def checkout_status(request: Request, session_id: str = Query(min_length=8, max_length=255)):
    user = require_current_user(request)
    session = _stripe_get("checkout/sessions", session_id)
    reference = str(session.get("client_reference_id") or "")
    metadata = session.get("metadata") or {}
    metadata_user = str(metadata.get("oryntra_user_id") or "") if isinstance(metadata, dict) else ""
    if reference != str(user["id"]) and metadata_user != str(user["id"]):
        raise HTTPException(status_code=403, detail="This checkout session belongs to another account.")

    synced = None
    subscription_id = session.get("subscription")
    if isinstance(subscription_id, dict):
        subscription_id = subscription_id.get("id")
    if isinstance(subscription_id, str) and subscription_id.startswith("sub_"):
        subscription = _stripe_get("subscriptions", subscription_id)
        conn = get_connection()
        try:
            customer_id = str(session.get("customer") or subscription.get("customer") or "")
            _upsert_billing_account(conn, user["id"], customer_id)
            synced = _sync_subscription(conn, subscription, fallback_user_id=user["id"])
            conn.commit()
        finally:
            conn.close()
    return {
        "checkout_status": session.get("status"),
        "payment_status": session.get("payment_status"),
        "subscription": synced,
    }


@router.post("/portal")
def create_portal(request: Request):
    user = require_current_user(request)
    conn = get_connection()
    try:
        customer_id = _existing_stripe_customer(conn, user["id"])
        if not customer_id:
            row = conn.execute(
                """
                SELECT provider_ref FROM subscriptions
                 WHERE user_id=? AND provider='stripe' AND provider_ref IS NOT NULL
                 ORDER BY started_at DESC LIMIT 1
                """,
                (user["id"],),
            ).fetchone()
            if row:
                subscription = _stripe_get("subscriptions", str(row["provider_ref"]))
                customer_id = str(subscription.get("customer") or "") or None
                if customer_id:
                    _upsert_billing_account(conn, user["id"], customer_id)
                    conn.commit()
    finally:
        conn.close()
    if not customer_id:
        raise HTTPException(status_code=409, detail={"code": "NO_STRIPE_CUSTOMER", "message": "No Stripe billing account is linked yet."})
    params: dict[str, Any] = {
        "customer": customer_id,
        "return_url": _public_base_url() + "/",
    }
    configuration = os.getenv("STRIPE_PORTAL_CONFIGURATION_ID", _DEFAULT_PORTAL_CONFIGURATION).strip()
    if configuration:
        params["configuration"] = configuration
    portal = _stripe_request("POST", "/billing_portal/sessions", params)
    url = str(portal.get("url") or "")
    if not url.startswith("https://"):
        raise HTTPException(status_code=502, detail="Stripe did not return a billing portal URL.")
    return {"url": url}


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    raw = await request.body()
    _verify_webhook_signature(raw, request.headers.get("stripe-signature", ""))
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook payload.") from exc
    if not isinstance(event, dict) or not event.get("id"):
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook event.")

    event_id = str(event["id"])
    event_type = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object") or {})
    if not isinstance(obj, dict):
        obj = {}

    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM stripe_webhook_events WHERE event_id=?", (event_id,)).fetchone():
            return {"received": True, "duplicate": True}

        if event_type == "checkout.session.completed":
            user_id = None
            try:
                user_id = int(obj.get("client_reference_id") or (obj.get("metadata") or {}).get("oryntra_user_id"))
            except (TypeError, ValueError, AttributeError):
                user_id = None
            customer_id = str(obj.get("customer") or "")
            if user_id and customer_id:
                _upsert_billing_account(conn, user_id, customer_id)
            subscription_id = obj.get("subscription")
            if isinstance(subscription_id, dict):
                subscription_id = subscription_id.get("id")
            if isinstance(subscription_id, str) and subscription_id.startswith("sub_"):
                subscription = _stripe_get("subscriptions", subscription_id)
                _sync_subscription(conn, subscription, fallback_user_id=user_id)

        elif event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "customer.subscription.paused",
            "customer.subscription.resumed",
        }:
            _sync_subscription(conn, obj)

        elif event_type in {"invoice.paid", "invoice.payment_failed"}:
            subscription_id = _extract_invoice_subscription_id(obj)
            if subscription_id.startswith("sub_"):
                subscription = _stripe_get("subscriptions", subscription_id)
                _sync_subscription(conn, subscription)

        conn.execute(
            "INSERT INTO stripe_webhook_events (event_id, event_type, processed_at) VALUES (?, ?, datetime('now'))",
            (event_id, event_type),
        )
        conn.commit()
    finally:
        conn.close()
    return {"received": True}
