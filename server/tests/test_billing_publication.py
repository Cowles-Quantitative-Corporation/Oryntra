"""Invented webhook events only: never calls Stripe or creates a payment."""
import hashlib
import hmac
import json
import time
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from backend import database
from backend.routes import billing

SECRET = "INVENTED_SIGNING_SECRET_FOR_TESTS_ONLY"

def signed(raw, timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(SECRET.encode(), str(timestamp).encode()+b"."+raw, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_webhook_authentication_rejects_tampering_and_expired_signatures(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    raw = b'{"id":"evt_invented"}'
    billing._verify_webhook_signature(raw, signed(raw))
    for body, header in [(raw+b" ", signed(raw)), (raw, signed(raw, int(time.time())-3600))]:
        with pytest.raises(HTTPException) as exc:
            billing._verify_webhook_signature(body, header)
        assert exc.value.status_code == 400
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET")
    with pytest.raises(HTTPException) as exc:
        billing._verify_webhook_signature(raw, signed(raw))
    assert exc.value.status_code == 503


def test_subscription_events_deduplicate_then_revoke_access(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path/"billing.db"))
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    database.init_db()
    conn = database.get_connection()
    user = conn.execute("INSERT INTO users (email,password_salt,password_hash) VALUES (?,?,?)",
                        ("billing-fixture@example.com", "INVENTED", "INVENTED")).lastrowid
    conn.commit(); conn.close()
    app = FastAPI(); app.include_router(billing.router, prefix="/billing")
    def event(event_id, status):
        return {"id":event_id,"type":"customer.subscription.updated","data":{"object":{
            "id":"sub_invented","customer":"cus_invented","status":status,
            "metadata":{"oryntra_user_id":str(user),"plan_code":"pro"}}}}
    def send(client, payload):
        raw=json.dumps(payload).encode()
        return client.post("/billing/webhook",content=raw,headers={"stripe-signature":signed(raw)})
    with TestClient(app) as client:
        assert send(client,event("evt_one","active")).status_code == 200
        duplicate=send(client,event("evt_one","canceled"))
        assert duplicate.json()["duplicate"] is True
        conn=database.get_connection()
        assert conn.execute("SELECT status FROM subscriptions").fetchone()[0] == "ACTIVE"
        conn.close()
        assert send(client,event("evt_two","canceled")).status_code == 200
    conn=database.get_connection()
    assert conn.execute("SELECT status FROM subscriptions").fetchone()[0] == "CANCELLED"
    assert conn.execute("SELECT COUNT(*) FROM stripe_webhook_events").fetchone()[0] == 2
    conn.close()


def test_checkout_status_cannot_link_another_users_session(monkeypatch):
    monkeypatch.setattr(billing,"require_current_user",lambda request:{"id":1})
    monkeypatch.setattr(billing,"_stripe_get",lambda *args:{"client_reference_id":"2"})
    with pytest.raises(HTTPException) as exc:
        billing.checkout_status(Request({"type":"http"}),session_id="cs_invented")
    assert exc.value.status_code == 403
