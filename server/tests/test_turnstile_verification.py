"""Local verification failures; no Cloudflare requests are made."""
from io import BytesIO
import urllib.error
import pytest
from fastapi import HTTPException, Request
from backend.routes import auth


def request():
    return Request({"type": "http", "client": ("127.0.0.1", 1234)})


def test_configured_challenge_requires_token_before_network(monkeypatch):
    monkeypatch.setenv("ORYNTRA_TURNSTILE_SECRET_KEY", "INVENTED_TEST_SECRET")
    def forbidden(*args, **kwargs):
        raise AssertionError("must reject missing token before a network call")
    monkeypatch.setattr(auth.urllib.request, "urlopen", forbidden)
    with pytest.raises(HTTPException) as exc:
        auth._verify_turnstile("", request())
    assert exc.value.status_code == 403


def test_challenge_result_is_authoritative(monkeypatch):
    monkeypatch.setenv("ORYNTRA_TURNSTILE_SECRET_KEY", "INVENTED_TEST_SECRET")
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *a, **k: BytesIO(b'{"success": false}'))
    with pytest.raises(HTTPException) as exc:
        auth._verify_turnstile("INVENTED_TOKEN", request())
    assert exc.value.status_code == 403
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *a, **k: BytesIO(b'{"success": true}'))
    auth._verify_turnstile("INVENTED_TOKEN", request())


def test_challenge_outage_does_not_allow_login(monkeypatch):
    monkeypatch.setenv("ORYNTRA_TURNSTILE_SECRET_KEY", "INVENTED_TEST_SECRET")
    def outage(*args, **kwargs):
        raise urllib.error.URLError("invented outage")
    monkeypatch.setattr(auth.urllib.request, "urlopen", outage)
    with pytest.raises(HTTPException) as exc:
        auth._verify_turnstile("INVENTED_TOKEN", request())
    assert exc.value.status_code == 503
