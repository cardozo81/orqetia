"""Signed LOCAL/TEST identity provider and human-authentication brokers.

This is a deterministic development adapter for operational homologation. It
preserves the Backoffice/Portal OIDC broker boundary and cannot be constructed
for staging or production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from orqetia.identity import HumanAuthenticationContext
from orqetia.infrastructure.backoffice import (
    OidcAuthorizationStart,
    OidcLoginRejected,
)
from orqetia.infrastructure.customer_portal.app import (
    CustomerPortalOidcAuthorizationStart,
    CustomerPortalOidcRejected,
)

LOCAL_OIDC_ISSUER = "https://orqetia.local/dev-idp"
LOCAL_OIDC_SIGNING_KEY_PATH = Path(
    "/var/lib/orqetia/bootstrap/local-oidc-signing.key"
)
LOCAL_LOGIN_TOKENS_PATH = Path("/var/lib/orqetia/bootstrap/local-login-tokens.json")
LOCAL_USED_CODES_PATH = Path("/var/lib/orqetia/oidc-used")
_LOCAL_ENVIRONMENTS = frozenset({"local", "test"})
_TOKEN_TTL_SECONDS = 300


@dataclass(frozen=True)
class LocalOidcProfile:
    profile_id: str
    audience: str
    subject: str
    display_name: str


LOCAL_OIDC_PROFILES = (
    LocalOidcProfile(
        profile_id="backoffice-admin",
        audience="backoffice",
        subject="local-backoffice-admin",
        display_name="Backoffice Admin",
    ),
    LocalOidcProfile(
        profile_id="client-viewer",
        audience="portal",
        subject="local-client-viewer",
        display_name="Client Viewer",
    ),
    LocalOidcProfile(
        profile_id="client-operator",
        audience="portal",
        subject="local-client-operator",
        display_name="Client Operator",
    ),
    LocalOidcProfile(
        profile_id="client-admin",
        audience="portal",
        subject="local-client-admin",
        display_name="Client Admin",
    ),
)


def load_local_oidc_signing_key(explicit: str | None = None) -> str:
    """Load the LOCAL/TEST signing key without exposing it in logs or Git."""

    if explicit is not None and explicit.strip():
        value = explicit.strip()
    else:
        try:
            value = LOCAL_OIDC_SIGNING_KEY_PATH.read_text(encoding="utf-8").strip()
        except FileNotFoundError as error:
            raise RuntimeError(
                "local OIDC signing key is unavailable; run local bootstrap first"
            ) from error
    if len(value.encode("utf-8")) < 32:
        raise RuntimeError("local OIDC signing key must contain at least 32 bytes")
    return value


def _guard_environment(environment: str) -> str:
    normalized = environment.strip().lower()
    if normalized not in _LOCAL_ENVIRONMENTS:
        raise RuntimeError("local OIDC is restricted to LOCAL/TEST")
    return normalized


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError("invalid local OIDC token encoding") from error


def _sign(payload: dict[str, object], key: bytes) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    body = _b64encode(encoded)
    signature = _b64encode(
        hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{signature}"


def _verify(token: str, key: bytes) -> dict[str, object]:
    if not token.isascii():
        raise ValueError("invalid local OIDC token encoding")
    try:
        body, signature = token.split(".", 1)
    except ValueError as error:
        raise ValueError("invalid local OIDC token") from error
    expected = _b64encode(
        hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid local OIDC signature")
    try:
        decoded = json.loads(_b64decode(body))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("invalid local OIDC payload") from error
    if not isinstance(decoded, dict):
        raise ValueError("invalid local OIDC payload shape")
    return decoded


def _require_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"local OIDC {key} is invalid")
    return value


def _require_current(payload: dict[str, object]) -> None:
    expiry = payload.get("exp")
    if not isinstance(expiry, int) or expiry < int(time.time()):
        raise ValueError("local OIDC token expired")


class _LocalBrokerBase:
    def __init__(
        self,
        *,
        environment: str,
        signing_key: str,
        audience: str,
        callback_url: str,
        idp_public_url: str,
    ) -> None:
        _guard_environment(environment)
        if len(signing_key.encode("utf-8")) < 32:
            raise ValueError("local OIDC signing key must contain at least 32 bytes")
        if audience not in {"backoffice", "portal"}:
            raise ValueError("unknown local OIDC audience")
        if not callback_url.startswith("https://"):
            raise ValueError("local OIDC callback URL must use HTTPS")
        if not idp_public_url.startswith("https://"):
            raise ValueError("local OIDC public URL must use HTTPS")
        self._key = signing_key.encode("utf-8")
        self._audience = audience
        self._callback_url = callback_url
        self._idp_public_url = idp_public_url.rstrip("/")

    def _start(self) -> tuple[str, str]:
        now = int(time.time())
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = _b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        )
        payload: dict[str, object] = {
                "v": 1,
                "kind": "transaction",
                "issuer": LOCAL_OIDC_ISSUER,
                "audience": self._audience,
                "callback": self._callback_url,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "iat": now,
                "exp": now + _TOKEN_TTL_SECONDS,
            }
        transaction = _sign(payload, self._key)
        authorization_url = (
            f"{self._idp_public_url}/authorize?"
            + urlencode({"transaction": transaction})
        )
        # Only the HttpOnly BFF transaction cookie contains the verifier.
        return authorization_url, _sign({**payload, "code_verifier": verifier}, self._key)

    def _complete(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        try:
            transaction = _verify(transaction_token, self._key)
            _require_current(transaction)
            if transaction.get("kind") != "transaction":
                raise ValueError("unexpected transaction token kind")
            if _require_string(transaction, "issuer") != LOCAL_OIDC_ISSUER:
                raise ValueError("issuer mismatch")
            if _require_string(transaction, "audience") != self._audience:
                raise ValueError("audience mismatch")
            if _require_string(transaction, "callback") != self._callback_url:
                raise ValueError("callback mismatch")
            if _require_string(transaction, "state") != state:
                raise ValueError("state mismatch")
            verifier = _require_string(transaction, "code_verifier")
            challenge = _b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            if not hmac.compare_digest(challenge, _require_string(transaction, "code_challenge")):
                raise ValueError("PKCE challenge mismatch")

            authorization = _verify(code, self._key)
            _require_current(authorization)
            if authorization.get("kind") != "authorization_code":
                raise ValueError("unexpected authorization code kind")
            if _require_string(authorization, "issuer") != LOCAL_OIDC_ISSUER:
                raise ValueError("authorization issuer mismatch")
            if _require_string(authorization, "audience") != self._audience:
                raise ValueError("authorization audience mismatch")
            if _require_string(authorization, "nonce") != _require_string(
                transaction, "nonce"
            ):
                raise ValueError("nonce mismatch")
            public_transaction = _sign(
                {k: v for k, v in transaction.items() if k != "code_verifier"}, self._key,
            )
            transaction_hash = hashlib.sha256(public_transaction.encode("utf-8")).hexdigest()
            if _require_string(authorization, "transaction_hash") != transaction_hash:
                raise ValueError("authorization transaction mismatch")
            subject = _require_string(authorization, "subject")
            authenticated_at_raw = authorization.get("authenticated_at")
            if not isinstance(authenticated_at_raw, int):
                raise ValueError("authenticated_at is invalid")
            if not 0 <= int(time.time()) - authenticated_at_raw <= _TOKEN_TTL_SECONDS:
                raise ValueError("authentication time is invalid")
            LOCAL_USED_CODES_PATH.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                descriptor = os.open(
                    LOCAL_USED_CODES_PATH / transaction_hash,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                )
            except FileExistsError as error:
                raise ValueError("authorization transaction already consumed") from error
            else:
                os.close(descriptor)
        except ValueError as error:
            raise PermissionError(str(error)) from error

        return HumanAuthenticationContext(
            issuer=LOCAL_OIDC_ISSUER,
            subject=subject,
            authenticated_at=datetime.fromtimestamp(
                authenticated_at_raw,
                tz=UTC,
            ),
            mfa_satisfied=True,
            amr=("local_password", "local_otp"),
            acr="urn:orqetia:local:mfa",
        )


class LocalBackofficeOidcBroker(_LocalBrokerBase):
    async def begin_login(self) -> OidcAuthorizationStart:
        authorization_url, transaction = self._start()
        return OidcAuthorizationStart(
            authorization_url=authorization_url,
            transaction_token=transaction,
        )

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        try:
            return self._complete(
                code=code,
                state=state,
                transaction_token=transaction_token,
            )
        except PermissionError as error:
            raise OidcLoginRejected(str(error)) from error


class LocalCustomerPortalOidcBroker(_LocalBrokerBase):
    async def begin_login(self) -> CustomerPortalOidcAuthorizationStart:
        authorization_url, transaction = self._start()
        return CustomerPortalOidcAuthorizationStart(
            authorization_url=authorization_url,
            transaction_token=transaction,
        )

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        try:
            return self._complete(
                code=code,
                state=state,
                transaction_token=transaction_token,
            )
        except PermissionError as error:
            raise CustomerPortalOidcRejected(str(error)) from error


def create_local_oidc_app(
    *,
    environment: str,
    signing_key: str,
    allowed_callbacks: tuple[str, ...],
) -> FastAPI:
    """Create the browser-facing synthetic IdP used only by local homologation."""

    _guard_environment(environment)
    key = signing_key.encode("utf-8")
    if len(key) < 32:
        raise ValueError("local OIDC signing key must contain at least 32 bytes")
    callbacks = frozenset(allowed_callbacks)
    if not callbacks or any(not value.startswith("https://") for value in callbacks):
        raise ValueError("local OIDC callbacks must be non-empty HTTPS URLs")

    app = FastAPI(
        title="ORQETIA Local Identity Provider",
        version="1.0.0-development",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'"
        )
        return response

    def transaction_payload(transaction: str) -> dict[str, object]:
        try:
            payload = _verify(transaction, key)
            _require_current(payload)
            if payload.get("kind") != "transaction":
                raise ValueError("unexpected token kind")
            if _require_string(payload, "issuer") != LOCAL_OIDC_ISSUER:
                raise ValueError("issuer mismatch")
            callback = _require_string(payload, "callback")
            if callback not in callbacks:
                raise ValueError("callback is not allowed")
            audience = _require_string(payload, "audience")
            if audience not in {"backoffice", "portal"}:
                raise ValueError("audience is invalid")
            return payload
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid login transaction") from error

    @app.get("/authorize", response_class=HTMLResponse)
    async def authorize(
        transaction: str = Query(min_length=20, max_length=4096),
    ) -> HTMLResponse:
        payload = transaction_payload(transaction)
        audience = _require_string(payload, "audience")
        profiles = tuple(
            profile for profile in LOCAL_OIDC_PROFILES
            if profile.audience == audience
        )
        links = "".join(
            "<option value='" + escape(profile.profile_id, quote=True) + "'>"
            + escape(profile.display_name)
            + "</option>"
            for profile in profiles
        )
        return HTMLResponse(
            "<!doctype html><html><head><title>ORQETIA Local Login</title>"
            "</head><body><main><h1>ORQETIA Local Login</h1>"
            "<p>LOCAL/TEST synthetic identity broker. MFA is simulated for homologation.</p>"
            "<form method='post' action='/dev-idp/select'>"
            f"<input type='hidden' name='transaction' value='{escape(transaction, quote=True)}'>"
            f"<label>Identity <select name='profile'>{links}</select></label>"
            "<label>Local access token <input type='password' name='access_token' "
            "autocomplete='off' required></label><button>Login</button>"
            "</form></main></body></html>"
        )

    @app.post("/select")
    async def select_profile(request: Request) -> RedirectResponse:
        # Capability for this local installation, never a product password protocol.
        # No access token travels in URLs, cookies, or logs.
        allowed_origins = {
            f"{urlsplit(callback).scheme}://{urlsplit(callback).netloc}"
            for callback in callbacks
        }
        if request.headers.get("origin") not in allowed_origins:
            raise HTTPException(status_code=403, detail="invalid origin")
        if request.headers.get("sec-fetch-site") not in {None, "same-origin"}:
            raise HTTPException(status_code=403, detail="invalid fetch origin")
        body = await request.body()
        if len(body) > 8192:
            raise HTTPException(status_code=413, detail="login form too large")
        form = parse_qs(body.decode("utf-8"), max_num_fields=4)
        transaction = form.get("transaction", [""])[0]
        profile = form.get("profile", [""])[0]
        supplied = form.get("access_token", [""])[0]
        tokens = json.loads(LOCAL_LOGIN_TOKENS_PATH.read_text(encoding="utf-8"))
        expected = tokens.get(profile)
        if not isinstance(expected, str) or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="invalid local login")
        payload = transaction_payload(transaction)
        audience = _require_string(payload, "audience")
        selected = next(
            (
                item
                for item in LOCAL_OIDC_PROFILES
                if item.profile_id == profile and item.audience == audience
            ),
            None,
        )
        if selected is None:
            raise HTTPException(status_code=403, detail="profile is not allowed")
        now = int(time.time())
        code = _sign(
            {
                "v": 1,
                "kind": "authorization_code",
                "issuer": LOCAL_OIDC_ISSUER,
                "audience": audience,
                "subject": selected.subject,
                "nonce": _require_string(payload, "nonce"),
                "transaction_hash": hashlib.sha256(
                    transaction.encode("utf-8")
                ).hexdigest(),
                "authenticated_at": now,
                "iat": now,
                "exp": now + _TOKEN_TTL_SECONDS,
            },
            key,
        )
        callback = _require_string(payload, "callback")
        state = _require_string(payload, "state")
        return RedirectResponse(
            callback + "?" + urlencode({"code": code, "state": state}),
            status_code=303,
        )

    @app.get("/health/live", include_in_schema=False)
    async def health_live() -> dict[str, str]:
        return {"status": "live"}

    return app
