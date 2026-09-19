from __future__ import annotations

import unittest
import os
import tempfile
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import database
from backend.main import app
from backend.routes import auth
from backend.routes.intelligence import IntelligenceScanRequest
from backend.routes.quant import MINERVA_MODEL_ID, _require_model_access


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.email = "security-test@example.com"
        auth._LOGIN_FAILURES.clear()

    def tearDown(self):
        auth._LOGIN_FAILURES.clear()

    def test_login_lockout_starts_after_configured_failures(self):
        for _ in range(auth.LOGIN_MAX_FAILURES - 1):
            self.assertFalse(auth._record_login_failure(self.email))
        self.assertTrue(auth._record_login_failure(self.email))
        with self.assertRaises(HTTPException) as raised:
            auth._reject_if_login_throttled(self.email)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("Retry-After", raised.exception.headers)

    def test_successful_login_state_can_clear_failures(self):
        auth._record_login_failure(self.email)
        auth._clear_login_failures(self.email)
        auth._reject_if_login_throttled(self.email)

    def test_dummy_password_hash_has_a_valid_shape(self):
        self.assertTrue(auth._verify_password("oryntra-invalid-login-placeholder", auth._DUMMY_PASSWORD_SALT, auth._DUMMY_PASSWORD_HASH))

    def test_health_response_has_baseline_security_headers(self):
        with TestClient(app) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "strict-origin-when-cross-origin")

    def test_runtime_exposes_only_public_presentation_configuration(self):
        environment = {
            "ORYNTRA_PARTNER_ADS_ENABLED": "false",
            "ORYNTRA_SUBSCRIPTION_OFFERS_ENABLED": "false",
            "ORYNTRA_TURNSTILE_SITE_KEY": "public-site-key",
            "ORYNTRA_TURNSTILE_SECRET_KEY": "",
        }
        with patch.dict(os.environ, environment, clear=False), TestClient(app) as client:
            response = client.get("/api/app/version")
        payload = response.json()
        self.assertEqual(payload["public_engine"], "v8")
        self.assertFalse(payload["partner_ads"]["enabled"])
        self.assertFalse(payload["subscription_offers_enabled"])
        self.assertEqual(payload["turnstile"], {"enabled": False, "site_key": ""})
        self.assertNotIn("secret", str(payload).lower())

    def test_mobile_turnstile_page_exposes_widget_without_secret(self):
        environment = {
            "ORYNTRA_TURNSTILE_SITE_KEY": "public-mobile-site-key",
            "ORYNTRA_TURNSTILE_SECRET_KEY": "private-test-secret",
        }
        with patch.dict(os.environ, environment, clear=False), TestClient(app) as client:
            response = client.get("/api/auth/turnstile/mobile")
        self.assertEqual(response.status_code, 200)
        self.assertIn("public-mobile-site-key", response.text)
        self.assertIn("mobile_auth", response.text)
        self.assertNotIn("private-test-secret", response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("challenges.cloudflare.com", response.headers["content-security-policy"])

    def test_mobile_turnstile_page_fails_closed_when_unconfigured(self):
        environment = {
            "ORYNTRA_TURNSTILE_SITE_KEY": "",
            "ORYNTRA_TURNSTILE_SECRET_KEY": "",
        }
        with patch.dict(os.environ, environment, clear=False), TestClient(app) as client:
            response = client.get("/api/auth/turnstile/mobile")
        self.assertEqual(response.status_code, 503)

    def test_oauth_configuration_status_never_exposes_secrets(self):
        with patch.dict(os.environ, {"ORYNTRA_GOOGLE_CLIENT_ID": "", "ORYNTRA_GOOGLE_CLIENT_SECRET": "", "ORYNTRA_APPLE_CLIENT_ID": "", "ORYNTRA_APPLE_CLIENT_SECRET": ""}, clear=False):
            status = auth.oauth_provider_status()
        self.assertEqual(status["providers"]["google"], {"enabled": False})
        self.assertEqual(status["providers"]["apple"], {"enabled": False})
        self.assertNotIn("secret", str(status).lower())

    def test_identity_and_one_time_oauth_state_tables_are_created(self):
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            database.DB_PATH = os.path.join(directory, "oryntra-test.db")
            try:
                database.init_db()
                conn = database.get_connection()
                try:
                    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                finally:
                    conn.close()
            finally:
                database.DB_PATH = original_path
        self.assertIn("user_identities", tables)
        self.assertIn("oauth_login_states", tables)

    def test_first_session_exposes_the_daily_subscription_offer_once(self):
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            database.DB_PATH = os.path.join(directory, "oryntra-test.db")
            try:
                with TestClient(app) as client:
                    signup = client.post("/api/auth/signup", json={"email": "offer@example.com", "password": "a-strong-password", "accept_legal": True})
                    self.assertTrue(signup.json()["user"]["show_subscription_offer"])
                    token = signup.json()["token"]
                    first = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
                    second = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            finally:
                database.DB_PATH = original_path
        self.assertTrue(first.json()["user"]["show_subscription_offer"])
        self.assertFalse(second.json()["user"]["show_subscription_offer"])

    def test_verified_provider_email_links_to_existing_password_account(self):
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            database.DB_PATH = os.path.join(directory, "oryntra-test.db")
            try:
                database.init_db()
                salt, password_hash = auth._hash_password("a-password-for-this-test")
                conn = database.get_connection()
                try:
                    created = conn.execute("INSERT INTO users (email, display_name, password_salt, password_hash) VALUES (?, ?, ?, ?)", ("linked@example.com", "Existing", salt, password_hash))
                    user = auth._identity_user(conn, "google", {"sub": "provider-subject", "email": "linked@example.com", "email_verified": True}, {"intent": "login", "accept_legal": 0})
                    conn.commit()
                    identity = conn.execute("SELECT user_id FROM user_identities WHERE provider='google' AND subject='provider-subject'").fetchone()
                finally:
                    conn.close()
            finally:
                database.DB_PATH = original_path
        self.assertEqual(user["id"], created.lastrowid)
        self.assertEqual(identity["user_id"], created.lastrowid)

    def test_minerva_has_a_server_side_subscription_gate(self):
        with self.assertRaises(HTTPException) as raised:
            _require_model_access({"subscription": None}, MINERVA_MODEL_ID)
        self.assertEqual(raised.exception.status_code, 402)
        self.assertEqual(raised.exception.detail["code"], "MODEL_SUBSCRIPTION_REQUIRED")
        with self.assertRaises(HTTPException) as unreleased:
            _require_model_access({"subscription": {"plan_code": "pro"}}, MINERVA_MODEL_ID)
        self.assertEqual(unreleased.exception.status_code, 409)
        self.assertEqual(unreleased.exception.detail["code"], "MODEL_RESEARCH_NOT_RELEASED")

    def test_scanner_accepts_only_the_shared_workspace_model_catalog(self):
        self.assertEqual(IntelligenceScanRequest(ticker="AAPL", model="v8").model, "v8")
        # Older browser clients persisted these labels for the now-V8 public
        # scanner. They must remain usable after a server deployment.
        self.assertEqual(IntelligenceScanRequest(ticker="AAPL", model="official").model, "v8")
        self.assertEqual(IntelligenceScanRequest(ticker="AAPL", model="v8_official").model, "v8")
        with self.assertRaises(ValueError):
            IntelligenceScanRequest(ticker="AAPL", model="universal_v2")
        with self.assertRaises(ValueError):
            IntelligenceScanRequest(ticker="AAPL", model="made_up_model")


if __name__ == "__main__":
    unittest.main()
