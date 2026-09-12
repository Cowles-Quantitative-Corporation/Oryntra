from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator

from ..database import get_connection, init_db
from ..provider_credentials import delete_credential, ensure_provider_credential_schema

router = APIRouter()
SESSION_DAYS = 90
SESSION_COOKIE_NAME = "oryntra_session"
SESSION_HINT_COOKIE_NAME = "oryntra_logged_in"
SESSION_MAX_AGE = SESSION_DAYS * 24 * 60 * 60
LOGIN_MAX_FAILURES = max(3, min(20, int(os.getenv("ORYNTRA_LOGIN_MAX_FAILURES", "8"))))
LOGIN_LOCK_SECONDS = max(60, min(24 * 60 * 60, int(os.getenv("ORYNTRA_LOGIN_LOCK_SECONDS", "900"))))
_LOGIN_FAILURES: dict[str, tuple[int, float]] = {}
OAUTH_STATE_MINUTES = 10
OAUTH_PROVIDERS = {
    "google": {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "jwks_endpoint": "https://www.googleapis.com/oauth2/v3/certs",
        "issuer": "https://accounts.google.com",
        "client_id_env": "ORYNTRA_GOOGLE_CLIENT_ID",
        "client_secret_env": "ORYNTRA_GOOGLE_CLIENT_SECRET",
        "redirect_env": "ORYNTRA_GOOGLE_REDIRECT_URI",
        "scopes": "openid email profile",
        "uses_pkce": True,
    },
    "apple": {
        "authorization_endpoint": "https://appleid.apple.com/auth/authorize",
        "token_endpoint": "https://appleid.apple.com/auth/token",
        "jwks_endpoint": "https://appleid.apple.com/auth/keys",
        "issuer": "https://appleid.apple.com",
        "client_id_env": "ORYNTRA_APPLE_CLIENT_ID",
        "client_secret_env": "ORYNTRA_APPLE_CLIENT_SECRET",
        "redirect_env": "ORYNTRA_APPLE_REDIRECT_URI",
        "scopes": "name email",
        "uses_pkce": False,
    },
}


class OAuthFlowError(Exception):
    """A safe, user-visible OAuth failure that never exposes provider tokens."""


class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
    accept_legal: bool = False

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str):
        v = (v or "").lower().strip()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Enter a valid email address.")
        return v

    @field_validator("password")
    @classmethod
    def strong_enough(cls, v: str):
        if len(v or "") < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str):
        v = (v or "").lower().strip()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Enter a valid email address.")
        return v


class SubscribeRequest(BaseModel):
    plan_code: str


class DeleteAccountRequest(BaseModel):
    password: str


class ProviderCredentialRequest(BaseModel):
    provider: str
    api_key: str

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value: str) -> str:
        clean = str(value or "").strip().lower()
        if clean not in {"polygon", "twelvedata"}:
            raise ValueError("Choose Polygon or Twelve Data.")
        return clean

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        clean = str(value or "").strip()
        if len(clean) < 8 or len(clean) > 512:
            raise ValueError("Enter a valid provider API key.")
        return clean


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _expires_at() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")

def _cookie_is_secure(request: Request) -> bool:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _set_session_cookies(response: Response, request: Request, token: str) -> None:
    secure = _cookie_is_secure(request)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=SESSION_HINT_COOKIE_NAME,
        value="1",
        max_age=SESSION_MAX_AGE,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response, request: Request) -> None:
    secure = _cookie_is_secure(request)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", secure=secure, samesite="lax")
    response.delete_cookie(key=SESSION_HINT_COOKIE_NAME, path="/", secure=secure, samesite="lax")


def _hash_password(password: str, salt_hex: Optional[str] = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return salt.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, password_hash: str) -> bool:
    _, candidate = _hash_password(password, salt_hex)
    return hmac.compare_digest(candidate, password_hash)


# Use the same PBKDF2 work for unknown emails. This does not make login a
# complete anti-enumeration solution, but avoids a fast password-hash branch.
_DUMMY_PASSWORD_SALT, _DUMMY_PASSWORD_HASH = _hash_password("oryntra-invalid-login-placeholder")


def _login_failure_key(email: str) -> str:
    return hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()


def _reject_if_login_throttled(email: str) -> None:
    key = _login_failure_key(email)
    failures, locked_until = _LOGIN_FAILURES.get(key, (0, 0.0))
    now = time.monotonic()
    if locked_until and now >= locked_until:
        _LOGIN_FAILURES.pop(key, None)
        return
    if failures >= LOGIN_MAX_FAILURES and now < locked_until:
        retry_after = max(1, int(locked_until - now))
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.", headers={"Retry-After": str(retry_after)})


def _record_login_failure(email: str) -> bool:
    key = _login_failure_key(email)
    failures, locked_until = _LOGIN_FAILURES.get(key, (0, 0.0))
    now = time.monotonic()
    if locked_until and now >= locked_until:
        failures, locked_until = 0, 0.0
    failures += 1
    if failures >= LOGIN_MAX_FAILURES:
        _LOGIN_FAILURES[key] = (failures, now + LOGIN_LOCK_SECONDS)
        return True
    _LOGIN_FAILURES[key] = (failures, 0.0)
    return False


def _clear_login_failures(email: str) -> None:
    _LOGIN_FAILURES.pop(_login_failure_key(email), None)


def _public_user(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"] or "",
        "created_at": row["created_at"],
    }


def _subscription_prompt_date() -> str:
    """The membership reminder has one clear, user-facing timezone contract."""
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _consume_subscription_prompt(conn, user_id: int) -> bool:
    """Return whether this successful sign-in earns the once-per-EST-day offer."""
    row = conn.execute(
        "SELECT last_subscription_prompt_est_date FROM users WHERE id=?", (user_id,)
    ).fetchone()
    today = _subscription_prompt_date()
    if row and row["last_subscription_prompt_est_date"] == today:
        return False
    conn.execute(
        "UPDATE users SET last_subscription_prompt_est_date=? WHERE id=?", (today, user_id)
    )
    return True


def _active_subscription_for(conn, user_id: int) -> Optional[dict]:
    override = conn.execute(
        "SELECT plan_code, plan_name, updated_at FROM debug_subscription_overrides WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if override:
        # Base is a deliberate owner test state. It must not mutate, cancel, or
        # conceal the underlying billing record; it only overrides access while
        # this private diagnostic record exists.
        if override["plan_code"] == "base":
            return None
        return {
            "plan_code": override["plan_code"],
            "plan_name": override["plan_name"],
            "status": "ACTIVE",
            "provider": "owner_debug",
            "started_at": override["updated_at"],
        }
    row = conn.execute(
        """
        SELECT * FROM subscriptions
         WHERE user_id=? AND status='ACTIVE'
         ORDER BY started_at DESC LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def get_auth_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return request.headers.get("x-oryntra-session") or request.cookies.get(SESSION_COOKIE_NAME)


def get_current_user_optional(request: Request) -> Optional[dict]:
    token = get_auth_token(request)
    if not token:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT users.*, user_sessions.show_subscription_offer FROM user_sessions
            JOIN users ON users.id = user_sessions.user_id
            WHERE user_sessions.token=?
              AND user_sessions.expires_at > datetime('now')
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        user = _public_user(row)
        user["show_subscription_offer"] = bool(row["show_subscription_offer"])
        user["subscription"] = _active_subscription_for(conn, user["id"])
        return user
    finally:
        conn.close()


def require_current_user(request: Request) -> dict:
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user


def require_active_subscription(request: Request) -> dict:
    user = require_current_user(request)
    if not user.get("subscription"):
        raise HTTPException(status_code=402, detail={"code": "SUBSCRIPTION_REQUIRED", "message": "Choose an Oryntra AI Pro plan to analyze tickers."})
    return user


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _oauth_state_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _oauth_config(provider: str) -> tuple[dict, str, str, str]:
    config = OAUTH_PROVIDERS.get(provider)
    if config is None:
        raise HTTPException(status_code=404, detail="Unsupported identity provider.")
    client_id = os.getenv(config["client_id_env"], "").strip()
    client_secret = os.getenv(config["client_secret_env"], "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail={"code": "OAUTH_NOT_CONFIGURED", "message": f"{provider.title()} sign-in is not configured yet."})
    configured_redirect = os.getenv(config["redirect_env"], "").strip()
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    redirect_uri = configured_redirect or (f"{base}/api/auth/oauth/{provider}/callback" if base else "")
    parsed = urllib.parse.urlparse(redirect_uri)
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if not redirect_uri or not parsed.netloc or (parsed.scheme != "https" and not local_http):
        raise HTTPException(status_code=503, detail={"code": "OAUTH_REDIRECT_INVALID", "message": "Configure an HTTPS public OAuth redirect URI."})
    return config, client_id, client_secret, redirect_uri


def oauth_provider_status() -> dict:
    providers = {}
    for provider in OAUTH_PROVIDERS:
        try:
            _oauth_config(provider)
            providers[provider] = {"enabled": True}
        except HTTPException:
            providers[provider] = {"enabled": False}
    return {"providers": providers, "linking": "A verified provider email may attach to an existing password account only when the emails exactly match."}


def _oauth_post_json(url: str, values: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise OAuthFlowError("The identity provider could not complete sign-in. Please try again.") from exc
    if not isinstance(payload, dict):
        raise OAuthFlowError("The identity provider returned an invalid response.")
    return payload


def _oauth_get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise OAuthFlowError("The identity provider verification keys are unavailable. Please try again.") from exc
    if not isinstance(payload, dict):
        raise OAuthFlowError("The identity provider verification response was invalid.")
    return payload


def _validated_identity_token(id_token: str, *, config: dict, client_id: str, nonce: str) -> dict:
    try:
        encoded_header, encoded_claims, encoded_signature = id_token.split(".")
        header = json.loads(_decode_b64url(encoded_header))
        claims = json.loads(_decode_b64url(encoded_claims))
        signature = _decode_b64url(encoded_signature)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise OAuthFlowError("The identity provider returned an invalid identity token.") from exc
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise OAuthFlowError("The identity token used an unsupported signing method.")
    keys = _oauth_get_json(config["jwks_endpoint"]).get("keys", [])
    key = next((item for item in keys if item.get("kid") == header["kid"] and item.get("kty") == "RSA"), None)
    if not key:
        raise OAuthFlowError("The identity provider signing key was not recognized. Please try again.")
    try:
        public_key = rsa.RSAPublicNumbers(
            int.from_bytes(_decode_b64url(key["e"]), "big"),
            int.from_bytes(_decode_b64url(key["n"]), "big"),
        ).public_key()
        public_key.verify(signature, f"{encoded_header}.{encoded_claims}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except (KeyError, ValueError, TypeError, Exception) as exc:
        # Signature failures and malformed JWKs are indistinguishable to the browser.
        raise OAuthFlowError("The identity token could not be verified.") from exc
    audience = claims.get("aud")
    valid_audience = audience == client_id or (isinstance(audience, list) and client_id in audience)
    now = int(time.time())
    if (claims.get("iss") != config["issuer"] or not valid_audience or claims.get("nonce") != nonce
            or not isinstance(claims.get("exp"), (int, float)) or int(claims["exp"]) < now - 30
            or not isinstance(claims.get("sub"), str) or not claims["sub"]):
        raise OAuthFlowError("The identity provider verification did not match this sign-in request.")
    return claims


def _consume_oauth_state(provider: str, state: str) -> dict:
    if not state or len(state) > 512:
        raise OAuthFlowError("This sign-in request has expired. Please start again.")
    conn = get_connection()
    try:
        conn.execute("DELETE FROM oauth_login_states WHERE expires_at <= datetime('now')")
        row = conn.execute("SELECT * FROM oauth_login_states WHERE state_hash=? AND provider=? AND expires_at > datetime('now')", (_oauth_state_hash(state), provider)).fetchone()
        if not row:
            raise OAuthFlowError("This sign-in request has expired or was already used. Please start again.")
        conn.execute("DELETE FROM oauth_login_states WHERE state_hash=?", (row["state_hash"],))
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def _exchange_authorization_code(provider: str, code: str, state: dict, config: dict, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    if not code or len(code) > 4096:
        raise OAuthFlowError("The identity provider did not return a valid authorization code.")
    values = {"code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}
    if config["uses_pkce"]:
        values["code_verifier"] = state["code_verifier"]
    tokens = _oauth_post_json(config["token_endpoint"], values)
    if not isinstance(tokens.get("id_token"), str):
        raise OAuthFlowError("The identity provider did not return a verifiable identity token.")
    return tokens


def _identity_user(conn, provider: str, claims: dict, state: dict, apple_user: str = "") -> dict:
    subject = str(claims["sub"])
    identity = conn.execute("SELECT users.* FROM user_identities JOIN users ON users.id=user_id WHERE provider=? AND subject=?", (provider, subject)).fetchone()
    if identity:
        conn.execute("UPDATE user_identities SET last_login_at=datetime('now') WHERE provider=? AND subject=?", (provider, subject))
        return dict(identity)
    email = str(claims.get("email") or "").lower().strip()
    verified = claims.get("email_verified") is True or str(claims.get("email_verified", "")).lower() == "true"
    if not email or not verified or "@" not in email:
        raise OAuthFlowError("The provider did not supply a verified email for this unlinked account. Sign in with your existing method first.")
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        if state["intent"] != "signup" or not bool(state["accept_legal"]):
            raise OAuthFlowError("No Oryntra account exists for this verified email. Choose Create account before continuing with a provider.")
        display_name = str(claims.get("name") or "").strip()[:80] or email.split("@", 1)[0]
        salt, password_hash = _hash_password(secrets.token_urlsafe(48))
        created = conn.execute("INSERT INTO users (email, display_name, password_salt, password_hash, legal_version, legal_accepted_at) VALUES (?, ?, ?, ?, '1.0', datetime('now'))", (email, display_name, salt, password_hash))
        user = conn.execute("SELECT * FROM users WHERE id=?", (created.lastrowid,)).fetchone()
    try:
        conn.execute("INSERT INTO user_identities (user_id, provider, subject, verified_email, last_login_at) VALUES (?, ?, ?, ?, datetime('now'))", (user["id"], provider, subject, email))
    except Exception as exc:
        raise OAuthFlowError("That provider identity is already linked to another account.") from exc
    return dict(user)


def _oauth_return_url(success: bool, reason: str = "") -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/") or "/"
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{'oauth=success' if success else 'auth_error=' + urllib.parse.quote(reason or 'oauth_failed')}"


def _finish_oauth(provider: str, code: str, state_value: str, request: Request, apple_user: str = "") -> Response:
    config, client_id, client_secret, redirect_uri = _oauth_config(provider)
    state = _consume_oauth_state(provider, state_value)
    tokens = _exchange_authorization_code(provider, code, state, config, client_id, client_secret, redirect_uri)
    claims = _validated_identity_token(tokens["id_token"], config=config, client_id=client_id, nonce=state["nonce"])
    conn = get_connection()
    try:
        row = _identity_user(conn, provider, claims, state, apple_user)
        token = secrets.token_urlsafe(32)
        show_offer = _consume_subscription_prompt(conn, row["id"])
        conn.execute("INSERT INTO user_sessions (token, user_id, expires_at, show_subscription_offer) VALUES (?, ?, ?, ?)", (token, row["id"], _expires_at(), int(show_offer)))
        conn.execute("UPDATE users SET last_login_at=datetime('now') WHERE id=?", (row["id"],))
        conn.commit()
        response = RedirectResponse(_oauth_return_url(True), status_code=303)
        _set_session_cookies(response, request, token)
        return response
    finally:
        conn.close()


@router.get("/oauth/providers")
async def oauth_providers():
    """Expose configuration readiness only; never expose client secrets or keys."""
    return oauth_provider_status()


@router.get("/oauth/{provider}/start")
async def start_oauth(provider: str, intent: str = "login", accept_legal: bool = False):
    if intent not in {"login", "signup"}:
        raise HTTPException(status_code=422, detail="OAuth intent must be login or signup.")
    if intent == "signup" and not accept_legal:
        raise HTTPException(status_code=422, detail="Accept the legal terms before creating an account with an identity provider.")
    config, client_id, _, redirect_uri = _oauth_config(provider)
    init_db()
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    state_hash = _oauth_state_hash(state)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    expires = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=OAUTH_STATE_MINUTES)).isoformat(timespec="seconds")
    conn = get_connection()
    try:
        conn.execute("DELETE FROM oauth_login_states WHERE expires_at <= datetime('now')")
        conn.execute("INSERT INTO oauth_login_states (state_hash, provider, nonce, code_verifier, intent, accept_legal, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (state_hash, provider, nonce, verifier if config["uses_pkce"] else None, intent, int(accept_legal), expires))
        conn.commit()
    finally:
        conn.close()
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "response_mode": "form_post" if provider == "apple" else "query", "scope": config["scopes"], "state": state, "nonce": nonce}
    if config["uses_pkce"]:
        params.update({"code_challenge": challenge, "code_challenge_method": "S256"})
    return RedirectResponse(f"{config['authorization_endpoint']}?{urllib.parse.urlencode(params)}", status_code=303)


@router.get("/oauth/{provider}/callback", include_in_schema=False)
async def oauth_callback_get(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(_oauth_return_url(False, "provider_cancelled"), status_code=303)
    try:
        return _finish_oauth(provider, code, state, request)
    except (OAuthFlowError, HTTPException):
        return RedirectResponse(_oauth_return_url(False, "oauth_failed"), status_code=303)


@router.post("/oauth/{provider}/callback", include_in_schema=False)
async def oauth_callback_post(provider: str, request: Request, code: str = Form(default=""), state: str = Form(default=""), error: str = Form(default=""), user: str = Form(default="")):
    if error:
        return RedirectResponse(_oauth_return_url(False, "provider_cancelled"), status_code=303)
    try:
        return _finish_oauth(provider, code, state, request, user)
    except (OAuthFlowError, HTTPException):
        return RedirectResponse(_oauth_return_url(False, "oauth_failed"), status_code=303)


@router.post("/signup")
async def signup(req: SignupRequest, request: Request, response: Response):
    init_db()
    email = req.email.lower().strip()
    display_name = (req.display_name or email.split("@", 1)[0])[:80]
    if not req.accept_legal:
        raise HTTPException(
            status_code=422,
            detail="You must accept the Terms of Service, Privacy Policy, and Market Analysis Risk Disclaimer to create an account.",
        )
    salt, password_hash = _hash_password(req.password)
    token = secrets.token_urlsafe(32)
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                """
                INSERT INTO users
                    (email, display_name, password_salt, password_hash, legal_version, legal_accepted_at)
                VALUES (?, ?, ?, ?, '1.0', datetime('now'))
                """,
                (email, display_name, salt, password_hash),
            )
            user_id = cur.lastrowid
        except Exception:
            raise HTTPException(status_code=409, detail="An account already exists for that email.")
        conn.execute(
            "INSERT INTO user_sessions (token, user_id, expires_at, show_subscription_offer) VALUES (?, ?, ?, ?)",
            (token, user_id, _expires_at(), int(_consume_subscription_prompt(conn, user_id))),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        conn.commit()
        user = _public_user(row)
        user["subscription"] = None
        user["show_subscription_offer"] = True
        _set_session_cookies(response, request, token)
        return {"token": token, "user": user}
    finally:
        conn.close()


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    init_db()
    email = req.email.lower().strip()
    _reject_if_login_throttled(email)
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        verified = _verify_password(
            req.password,
            row["password_salt"] if row else _DUMMY_PASSWORD_SALT,
            row["password_hash"] if row else _DUMMY_PASSWORD_HASH,
        )
        if not row or not verified:
            if _record_login_failure(email):
                raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.", headers={"Retry-After": str(LOGIN_LOCK_SECONDS)})
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        _clear_login_failures(email)
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO user_sessions (token, user_id, expires_at, show_subscription_offer) VALUES (?, ?, ?, ?)",
            (token, row["id"], _expires_at(), int(_consume_subscription_prompt(conn, row["id"]))),
        )
        conn.execute("UPDATE users SET last_login_at=datetime('now') WHERE id=?", (row["id"],))
        conn.commit()
        user = _public_user(row)
        user["subscription"] = _active_subscription_for(conn, user["id"])
        user["show_subscription_offer"] = bool(conn.execute("SELECT show_subscription_offer FROM user_sessions WHERE token=?", (token,)).fetchone()["show_subscription_offer"])
        _set_session_cookies(response, request, token)
        return {"token": token, "user": user}
    finally:
        conn.close()


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = get_auth_token(request)
    if token:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM user_sessions WHERE token=?", (token,))
            conn.commit()
        finally:
            conn.close()
    _clear_session_cookies(response, request)
    return {"ok": True}


@router.get("/me")
async def me(request: Request, response: Response):
    token = get_auth_token(request)
    user = get_current_user_optional(request)
    if user and token:
        conn = get_connection()
        try:
            conn.execute("UPDATE user_sessions SET expires_at=?, show_subscription_offer=0 WHERE token=?", (_expires_at(), token))
            conn.commit()
        finally:
            conn.close()
        _set_session_cookies(response, request, token)
    return {"authenticated": bool(user), "user": user}


@router.get("/provider-credentials")
async def get_provider_credentials(request: Request):
    require_current_user(request)
    return {
        "mode": "browser_direct",
        "message": "Provider keys stay in the browser and are sent directly to the selected provider. Oryntra does not receive or store them.",
    }


@router.put("/provider-credentials")
async def put_provider_credential(req: ProviderCredentialRequest, request: Request):
    require_current_user(request)
    raise HTTPException(
        status_code=410,
        detail="Oryntra no longer accepts provider keys. Keep your key in the browser and connect directly to the provider.",
    )


@router.delete("/provider-credentials/{provider}")
async def remove_provider_credential(provider: str, request: Request):
    user = require_current_user(request)
    return delete_credential(user["id"], provider)


@router.delete("/account")
async def delete_account(req: DeleteAccountRequest, request: Request, response: Response):
    user = require_current_user(request)
    ensure_provider_credential_schema()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not _verify_password(req.password, row["password_salt"], row["password_hash"]):
            raise HTTPException(status_code=401, detail="Password confirmation failed.")
        conn.execute("DELETE FROM paper_trades WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM user_watchlist WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM subscriptions WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM debug_subscription_overrides WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM analysis_usage WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM user_provider_credentials WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM users WHERE id=?", (user["id"],))
        conn.commit()
    finally:
        conn.close()
    _clear_session_cookies(response, request)
    return {"ok": True, "deleted": True}


@router.post("/subscribe")
async def subscribe(request: Request, req: SubscribeRequest):
    if os.getenv("ORYNTRA_ALLOW_MANUAL_BETA_SUBSCRIPTIONS", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "BILLING_NOT_CONFIGURED",
                "message": "Subscription activation is unavailable until verified billing is configured.",
            },
        )
    if os.getenv("ORYNTRA_MARKET_DATA_LICENSE_MODE", "personal_research").strip().lower() != "business_approved":
        raise HTTPException(
            status_code=451,
            detail={
                "code": "MARKET_DATA_LICENSE_REQUIRED",
                "message": "Paid public analysis cannot be activated under the personal market-data configuration.",
            },
        )
    user = require_current_user(request)
    plan = req.plan_code.lower().strip()
    allowed = {
        "starter": "Oryntra AI Starter",
        "pro": "Oryntra AI Pro",
    }
    if plan not in allowed:
        raise HTTPException(status_code=400, detail="Unknown plan.")
    conn = get_connection()
    try:
        conn.execute("UPDATE subscriptions SET status='CANCELLED' WHERE user_id=? AND status='ACTIVE'", (user["id"],))
        conn.execute(
            """
            INSERT INTO subscriptions (user_id, plan_code, plan_name, status, started_at)
            VALUES (?, ?, ?, 'ACTIVE', datetime('now'))
            """,
            (user["id"], plan, allowed[plan]),
        )
        conn.commit()
    finally:
        conn.close()
    return await me(request)
