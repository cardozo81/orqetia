"""Secure server-side Backoffice BFF shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Protocol
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from orqetia.identity import (
    BackofficeAuthorizationService,
    BackofficeWebSessionService,
    HumanAuthenticationContext,
    WebSessionRejected,
)

_SESSION_COOKIE = "__Host-orqetia-bo-session"
_CSRF_COOKIE = "__Host-orqetia-bo-csrf"
_OIDC_COOKIE = "__Host-orqetia-bo-oidc"


class OidcLoginRejected(PermissionError):
    """Trusted OIDC adapter rejected or could not validate the login transaction."""


@dataclass(frozen=True)
class OidcAuthorizationStart:
    authorization_url: str
    transaction_token: str


class BackofficeOidcBroker(Protocol):
    async def begin_login(self) -> OidcAuthorizationStart:
        """Create state/nonce/PKCE-bound login transaction and authorization URL."""

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        """Validate state/nonce/code/PKCE/tokens and return trusted identity context."""


class UnconfiguredBackofficeOidcBroker:
    async def begin_login(self) -> OidcAuthorizationStart:
        raise OidcLoginRejected("OIDC broker is not configured")

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        del code, state, transaction_token
        raise OidcLoginRejected("OIDC broker is not configured")


def create_backoffice_app(
    *,
    oidc: BackofficeOidcBroker,
    authorization: BackofficeAuthorizationService,
    sessions: BackofficeWebSessionService,
    allowed_origin: str,
    enable_hsts: bool = False,
) -> FastAPI:
    origin = allowed_origin.rstrip("/")
    if not origin.startswith(("http://", "https://")):
        raise ValueError("allowed_origin must be an absolute HTTP(S) origin")

    app = FastAPI(
        title="ORQETIA Backoffice",
        version="1.0.0-development",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'"
        )
        if enable_hsts:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    async def authenticated(request: Request):
        token = request.cookies.get(_SESSION_COOKIE, "")
        if not token:
            raise WebSessionRejected("Backoffice session required")
        return await sessions.authenticate(
            session_token=token,
            occurred_at=datetime.now(UTC),
        )

    def require_same_origin(request: Request) -> None:
        supplied = request.headers.get("origin")
        if supplied != origin:
            raise WebSessionRejected("request Origin rejected")
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site not in {"same-origin", "same-site"}:
            raise WebSessionRejected("cross-site mutation rejected")

    async def form_data(request: Request) -> dict[str, str]:
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("application/x-www-form-urlencoded"):
            raise WebSessionRejected("form content type required")
        raw = (await request.body()).decode("utf-8", errors="strict")
        parsed = parse_qs(raw, keep_blank_values=True, strict_parsing=True)
        return {key: values[-1] for key, values in parsed.items()}

    @app.exception_handler(WebSessionRejected)
    async def handle_session_rejected(
        _request: Request,
        _exc: WebSessionRejected,
    ) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><title>Unauthorized</title><h1>Unauthorized</h1>",
            status_code=401,
        )

    @app.get("/backoffice/login")
    async def login() -> Response:
        try:
            start = await oidc.begin_login()
        except OidcLoginRejected:
            return HTMLResponse(
                "<!doctype html><title>Unavailable</title>"
                "<h1>Identity service unavailable</h1>",
                status_code=503,
            )
        response = RedirectResponse(start.authorization_url, status_code=302)
        response.set_cookie(
            _OIDC_COOKIE,
            start.transaction_token,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=600,
        )
        return response

    @app.get("/backoffice/callback")
    async def callback(request: Request, code: str = "", state: str = "") -> Response:
        transaction = request.cookies.get(_OIDC_COOKIE, "")
        if not code or not state or not transaction:
            raise WebSessionRejected("OIDC callback transaction is incomplete")
        try:
            context = await oidc.complete_login(
                code=code,
                state=state,
                transaction_token=transaction,
            )
            principal = await authorization.authenticate(
                context=context,
                occurred_at=datetime.now(UTC),
            )
            established = await sessions.establish(
                principal=principal,
                occurred_at=datetime.now(UTC),
            )
        except (OidcLoginRejected, PermissionError) as error:
            raise WebSessionRejected("Backoffice login rejected") from error

        response = RedirectResponse("/backoffice", status_code=303)
        response.delete_cookie(_OIDC_COOKIE, path="/")
        response.set_cookie(
            _SESSION_COOKIE,
            established.session_token,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=8 * 60 * 60,
        )
        response.set_cookie(
            _CSRF_COOKIE,
            established.csrf_token,
            secure=True,
            httponly=False,
            samesite="strict",
            path="/",
            max_age=8 * 60 * 60,
        )
        return response

    @app.get("/backoffice")
    async def home(request: Request) -> HTMLResponse:
        authenticated_session = await authenticated(request)
        principal = authenticated_session.principal
        csrf = request.cookies.get(_CSRF_COOKIE, "")
        roles = ", ".join(role.value for role in principal.roles)
        return HTMLResponse(
            "<!doctype html><html><head><title>ORQETIA Backoffice</title></head>"
            "<body><main><h1>ORQETIA Backoffice</h1>"
            f"<p>Identity: {escape(principal.subject)}</p>"
            f"<p>Roles: {escape(roles)}</p>"
            "<nav><a href='/backoffice/tenants'>Tenants</a> "
            "<a href='/backoffice/providers'>Providers</a> "
            "<a href='/backoffice/intelligence'>Intelligence</a></nav>"
            "<form method='post' action='/backoffice/logout'>"
            f"<input type='hidden' name='csrf_token' value='{escape(csrf)}'>"
            "<button type='submit'>Logout</button></form>"
            "</main></body></html>"
        )

    @app.post("/backoffice/logout")
    async def logout(request: Request) -> Response:
        require_same_origin(request)
        authenticated_session = await authenticated(request)
        data = await form_data(request)
        csrf = data.get("csrf_token", "")
        cookie_csrf = request.cookies.get(_CSRF_COOKIE, "")
        if not hmac_safe_equal(csrf, cookie_csrf):
            raise WebSessionRejected("CSRF cookie/form mismatch")
        sessions.require_csrf(
            session=authenticated_session.record,
            csrf_token=csrf,
        )
        token = request.cookies.get(_SESSION_COOKIE, "")
        await sessions.revoke(
            session_token=token,
            occurred_at=datetime.now(UTC),
        )
        response = RedirectResponse("/backoffice/login", status_code=303)
        response.delete_cookie(_SESSION_COOKIE, path="/")
        response.delete_cookie(_CSRF_COOKIE, path="/")
        return response

    @app.get("/health/live", include_in_schema=False)
    async def health_live() -> dict[str, str]:
        return {"status": "live"}

    return app


def hmac_safe_equal(left: str, right: str) -> bool:
    import hmac

    if not left or not right:
        return False
    return hmac.compare_digest(left, right)
