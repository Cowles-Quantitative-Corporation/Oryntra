"""Account-scoped encrypted market-data provider credentials.

Plaintext provider keys are accepted only for the duration of the authenticated
request.  They are encrypted before being written to SQLite and are never
returned through an API response, log, or browser payload.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from .database import get_connection

SUPPORTED_PROVIDERS = {"polygon", "twelvedata", "fred"}


def _cipher() -> Fernet:
    raw = os.getenv("ORYNTRA_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CREDENTIAL_ENCRYPTION_NOT_CONFIGURED",
                "message": "Secure provider-key storage is unavailable until the server encryption key is configured.",
            },
        )
    try:
        return Fernet(raw.encode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CREDENTIAL_ENCRYPTION_INVALID",
                "message": "The server credential-encryption key is invalid. Ask the site operator to fix it before saving a provider key.",
            },
        ) from exc


def ensure_provider_credential_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_provider_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                encrypted_api_key TEXT NOT NULL,
                key_version TEXT NOT NULL DEFAULT 'fernet-v1',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_used_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, provider)
            );
            CREATE INDEX IF NOT EXISTS idx_provider_credentials_user
                ON user_provider_credentials(user_id, provider);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail="Choose Polygon, Twelve Data, or FRED.")
    return provider


def credential_status(user_id: int) -> dict[str, Any]:
    ensure_provider_credential_schema()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT provider, created_at, updated_at, last_used_at
                 FROM user_provider_credentials WHERE user_id=?""",
            (int(user_id),),
        ).fetchall()
    finally:
        conn.close()
    saved = {str(row["provider"]): dict(row) for row in rows}
    return {
        "encryption_configured": bool(os.getenv("ORYNTRA_CREDENTIAL_ENCRYPTION_KEY", "").strip()),
        "providers": [
            {
                "provider": provider,
                "saved": provider in saved,
                "updated_at": saved.get(provider, {}).get("updated_at"),
                "last_used_at": saved.get(provider, {}).get("last_used_at"),
            }
            for provider in sorted(SUPPORTED_PROVIDERS)
        ],
        "secrets_returned": False,
    }


def save_credential(user_id: int, provider: str, api_key: str) -> dict[str, Any]:
    clean_provider = _provider(provider)
    secret = str(api_key or "").strip()
    if len(secret) < 8 or len(secret) > 512:
        raise HTTPException(status_code=422, detail="Enter a valid provider API key.")
    encrypted = _cipher().encrypt(secret.encode("utf-8")).decode("utf-8")
    ensure_provider_credential_schema()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO user_provider_credentials
               (user_id, provider, encrypted_api_key, key_version, created_at, updated_at)
               VALUES (?, ?, ?, 'fernet-v1', datetime('now'), datetime('now'))
               ON CONFLICT(user_id, provider) DO UPDATE SET
                 encrypted_api_key=excluded.encrypted_api_key,
                 key_version='fernet-v1', updated_at=datetime('now')""",
            (int(user_id), clean_provider, encrypted),
        )
        conn.commit()
    finally:
        conn.close()
    return credential_status(user_id)


def delete_credential(user_id: int, provider: str) -> dict[str, Any]:
    clean_provider = _provider(provider)
    ensure_provider_credential_schema()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM user_provider_credentials WHERE user_id=? AND provider=?", (int(user_id), clean_provider))
        conn.commit()
    finally:
        conn.close()
    return credential_status(user_id)


def decrypted_credentials(user_id: int) -> dict[str, str]:
    """Return plaintext only to the server request path that calls the provider."""
    ensure_provider_credential_schema()
    cipher = _cipher()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT provider, encrypted_api_key FROM user_provider_credentials WHERE user_id=?", (int(user_id),)
        ).fetchall()
        credentials: dict[str, str] = {}
        for row in rows:
            try:
                credentials[str(row["provider"])] = cipher.decrypt(str(row["encrypted_api_key"]).encode("utf-8")).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError):
                continue
        if credentials:
            conn.execute(
                "UPDATE user_provider_credentials SET last_used_at=? WHERE user_id=?",
                (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"), int(user_id)),
            )
            conn.commit()
        return credentials
    finally:
        conn.close()
