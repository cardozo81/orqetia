"""Secure server-side Customer Portal BFF shell."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Protocol
from urllib.parse import parse_qs
from uuid import UUID, uuid7

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from orqetia.identity.backoffice_authz import HumanAuthenticationContext
from orqetia.identity.customer_authz import CustomerAuthorizationService
from orqetia.identity.customer_web_sessions import (
    CustomerPortalWebSessionService,
)
from orqetia.identity.web_sessions import WebSessionRejected
from orqetia.execution import OwnershipScope, TaskStatus
from orqetia.infrastructure.http.execution_runtime import (
    ClientExecutionArtifactUnavailable,
    ClientExecutionConflict,
    ClientExecutionForbidden,
    ClientExecutionNotFound,
    ClientExecutionRuntime,
    ClientRequestedTarget,
)

_SESSION_COOKIE = "__Host-orqetia-portal-session"
_CSRF_COOKIE = "__Host-orqetia-portal-csrf"
_OIDC_COOKIE = "__Host-orqetia-portal-oidc"


class CustomerPortalOidcRejected(PermissionError):
    """Trusted OIDC adapter rejected or could not validate login."""


class CustomerPortalUnavailable(RuntimeError):
    """Client-facing application services are not configured."""


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
    execution: ClientExecutionRuntime | None = None,
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

    def required_execution() -> ClientExecutionRuntime:
        if execution is None:
            raise CustomerPortalUnavailable(
                "Client execution runtime is unavailable"
            )
        return execution

    def ownership(principal) -> OwnershipScope:
        return OwnershipScope(
            tenant_id=principal.tenant_id,
            client_id=principal.client_id,
        )

    async def authorized_mutation(
        request: Request,
        permission: str,
    ):
        require_same_origin(request)
        current = await authenticated(request)
        authorization.require_permission(
            principal=current.principal,
            permission=permission,
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
        return current, data

    def csrf_field(request: Request) -> str:
        return (
            "<input type='hidden' name='csrf_token' value='"
            + escape(request.cookies.get(_CSRF_COOKIE, ""))
            + "'>"
        )

    def safe_json(value: object) -> str:
        return escape(
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
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
        response = page("Unauthorized", "<p>Authentication required.</p>")
        response.status_code = 401
        return response

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

    @app.exception_handler(CustomerPortalUnavailable)
    async def handle_unavailable(
        _request: Request,
        _exc: CustomerPortalUnavailable,
    ) -> HTMLResponse:
        response = page(
            "Unavailable",
            "<p>Client service unavailable.</p>",
        )
        response.status_code = 503
        return response

    @app.exception_handler(ClientExecutionNotFound)
    async def handle_execution_not_found(
        _request: Request,
        _exc: ClientExecutionNotFound,
    ) -> HTMLResponse:
        response = page("Not found", "<p>Resource not found.</p>")
        response.status_code = 404
        return response

    @app.exception_handler(ClientExecutionForbidden)
    async def handle_execution_forbidden(
        _request: Request,
        _exc: ClientExecutionForbidden,
    ) -> HTMLResponse:
        response = page("Forbidden", "<p>Target is not authorized.</p>")
        response.status_code = 403
        return response

    @app.exception_handler(ClientExecutionConflict)
    async def handle_execution_conflict(
        _request: Request,
        _exc: ClientExecutionConflict,
    ) -> HTMLResponse:
        response = page("Conflict", "<p>Execution state conflict.</p>")
        response.status_code = 409
        return response

    @app.exception_handler(ClientExecutionArtifactUnavailable)
    async def handle_artifact_unavailable(
        _request: Request,
        _exc: ClientExecutionArtifactUnavailable,
    ) -> HTMLResponse:
        response = page("Not found", "<p>Retained artifact unavailable.</p>")
        response.status_code = 404
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

    @app.get("/portal/sessions")
    async def sessions_page(request: Request) -> HTMLResponse:
        current = await authorized(request, "sessions:read")
        body = (
            "<p>Sessions are ownership-scoped to the selected membership.</p>"
            "<form method='get' action='/portal/sessions/open'>"
            "<label>Session ID <input name='session_id' required></label>"
            "<button type='submit'>Open session</button></form>"
        )
        if "tasks:write" in current.principal.permissions:
            body += (
                "<h2>Create session</h2>"
                "<form method='post' action='/portal/sessions'>"
                + csrf_field(request)
                + "<input type='hidden' name='idempotency_key' value='"
                + escape(str(uuid7()))
                + "'><label>External reference "
                "<input name='external_reference' maxlength='500'></label>"
                "<button type='submit'>Create session</button></form>"
            )
        return page(
            "Sessions",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.get("/portal/sessions/open")
    async def open_session(
        request: Request,
        session_id: str = "",
    ) -> Response:
        await authorized(request, "sessions:read")
        parsed = required_uuid(session_id, "session_id")
        return RedirectResponse(
            f"/portal/sessions/{parsed}",
            status_code=303,
        )

    @app.post("/portal/sessions")
    async def create_session(request: Request) -> Response:
        current, data = await authorized_mutation(
            request,
            "tasks:write",
        )
        session = await required_execution().create_session(
            scope=ownership(current.principal),
            external_reference=data.get("external_reference", "").strip() or None,
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse(
            f"/portal/sessions/{session.session_id}",
            status_code=303,
        )

    @app.get("/portal/sessions/{session_id}")
    async def session_detail(
        request: Request,
        session_id: UUID,
    ) -> HTMLResponse:
        current = await authorized(request, "sessions:read")
        session = await required_execution().get_session(
            scope=ownership(current.principal),
            session_id=session_id,
        )
        body = (
            "<p>Status: "
            + escape(session.status.value)
            + "</p><p>Session ID: "
            + escape(str(session.session_id))
            + "</p><p>Policy version: "
            + escape(str(session.policy.effective_policy_version_id))
            + "</p>"
        )
        if session.external_reference:
            body += (
                "<p>External reference: "
                + escape(session.external_reference)
                + "</p>"
            )
        if "tasks:write" in current.principal.permissions:
            body += (
                "<h2>Create task</h2>"
                "<form method='post' action='/portal/sessions/"
                + escape(str(session.session_id))
                + "/tasks'>"
                + csrf_field(request)
                + "<input type='hidden' name='idempotency_key' value='"
                + escape(str(uuid7()))
                + "'><label>Operation <input name='operation' "
                "value='TASK_EXECUTION' required></label>"
                "<label>Input JSON <textarea name='input_json' required>"
                '{"input_text":""}'
                "</textarea></label>"
                "<label>Provider <input name='provider_id'></label>"
                "<label>Model <input name='model_id'></label>"
                "<label>Reasoning profile "
                "<input name='reasoning_profile'></label>"
                "<label>External reference "
                "<input name='external_reference'></label>"
                "<button type='submit'>Create task</button></form>"
                "<p>Leave target fields empty for AUTO.</p>"
            )
        return page(
            "Session",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.post("/portal/sessions/{session_id}/tasks")
    async def create_task(
        request: Request,
        session_id: UUID,
    ) -> Response:
        current, data = await authorized_mutation(
            request,
            "tasks:write",
        )
        raw_payload = data.get("input_json", "")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as error:
            raise ValueError("input_json must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("input_json must be a JSON object")

        provider_id = data.get("provider_id", "").strip()
        model_id = data.get("model_id", "").strip() or None
        reasoning = data.get("reasoning_profile", "").strip() or None
        if not provider_id and (model_id is not None or reasoning is not None):
            raise ValueError("provider_id is required for explicit target")
        target = (
            None
            if not provider_id
            else ClientRequestedTarget(
                provider_id=provider_id,
                model_id=model_id,
                reasoning_profile=reasoning,
            )
        )
        task = await required_execution().create_task(
            scope=ownership(current.principal),
            session_id=session_id,
            operation=data.get("operation", "").strip(),
            input_payload=payload,
            target=target,
            external_reference=data.get("external_reference", "").strip() or None,
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse(
            f"/portal/tasks/{task.task_id}",
            status_code=303,
        )

    @app.get("/portal/tasks")
    async def tasks_page(request: Request) -> HTMLResponse:
        current = await authorized(request, "tasks:read")
        body = (
            "<form method='get' action='/portal/tasks/open'>"
            "<label>Task ID <input name='task_id' required></label>"
            "<button type='submit'>Open task</button></form>"
        )
        return page(
            "Tasks",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.get("/portal/tasks/open")
    async def open_task(
        request: Request,
        task_id: str = "",
    ) -> Response:
        await authorized(request, "tasks:read")
        parsed = required_uuid(task_id, "task_id")
        return RedirectResponse(
            f"/portal/tasks/{parsed}",
            status_code=303,
        )

    @app.get("/portal/tasks/{task_id}")
    async def task_detail(
        request: Request,
        task_id: UUID,
    ) -> HTMLResponse:
        current = await authorized(request, "tasks:read")
        task = await required_execution().get_task(
            scope=ownership(current.principal),
            task_id=task_id,
        )
        body = (
            "<p>Task ID: "
            + escape(str(task.task_id))
            + "</p><p>Status: "
            + escape(task.status.value)
            + "</p><p>Execution mode: "
            + escape(task.requested_execution_mode.value)
            + "</p><p>Accepted: "
            + escape(", ".join(task.accepted_requirements))
            + "</p><p>Missing: "
            + escape(", ".join(task.missing_requirements))
            + "</p><p><a href='/portal/tasks/"
            + escape(str(task.task_id))
            + "/result'>Result</a> | "
            "<a href='/portal/tasks/"
            + escape(str(task.task_id))
            + "/attempts'>Attempts</a></p>"
        )
        if task.effective_target is not None:
            body += (
                "<p>Effective target: "
                + escape(task.effective_target.provider_id)
                + " / "
                + escape(task.effective_target.model_id)
                + " / "
                + escape(task.effective_target.reasoning_profile)
                + "</p>"
            )
        if (
            "tasks:write" in current.principal.permissions
            and not task.status.terminal
        ):
            body += (
                "<form method='post' action='/portal/tasks/"
                + escape(str(task.task_id))
                + "/cancel'>"
                + csrf_field(request)
                + "<input type='hidden' name='idempotency_key' value='"
                + escape(str(uuid7()))
                + "'><button type='submit'>Cancel task</button></form>"
            )
        return page(
            "Task",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.post("/portal/tasks/{task_id}/cancel")
    async def cancel_task(
        request: Request,
        task_id: UUID,
    ) -> Response:
        current, data = await authorized_mutation(
            request,
            "tasks:write",
        )
        await required_execution().cancel_task(
            scope=ownership(current.principal),
            task_id=task_id,
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse(
            f"/portal/tasks/{task_id}",
            status_code=303,
        )

    @app.get("/portal/tasks/{task_id}/result")
    async def task_result(
        request: Request,
        task_id: UUID,
    ) -> HTMLResponse:
        current = await authorized(request, "tasks:read")
        result = await required_execution().get_task_result(
            scope=ownership(current.principal),
            task_id=task_id,
        )
        body = (
            "<p>Status: "
            + escape(result.status)
            + "</p><p>Accepted: "
            + escape(", ".join(result.accepted))
            + "</p><p>Missing: "
            + escape(", ".join(result.missing))
            + "</p><pre>"
            + safe_json(result.result)
            + "</pre>"
        )
        return page(
            "Task result",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.get("/portal/tasks/{task_id}/attempts")
    async def task_attempts(
        request: Request,
        task_id: UUID,
    ) -> HTMLResponse:
        current = await authorized(request, "tasks:read")
        page_data = await required_execution().list_task_attempts(
            scope=ownership(current.principal),
            task_id=task_id,
            cursor=None,
            limit=100,
        )
        rows: list[str] = []
        for attempt in page_data.items:
            provider_name = await required_execution().provider_name(
                scope=ownership(current.principal),
                provider_id=attempt.target.provider_id,
            )
            rows.append(
                "<tr><td>"
                + escape(str(attempt.attempt_id))
                + "</td><td>"
                + escape(provider_name)
                + "</td><td>"
                + escape(attempt.target.model_id)
                + "</td><td>"
                + escape(attempt.target.reasoning_profile)
                + "</td><td>"
                + escape(str(attempt.cycle))
                + "</td><td>"
                + escape(str(attempt.attempt_index))
                + "</td><td>"
                + escape(attempt.status.value)
                + "</td><td>"
                + escape(attempt.provider_outcome or "")
                + "</td><td><a href='/portal/tasks/"
                + escape(str(task_id))
                + "/attempts/"
                + escape(str(attempt.attempt_id))
                + "/exchanges'>Evidence</a></td></tr>"
            )
        body = (
            "<table><thead><tr><th>Attempt</th><th>Provider</th>"
            "<th>Model</th><th>Reasoning</th><th>Cycle</th>"
            "<th>Index</th><th>Status</th><th>Outcome</th>"
            "<th>Evidence</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
        return page(
            "Attempts",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.get(
        "/portal/tasks/{task_id}/attempts/{attempt_id}/exchanges"
    )
    async def attempt_exchanges(
        request: Request,
        task_id: UUID,
        attempt_id: UUID,
    ) -> HTMLResponse:
        current = await authorized(request, "tasks:read")
        evidence = await required_execution().list_attempt_exchanges(
            scope=ownership(current.principal),
            task_id=task_id,
            attempt_id=attempt_id,
        )
        rows: list[str] = []
        for item in evidence:
            response = item.response_evidence
            rows.append(
                "<section><h2>"
                + escape(item.status_label)
                + "</h2><p>Provider: "
                + escape(item.provider_name)
                + "</p><p>Operation: "
                + escape(item.operation)
                + "</p><h3>Sanitized request</h3><pre>"
                + escape(item.request_evidence.sanitized_raw_body)
                + "</pre>"
                + (
                    ""
                    if response is None
                    else (
                        "<h3>Sanitized response</h3><pre>"
                        + escape(response.sanitized_raw_body)
                        + "</pre>"
                    )
                )
                + "</section>"
            )
        return page(
            "Exchange evidence",
            "".join(rows),
            nav=navigation(current.principal.permissions),
        )

    @app.get("/portal/providers")
    async def providers_page(request: Request) -> HTMLResponse:
        current = await authorized(request, "providers:read")
        runtime = required_execution()
        scope = ownership(current.principal)
        providers = await runtime.list_providers(scope=scope)
        models = await runtime.list_models(scope=scope)
        model_rows = "".join(
            "<tr><td>"
            + escape(item.provider_id)
            + "</td><td>"
            + escape(item.model_name)
            + "</td><td>"
            + escape(", ".join(item.reasoning_profiles))
            + "</td><td>"
            + escape(", ".join(item.capabilities))
            + "</td></tr>"
            for item in models
        )
        provider_rows = "".join(
            "<li>"
            + escape(item.provider_name)
            + " — "
            + escape(", ".join(item.capabilities))
            + "</li>"
            for item in providers
        )
        body = (
            "<h2>Providers</h2><ul>"
            + provider_rows
            + "</ul><h2>Models</h2><table><thead><tr>"
            "<th>Provider</th><th>Model</th><th>Reasoning</th>"
            "<th>Capabilities</th></tr></thead><tbody>"
            + model_rows
            + "</tbody></table>"
        )
        return page(
            "Providers & Models",
            body,
            nav=navigation(current.principal.permissions),
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
