"""Website contracts retained while updating the private research backend."""
import asyncio

import pytest
from fastapi import HTTPException, Response

from backend import database
from backend.main import app
from backend.routes import auth


def test_website_routes_survive_research_integration():
    paths = {route.path for route in app.routes}
    assert {"/api/billing/checkout", "/api/billing/webhook", "/api/demo/status",
            "/api/auth/account", "/api/intelligence/minerva-scan-upload"} <= paths
    assert not any(path.startswith("/api/control/qoptima") for path in paths)


def test_billing_schema_survives_research_integration(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "website.db"))
    database.init_db()
    with database.get_connection() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"billing_accounts", "stripe_webhook_events"} <= tables
        columns = {row[1] for row in conn.execute("PRAGMA table_info(subscriptions)")}
        assert {"provider_state", "trial_end", "cancel_at_period_end", "updated_at"} <= columns


def test_active_stripe_subscription_prevents_account_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "website.db"))
    database.init_db()
    salt, password_hash = auth._hash_password("test-password")
    with database.get_connection() as conn:
        user_id = conn.execute(
            "INSERT INTO users (email, display_name, password_salt, password_hash) VALUES (?, ?, ?, ?)",
            ("subscriber@example.com", "Subscriber", salt, password_hash),
        ).lastrowid
        conn.execute(
            "INSERT INTO subscriptions (user_id, plan_code, plan_name, provider, provider_ref, status) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "pro", "Oryntra Pro", "stripe", "sub_test", "ACTIVE"),
        )
    monkeypatch.setattr(auth, "require_current_user", lambda request: {"id": user_id})
    with pytest.raises(HTTPException) as raised:
        asyncio.run(auth.delete_account(auth.DeleteAccountRequest(password="test-password"), None, Response()))
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "ACTIVE_SUBSCRIPTION"
    with database.get_connection() as conn:
        assert conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
