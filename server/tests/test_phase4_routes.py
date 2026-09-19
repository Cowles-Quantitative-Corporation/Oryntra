from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import database
from backend.routes import control_plane


def test_private_control_api_accepts_loopback_high_entropy_secret_and_creates_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "control.db"))
    monkeypatch.setenv("ORYNTRA_CONTROL_LOCAL_MODE", "true")
    monkeypatch.setenv("ORYNTRA_CONTROL_LOCAL_SECRET", "s" * 64)
    database.init_db()
    app = FastAPI()
    app.include_router(control_plane.router, prefix="/api/control")
    headers = {"X-Oryntra-Control-Secret": "s" * 64}
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        registry = client.get("/api/control/registry", headers=headers)
        assert registry.status_code == 200, registry.text
        created = client.post(
            "/api/control/candidates", headers=headers,
            json={
                "label": "Private candidate", "alpha_model": "tba8", "construction": "phase2",
                "risk_model": "v203", "phase3_enabled": True, "benchmark": "spy_qqq_equal",
                "universe": ["AAPL", "MSFT"], "initial_nav": 100000,
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["risk_model_id"] == "v203"
        overview = client.get("/api/control/overview", headers=headers)
        assert overview.status_code == 200
        assert len(overview.json()["candidates"]) == 1


def test_control_secret_fails_closed_off_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "control-nonlocal.db"))
    monkeypatch.setenv("ORYNTRA_CONTROL_LOCAL_MODE", "true")
    monkeypatch.setenv("ORYNTRA_CONTROL_LOCAL_SECRET", "s" * 64)
    database.init_db()
    app = FastAPI()
    app.include_router(control_plane.router, prefix="/api/control")
    with TestClient(app, client=("203.0.113.8", 50000)) as client:
        response = client.get("/api/control/registry", headers={"X-Oryntra-Control-Secret": "s" * 64})
    assert response.status_code in {401, 404}
