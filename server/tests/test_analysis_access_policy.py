from __future__ import annotations

import pytest
from fastapi import HTTPException

import backend.database as database
from backend.analysis_access import policy_status, refund_quota, reserve_quota, usage_status


def test_personal_mode_is_owner_only(monkeypatch):
    monkeypatch.setenv("ORYNTRA_MARKET_DATA_LICENSE_MODE", "personal_research")
    monkeypatch.setenv("ORYNTRA_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setenv("ORYNTRA_PUBLIC_DERIVED_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("ORYNTRA_BROWSER_DIRECT_ANALYSIS_ENABLED", "false")
    owner = policy_status({"email": "owner@example.com"})
    public = policy_status({"email": "user@example.com"})
    assert owner["analysis_permitted"] is True
    assert public["analysis_permitted"] is False


def test_business_mode_requires_explicit_public_enable(monkeypatch):
    monkeypatch.setenv("ORYNTRA_MARKET_DATA_LICENSE_MODE", "business_approved")
    monkeypatch.setenv("ORYNTRA_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setenv("ORYNTRA_PUBLIC_DERIVED_ANALYSIS_ENABLED", "false")
    monkeypatch.setenv("ORYNTRA_BROWSER_DIRECT_ANALYSIS_ENABLED", "false")
    assert policy_status({"email": "user@example.com"})["analysis_permitted"] is False
    monkeypatch.setenv("ORYNTRA_PUBLIC_DERIVED_ANALYSIS_ENABLED", "true")
    assert policy_status({"email": "user@example.com"})["analysis_permitted"] is True


def test_browser_direct_mode_is_an_explicit_authenticated_public_opt_in(monkeypatch):
    monkeypatch.setenv("ORYNTRA_MARKET_DATA_LICENSE_MODE", "personal_research")
    monkeypatch.setenv("ORYNTRA_PUBLIC_DERIVED_ANALYSIS_ENABLED", "false")
    monkeypatch.setenv("ORYNTRA_BROWSER_DIRECT_ANALYSIS_ENABLED", "true")
    status = policy_status({"email": "user@example.com"})
    assert status["analysis_permitted"] is True
    assert status["browser_direct_analysis_enabled"] is True
    assert status["user_provider_keys_required"] is False


def test_daily_quota_reserve_and_refund(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "quota.db"))
    monkeypatch.setenv("ORYNTRA_DAILY_ANALYSIS_LIMIT", "2")
    database.init_db()
    conn = database.get_connection()
    conn.execute(
        "INSERT INTO users(id, email, display_name, password_salt, password_hash) VALUES (?, ?, ?, ?, ?)",
        (7, "quota@example.com", "Quota User", "00", "00"),
    )
    conn.commit()
    conn.close()
    first = reserve_quota(7, 1)
    assert first["used"] == 1 and first["remaining"] == 1
    second = reserve_quota(7, 1)
    assert second["used"] == 2 and second["remaining"] == 0
    with pytest.raises(HTTPException) as exc:
        reserve_quota(7, 1)
    assert exc.value.status_code == 429
    refunded = refund_quota(7, 1)
    assert refunded["used"] == 1
    assert usage_status(7)["remaining"] == 1


def test_pro_scanner_limit_is_200_and_max_bundle_is_unlimited(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "plans.db"))
    monkeypatch.delenv("ORYNTRA_DAILY_ANALYSIS_LIMIT", raising=False)
    database.init_db()
    conn = database.get_connection()
    conn.executemany(
        "INSERT INTO users(id, email, display_name, password_salt, password_hash) VALUES (?, ?, ?, ?, ?)",
        [(1, "plus@example.com", "Plus", "00", "00"), (2, "max@example.com", "Max", "00", "00")],
    )
    conn.executemany(
        "INSERT INTO subscriptions (user_id, plan_code, plan_name, status) VALUES (?, ?, ?, 'ACTIVE')",
        [(1, "pro", "Oryntra Pro"), (2, "max_bundle", "Max Bundle")],
    )
    conn.commit()
    conn.close()
    assert usage_status(1)["limit"] == 200
    assert usage_status(1)["remaining"] == 200
    assert usage_status(2)["limit"] is None
    assert usage_status(2)["remaining"] is None
