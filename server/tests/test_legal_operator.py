from fastapi.testclient import TestClient

from backend.main import app


def test_all_public_legal_routes_use_the_canonical_preformation_operator_copy(monkeypatch):
    monkeypatch.setenv("ORYNTRA_CQC_FORMED_AND_IP_ASSIGNED", "false")
    with TestClient(app) as client:
        for path in ("/legal/terms", "/legal/privacy", "/legal/risk-disclaimer", "/legal/methodology", "/legal/refund", "/legal/contact"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "Oryntra is in development and is not yet operated by Cowles Quantitative Corporation." in response.text
            assert "silasproff@gmail.com" not in response.text


def test_formed_assignment_flag_switches_only_the_configured_public_statement(monkeypatch):
    monkeypatch.setenv("ORYNTRA_CQC_FORMED_AND_IP_ASSIGNED", "true")
    monkeypatch.setenv("ORYNTRA_LEGAL_CONTACT_EMAIL", "legal@cowlesquantcorp.com")
    with TestClient(app) as client:
        response = client.get("/legal/terms")
    assert response.status_code == 200
    assert "Oryntra is owned and operated by Cowles Quantitative Corporation, a Florida corporation." in response.text
    assert "legal@cowlesquantcorp.com" in response.text
