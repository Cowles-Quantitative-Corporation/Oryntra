from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.database as database
from backend.main import app
from backend.routes import debug_access


def _signup(client: TestClient) -> str:
    response = client.post(
        "/api/auth/signup",
        json={"email": "owner-debug@example.com", "password": "strong-password", "accept_legal": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _request(peer, headers=None):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers or {})


def test_owner_debug_requires_exact_peer_ip_and_never_rewrites_billing(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "debug.db"))
    monkeypatch.setenv("ORYNTRA_DEBUG_TOOLS_ENABLED", "true")
    monkeypatch.setenv("ORYNTRA_DEBUG_ALLOWED_IP", "47.202.51.193")
    with TestClient(app, client=("47.202.51.193", 50000)) as client:
        token = _signup(client)
        headers = {"Authorization": f"Bearer {token}"}
        conn = database.get_connection()
        try:
            conn.execute("INSERT INTO subscriptions (user_id, plan_code, plan_name, status, provider) VALUES (1, 'starter', 'Actual billing record', 'ACTIVE', 'billing')")
            conn.commit()
        finally:
            conn.close()
        assert client.get("/api/internal/debug/access", headers=headers).status_code == 200
        changed = client.post("/api/internal/debug/subscription", headers=headers, json={"plan": "pro"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["subscription"]["plan_code"] == "pro"
        assert client.get("/api/auth/me", headers=headers).json()["user"]["subscription"]["provider"] == "owner_debug"
        conn = database.get_connection()
        try:
            billing = conn.execute("SELECT plan_code, status, provider FROM subscriptions").fetchone()
            assert tuple(billing) == ("starter", "ACTIVE", "billing")
            assert conn.execute("SELECT plan_code FROM debug_subscription_overrides").fetchone()[0] == "pro"
        finally:
            conn.close()
        restored = client.post("/api/internal/debug/subscription", headers=headers, json={"plan": "actual"})
        assert restored.status_code == 200, restored.text
        assert restored.json()["test_override"] is None
        assert restored.json()["subscription"]["plan_code"] == "starter"


def test_owner_debug_does_not_trust_spoofed_forwarded_ip(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "spoofed-debug.db"))
    monkeypatch.setenv("ORYNTRA_DEBUG_TOOLS_ENABLED", "true")
    monkeypatch.setenv("ORYNTRA_DEBUG_ALLOWED_IP", "47.202.51.193")
    with TestClient(app, client=("198.51.100.77", 50000)) as client:
        token = _signup(client)
        response = client.get("/api/internal/debug/access", headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": "47.202.51.193"})
    assert response.status_code == 404


def test_forwarded_ip_is_ignored_when_the_peer_is_not_a_trusted_proxy(monkeypatch):
    monkeypatch.setenv("ORYNTRA_DEBUG_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    assert debug_access._client_ip(_request("203.0.113.9", {"cf-connecting-ip": "47.202.51.193"})) == "203.0.113.9"


def test_owner_debug_accepts_cloudflare_ip_only_from_configured_loopback_proxy(monkeypatch, tmp_path):
    """Exercise the production tunnel shape without trusting public headers."""
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "trusted-proxy-debug.db"))
    monkeypatch.setenv("ORYNTRA_DEBUG_TOOLS_ENABLED", "true")
    monkeypatch.setenv("ORYNTRA_DEBUG_ALLOWED_IP", "47.202.51.193")
    monkeypatch.setenv("ORYNTRA_DEBUG_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        token = _signup(client)
        response = client.get(
            "/api/internal/debug/access",
            headers={"Authorization": f"Bearer {token}", "CF-Connecting-IP": "47.202.51.193"},
        )
    assert response.status_code == 200, response.text


def test_owner_access_fails_closed_without_an_explicit_allowed_ip(monkeypatch):
    monkeypatch.setattr(debug_access, "require_current_user", lambda request: {"id": 1})
    monkeypatch.setenv("ORYNTRA_DEBUG_TOOLS_ENABLED", "true")
    monkeypatch.delenv("ORYNTRA_DEBUG_ALLOWED_IP", raising=False)
    with pytest.raises(Exception) as blocked:
        debug_access._require_debug_access(_request("47.202.51.193"))
    assert getattr(blocked.value, "status_code", None) == 404
