from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import backend.database as database
from backend.main import app
from backend.routes import portfolio_lab


def _bars() -> list[dict]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {"timestamp": (start + timedelta(days=index)).isoformat(), "open": 100 + index * .1,
         "high": 101 + index * .1, "low": 99 + index * .1, "close": 100.5 + index * .1,
         "volume": 1_000_000}
        for index in range(254)
    ]


def test_portfolio_lab_is_internal_only_and_never_unlocked_by_a_subscription(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "portfolio.db"))
    monkeypatch.setenv("ORYNTRA_BROWSER_DIRECT_ANALYSIS_ENABLED", "true")
    monkeypatch.setattr(portfolio_lab, "_run_decision", lambda _: (
        {"dataset_fingerprint": "dataset-fingerprint", "config_fingerprint": "config-fingerprint",
         "universe": {"end": "2025-01-31"}, "position_policy": {"enabled": True},
         "position_policy_execution": {"open_states": []}},
        [{"symbol": "AAPL", "action": "BUY", "target_weight_pct": 25.0, "prior_weight_pct": 0.0,
          "reference_close": 123.45, "estimated_target_shares": 2024.0,
          "rationale": "Completed-close target increased.", "lifecycle": {}}],
    ))
    request = {"label": "My ledger", "histories": [{"ticker": "AAPL", "bars": _bars()}, {"ticker": "MSFT", "bars": _bars()}]}
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "pro@example.com", "password": "strong-password", "accept_legal": True})
        token = signup.json()["token"]
        blocked = client.post("/api/portfolio-lab/run-upload", headers={"Authorization": f"Bearer {token}"}, json=request)
        assert blocked.status_code == 404
        conn = database.get_connection()
        conn.execute("INSERT INTO subscriptions (user_id, plan_code, plan_name, status) VALUES (1, 'pro', 'Oryntra Pro', 'ACTIVE')")
        conn.commit()
        conn.close()
        still_blocked = client.post("/api/portfolio-lab/run-upload", headers={"Authorization": f"Bearer {token}"}, json=request)
        assert still_blocked.status_code == 404


def test_portfolio_lab_requires_server_provisioned_internal_operator(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "portfolio-internal.db"))
    monkeypatch.setenv("ORYNTRA_CQC_INTERNAL_RESEARCH_ENABLED", "true")
    monkeypatch.setattr(portfolio_lab, "_run_decision", lambda _: (
        {"dataset_fingerprint": "dataset-fingerprint", "config_fingerprint": "config-fingerprint",
         "universe": {"end": "2025-01-31"}, "position_policy": {"enabled": True},
         "position_policy_execution": {"open_states": []}},
        [{"symbol": "AAPL", "action": "BUY", "target_weight_pct": 25.0, "prior_weight_pct": 0.0,
          "reference_close": 123.45, "estimated_target_shares": 2024.0,
          "rationale": "Internal test", "lifecycle": {}}],
    ))
    request = {"label": "Internal", "histories": [{"ticker": "AAPL", "bars": _bars()}, {"ticker": "MSFT", "bars": _bars()}]}
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={"email": "operator@example.com", "password": "strong-password", "accept_legal": True})
        token = signup.json()["token"]
        conn = database.get_connection()
        conn.execute("INSERT INTO internal_operator_access (user_id) VALUES (1)")
        conn.commit()
        conn.close()
        response = client.post("/api/portfolio-lab/run-upload", headers={"Authorization": f"Bearer {token}"}, json=request)
    assert response.status_code == 200, response.text
