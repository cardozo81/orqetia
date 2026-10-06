"""Secure server-side Customer Portal BFF shell."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Protocol
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from orqetia.identity.backoffice_authz import HumanAuthenticationContext
from orqetia.identity.customer_authz import CustomerAuthorizationService
from orqetia.identity.customer_web_sessions import (
    CustomerPortalWebSessionService,
)
from orqetia.identity.web_sessions import WebSessionRejected

_SESSION_COOKIE = "__Host-orqetia-portal-session"
_CSRF_COOKIE = "__Host-orqetia-portal-csrf"
_OIDC_COOKIE = "__Host-orqetia-portal-oidc"


class CustomerPortalOidcRejected(PermissionError):
    """Trusted OIDC adapter rejected or could not validate login."""


@dataclass(frozen=True)
class CustomerPortalOidcAuthorizationStart:
    authorization_url: str
    transaction_token: str


class CustomerPortalOidcBroker(Protocol):
    async def begin_login(self) -> CustomerPortalOidcAuthorizationStart: ...

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext: ...


class UnconfiguredCustomerPortalOidcBroker:
    async def begin_login(self) -> CustomerPortalOidcAuthorizationStart:
        raise CustomerPortalOidcRejected("OIDC broker is not configured")

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        del code, state, transaction_token
        raise CustomerPortalOidcRejected("OIDC broker is not configured")


def create_customer_portal_app(
    *,
    oidc: CustomerPortalOidcBroker,
    authorization: CustomerAuthorizationService,
    sessions: CustomerPortalWebSessionService,
    allowed_origin: str,
    enable_hsts: bool = False,
) -> FastAPI:
    origin = allowed_origin.rstrip("/")
    if not origin.startswith(("http://", "https://")):
        raise ValueError("allowed_origin must be an absolute HTTP(S) origin")

    app = FastAPI(
        title="ORQETIA Client Portal",
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

    def require_same_origin(request: Request) -> None:
        if request.headers.get("origin") != origin:
            raise WebSessionRejected("request Origin rejected")
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site not in {
            "same-origin",
            "same-site",
        }:
            raise WebSessionRejected("cross-site mutation rejected")

    async def form_data(request: Request) -> dict[str, str]:
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith(
            "application/x-www-form-urlencoded"
        ):
            raise WebSessionRejected("form content type required")
        raw = (await request.body()).decode("utf-8", errors="strict")
        parsed = parse_qs(raw, keep_blank_values=True, strict_parsing=True)
        return {key: values[-1] for key, values in parsed.items()}

    def required_uuid(value: str, field: str) -> UUID:
        try:
            return UUID(value)
        except ValueError as error:
            raise ValueError(f"{field} must be a UUID") from error

    async def authenticated(request: Request):
        token = request.cookies.get(_SESSION_COOKIE, "")
        if not token:
            raise WebSessionRejected("Customer Portal session required")
        return await sessions.authenticate(
            session_token=token,
            occurred_at=datetime.now(UTC),
        )

    async def authorized(request: Request, permission: str):
        current = await authenticated(request)
        authorization.require_permission(
            principal=current.principal,
            permission=permission,
            occurred_at=datetime.now(UTC),
        )
        return current

    def set_session_cookies(
        response: Response,
        *,
        session_token: str,
        csrf_token: str,
    ) -> None:
        response.set_cookie(
            _SESSION_COOKIE,
            session_token,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=8 * 60 * 60,
        )
        response.set_cookie(
            _CSRF_COOKIE,
            csrf_token,
            secure=True,
            httponly=False,
            samesite="strict",
            path="/",
            max_age=8 * 60 * 60,
        )

    def navigation(permissions: frozenset[str]) -> str:
        links: list[str] = ["<a href='/portal'>Overview</a>"]
        allowed = (
            ("sessions:read", "/portal/sessions", "Sessions"),
            ("tasks:read", "/portal/tasks", "Tasks"),
            ("providers:read", "/portal/providers", "Providers & Models"),
            ("credentials:read", "/portal/credentials", "API Credentials"),
            ("usage:read", "/portal/usage", "Usage"),
            ("estimates:write", "/portal/estimates", "Token Estimates"),
            ("audit:read", "/portal/activity", "Activity"),
            ("docs:read", "/portal/docs", "Documentation"),
        )
        links.extend(
            f"<a href='{path}'>{escape(label)}</a>"
            for permission, path, label in allowed
            if permission in permissions
        )
        return "<nav>" + " | ".join(links) + "</nav>"

    def page(title: str, body: str, *, nav: str = "") -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html><head><title>"
            + escape(title)
            + "</title></head><body><main>"
            + nav
            + "<h1>"
            + escape(title)
            + "</h1>"
            + body
            + "</main></body></html>"
        )

    @app.exception_handler(WebSessionRejected)
    async def handle_session_rejected(
        _request: Request,
        _exc: WebSessionRejected,
    ) -> HTMLResponse:
        return page("Unauthorized", "<p>Authentication required.</p>")

    @app.exception_handler(PermissionError)
    async def handle_forbidden(
        _request: Request,
        _exc: PermissionError,
    ) -> HTMLResponse:
        response = page("Forbidden", "<p>Permission denied.</p>")
        response.status_code = 403
        return response

    @app.exception_handler(ValueError)
    async def handle_invalid(
        _request: Request,
        _exc: ValueError,
    ) -> HTMLResponse:
        response = page("Invalid request", "<p>Invalid input.</p>")
        response.status_code = 400
        return response

    @app.get("/portal/login")
    async def login() -> Response:
        try:
            start = await oidc.begin_login()
        except CustomerPortalOidcRejected:
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

    @app.get("/portal/callback")
    async def callback(
        request: Request,
        code: str = "",
        state: str = "",
    ) -> Response:
        transaction = request.cookies.get(_OIDC_COOKIE, "")
        if not code or not state or not transaction:
            raise WebSessionRejected(
                "OIDC callback transaction is incomplete"
            )
        try:
            context = await oidc.complete_login(
                code=code,
                state=state,
                transaction_token=transaction,
            )
            memberships = await authorization.list_available_memberships(
                context=context
            )
            if not memberships:
                raise PermissionError("no active customer membership")
            established = await sessions.establish_identity(
                context=context,
                occurred_at=datetime.now(UTC),
            )
        except (CustomerPortalOidcRejected, PermissionError) as error:
            raise WebSessionRejected("Customer Portal login rejected") from error

        target = "/portal/select-membership"
        if len(memberships) == 1:
            await sessions.bind_membership(
                session_token=established.session_token,
                membership_id=memberships[0].membership_id,
                occurred_at=datetime.now(UTC),
            )
            target = "/portal"

        response = RedirectResponse(target, status_code=303)
        response.delete_cookie(_OIDC_COOKIE, path="/")
        set_session_cookies(
            response,
            session_token=established.session_token,
            csrf_token=established.csrf_token,
        )
        return response

    @app.get("/portal/select-membership")
    async def select_membership(request: Request) -> Response:
        token = request.cookies.get(_SESSION_COOKIE, "")
        current = await sessions.authenticate_identity(
            session_token=token,
            occurred_at=datetime.now(UTC),
        )
        if current.record.membership_selected:
            return RedirectResponse("/portal", status_code=303)
        memberships = await authorization.list_available_memberships(
            context=current.context
        )
        csrf = request.cookies.get(_CSRF_COOKIE, "")
        choices = "".join(
            "<form method='post' action='/portal/select-membership'>"
            "<input type='hidden' name='csrf_token' value='"
            + escape(csrf)
            + "'><input type='hidden' name='membership_id' value='"
            + escape(str(membership.membership_id))
            + "'><button type='submit'>"
            + escape(str(membership.tenant_id))
            + " / "
            + escape(str(membership.client_id))
            + "</button></form>"
            for membership in memberships
        )
        return page("Select client", choices)

    @app.post("/portal/select-membership")
    async def bind_membership(request: Request) -> Response:
        require_same_origin(request)
        token = request.cookies.get(_SESSION_COOKIE, "")
        current = await sessions.authenticate_identity(
            session_token=token,
            occurred_at=datetime.now(UTC),
        )
        data = await form_data(request)
        csrf = data.get("csrf_token", "")
        cookie_csrf = request.cookies.get(_CSRF_COOKIE, "")
        if not _safe_equal(csrf, cookie_csrf):
            raise WebSessionRejected("CSRF cookie/form mismatch")
        sessions.require_csrf(
            session=current.record,
            csrf_token=csrf,
        )
        await sessions.bind_membership(
            session_token=token,
            membership_id=required_uuid(
                data.get("membership_id", ""),
                "membership_id",
            ),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/portal", status_code=303)

    @app.get("/portal")
    async def home(request: Request) -> HTMLResponse:
        current = await authorized(request, "portal:read")
        principal = current.principal
        csrf = request.cookies.get(_CSRF_COOKIE, "")
        roles = ", ".join(role.value for role in principal.roles)
        body = (
            "<p>Identity: "
            + escape(principal.subject)
            + "</p><p>Tenant: "
            + escape(str(principal.tenant_id))
            + "</p><p>Client: "
            + escape(str(principal.client_id))
            + "</p><p>Roles: "
            + escape(roles)
            + "</p><form method='post' action='/portal/logout'>"
            + "<input type='hidden' name='csrf_token' value='"
            + escape(csrf)
            + "'><button type='submit'>Logout</button></form>"
        )
        return page(
            "ORQETIA Client Portal",
            body,
            nav=navigation(principal.permissions),
        )

    @app.post("/portal/logout")
    async def logout(request: Request) -> Response:
        require_same_origin(request)
        token = request.cookies.get(_SESSION_COOKIE, "")
        current = await sessions.authenticate_identity(
            session_token=token,
            occurred_at=datetime.now(UTC),
        )
        data = await form_data(request)
        csrf = data.get("csrf_token", "")
        cookie_csrf = request.cookies.get(_CSRF_COOKIE, "")
        if not _safe_equal(csrf, cookie_csrf):
            raise WebSessionRejected("CSRF cookie/form mismatch")
        sessions.require_csrf(
            session=current.record,
            csrf_token=csrf,
        )
        await sessions.revoke(
            session_token=token,
            occurred_at=datetime.now(UTC),
        )
        response = RedirectResponse("/portal/login", status_code=303)
        response.delete_cookie(_SESSION_COOKIE, path="/")
        response.delete_cookie(_CSRF_COOKIE, path="/")
        return response

    return app


def _safe_equal(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)
