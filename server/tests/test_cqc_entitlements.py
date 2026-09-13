import os
import tempfile
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import database
from backend.main import app
from backend.routes.cqc_entitlements import _signature, subject_for_email


def _headers(subject: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "X-CQC-Subject": subject,
        "X-CQC-Timestamp": timestamp,
        "X-CQC-Signature": _signature(timestamp, subject),
    }


def test_cqc_max_relay_returns_only_active_bundle_access():
    original_path = database.DB_PATH
    with tempfile.TemporaryDirectory() as directory, patch.dict(
        os.environ, {"CQC_ENTITLEMENT_SHARED_SECRET": "a" * 48}, clear=False
    ):
        database.DB_PATH = os.path.join(directory, "oryntra-test.db")
        try:
            database.init_db()
            conn = database.get_connection()
            try:
                conn.execute(
                    "INSERT INTO cqc_entitlements (subject, product_code, provider) VALUES (?, 'cqc_max', 'test')",
                    ("a" * 64,),
                )
                conn.commit()
            finally:
                conn.close()
            with TestClient(app) as client:
                response = client.get("/api/cqc/entitlement", headers=_headers("a" * 64))
                rejected = client.get("/api/cqc/entitlement", headers={"X-CQC-Subject": "a" * 64})
        finally:
            database.DB_PATH = original_path
    assert response.status_code == 200
    assert response.json() == {"products": ["cqc_max", "oryntra_pro", "rulemirror_pro"], "active": True, "expires_at": None}
    assert rejected.status_code == 401


def test_cqc_max_is_effective_as_oryntra_pro_for_the_matching_local_user():
    original_path = database.DB_PATH
    with tempfile.TemporaryDirectory() as directory, patch.dict(
        os.environ, {"CQC_ENTITLEMENT_SHARED_SECRET": "a" * 48}, clear=False
    ):
        database.DB_PATH = os.path.join(directory, "oryntra-test.db")
        try:
            database.init_db()
            conn = database.get_connection()
            try:
                conn.execute("INSERT INTO users (email, password_salt, password_hash) VALUES (?, ?, ?)", ("bundle@example.com", "salt", "hash"))
                conn.execute("INSERT INTO cqc_entitlements (subject, product_code, provider) VALUES (?, 'cqc_max', 'test')", (subject_for_email("bundle@example.com"),))
                conn.commit()
                from backend.routes.auth import _active_subscription_for
                subscription = _active_subscription_for(conn, 1)
            finally:
                conn.close()
        finally:
            database.DB_PATH = original_path
    assert subscription["plan_code"] == "cqc_max"
