from fastapi.testclient import TestClient

import backend.database as database
from backend.main import app


def _signup(client: TestClient) -> str:
    response = client.post(
        "/api/auth/signup",
        json={"email": "owner-debug@example.com", "password": "strong-password", "accept_legal": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_owner_debug_requires_exact_peer_ip_and_never_rewrites_billing(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "debug.db"))
    monkeypatch.setenv("ORYNTRA_DEBUG_TOOLS_ENABLED", "true")

    with TestClient(app, client=("47.202.51.193", 50000)) as client:
        token = _signup(client)
        headers = {"Authorization": f"Bearer {token}"}
        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO subscriptions (user_id, plan_code, plan_name, status, provider) VALUES (1, 'starter', 'Actual billing record', 'ACTIVE', 'billing')"
            )
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

    with TestClient(app, client=("198.51.100.77", 50000)) as client:
        token = _signup(client)
        response = client.get(
            "/api/internal/debug/access",
            headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": "47.202.51.193"},
        )
    assert response.status_code == 404
