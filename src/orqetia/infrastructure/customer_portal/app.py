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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from orqetia.audit import CustomerActivityStore, activity_event
from orqetia.estimation import (
    EstimateExecutionMode,
    EstimateForbidden,
    EstimateService,
    EstimateSpec,
    EstimateSubject,
    EstimateTarget,
    EstimateUnavailable,
    ReferenceScope,
)
from orqetia.execution import OwnershipScope
from orqetia.identity import (
    ClientAccessCredentialService,
    CredentialMutationResult,
    CustomerPrincipal,
    IdempotencyConflict,
)
from orqetia.identity.backoffice_authz import HumanAuthenticationContext
from orqetia.identity.customer_authz import CustomerAuthorizationService
from orqetia.identity.customer_web_sessions import (
    AuthenticatedCustomerPortalSession,
    CustomerPortalWebSessionService,
)
from orqetia.identity.web_sessions import WebSessionRejected
from orqetia.infrastructure.http.execution_runtime import (
    ClientExecutionArtifactUnavailable,
    ClientExecutionConflict,
    ClientExecutionForbidden,
    ClientExecutionNotFound,
    ClientExecutionRuntime,
    ClientRequestedTarget,
)
from orqetia.read_models import ClientUsageReportService

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
    credentials: ClientAccessCredentialService | None = None,
    usage: ClientUsageReportService | None = None,
    estimation: EstimateService | None = None,
    activity: CustomerActivityStore | None = None,
    client_openapi_document: dict[str, object] | None = None,
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

    async def authenticated(
        request: Request,
    ) -> AuthenticatedCustomerPortalSession:
        token = request.cookies.get(_SESSION_COOKIE, "")
        if not token:
            raise WebSessionRejected("Customer Portal session required")
        return await sessions.authenticate(
            session_token=token,
            occurred_at=datetime.now(UTC),
        )

    async def authorized(
        request: Request,
        permission: str,
    ) -> AuthenticatedCustomerPortalSession:
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

    def required_credentials() -> ClientAccessCredentialService:
        if credentials is None:
            raise CustomerPortalUnavailable(
                "Client credential service is unavailable"
            )
        return credentials

    def required_usage() -> ClientUsageReportService:
        if usage is None:
            raise CustomerPortalUnavailable(
                "Client usage reporting is unavailable"
            )
        return usage

    def required_estimation() -> EstimateService:
        if estimation is None:
            raise CustomerPortalUnavailable(
                "Client estimation service is unavailable"
            )
        return estimation

    def required_activity() -> CustomerActivityStore:
        if activity is None:
            raise CustomerPortalUnavailable(
                "Client activity service is unavailable"
            )
        return activity

    async def record_activity(
        current: AuthenticatedCustomerPortalSession,
        *,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        if activity is None:
            return
        principal = current.principal
        await activity.record(
            activity_event(
                tenant_id=principal.tenant_id,
                client_id=principal.client_id,
                identity_id=principal.identity_id,
                membership_id=principal.membership_id,
                action=action,
                occurred_at=datetime.now(UTC),
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )

    def delegable_scopes(permissions: frozenset[str]) -> frozenset[str]:
        output: set[str] = set()
        if "sessions:read" in permissions:
            output.add("sessions:read")
        if "tasks:read" in permissions:
            output.add("tasks:read")
        if "tasks:write" in permissions:
            output.update(
                {
                    "sessions:write",
                    "tasks:write",
                    "tasks:cancel",
                    "tasks:target",
                }
            )
        if "providers:read" in permissions:
            output.add("catalog:read")
        if "usage:read" in permissions:
            output.add("usage:read")
        if "estimates:write" in permissions:
            output.add("estimates:write")
        return frozenset(output)

    def ownership(principal: CustomerPrincipal) -> OwnershipScope:
        return OwnershipScope(
            tenant_id=principal.tenant_id,
            client_id=principal.client_id,
        )

    async def authorized_mutation(
        request: Request,
        permission: str,
    ) -> tuple[AuthenticatedCustomerPortalSession, dict[str, str]]:
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

    @app.exception_handler(EstimateForbidden)
    async def handle_estimate_forbidden(
        _request: Request,
        _exc: EstimateForbidden,
    ) -> HTMLResponse:
        response = page(
            "Forbidden",
            "<p>Requested estimate target is not authorized.</p>",
        )
        response.status_code = 403
        return response

    @app.exception_handler(EstimateUnavailable)
    async def handle_estimate_unavailable(
        _request: Request,
        exc: EstimateUnavailable,
    ) -> HTMLResponse:
        limitations = "".join(
            "<li>" + escape(item) + "</li>"
            for item in exc.limitations[:10]
        )
        response = page(
            "Estimate unavailable",
            "<p>"
            + escape(exc.reason)
            + "</p><ul>"
            + limitations
            + "</ul>",
        )
        response.status_code = 422
        return response

    @app.exception_handler(IdempotencyConflict)
    async def handle_idempotency_conflict(
        _request: Request,
        _exc: IdempotencyConflict,
    ) -> HTMLResponse:
        response = page(
            "Conflict",
            "<p>Idempotency key was reused with a different request.</p>",
        )
        response.status_code = 409
        return response

    @app.exception_handler(LookupError)
    async def handle_not_found(
        _request: Request,
        _exc: LookupError,
    ) -> HTMLResponse:
        response = page("Not found", "<p>Resource not found.</p>")
        response.status_code = 404
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

    @app.get("/openapi.json", include_in_schema=False)
    async def client_openapi() -> JSONResponse:
        if client_openapi_document is None:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "OPENAPI_UNAVAILABLE",
                    "message": "Canonical Client API contract is unavailable.",
                },
            )
        return JSONResponse(content=client_openapi_document)

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
        await record_activity(
            current,
            action="SESSION_CREATE",
            resource_type="session",
            resource_id=str(session.session_id),
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
        await record_activity(
            current,
            action="TASK_CREATE",
            resource_type="task",
            resource_id=str(task.task_id),
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
        await record_activity(
            current,
            action="TASK_CANCEL",
            resource_type="task",
            resource_id=str(task_id),
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

    @app.get("/portal/usage")
    async def usage_page(
        request: Request,
        period_from: datetime | None = None,
        period_to: datetime | None = None,
    ) -> HTMLResponse:
        current = await authorized(request, "usage:read")
        report = await required_usage().read(
            tenant_id=current.principal.tenant_id,
            client_id=current.principal.client_id,
            period_from=period_from,
            period_to=period_to,
            cursor=None,
            limit=50,
        )
        rows = "".join(
            "<tr><td>"
            + escape(str(item.period_start))
            + "</td><td>"
            + escape(str(item.period_end))
            + "</td><td>"
            + escape(str(item.input_tokens))
            + "</td><td>"
            + escape(str(item.cached_input_tokens))
            + "</td><td>"
            + escape(str(item.output_tokens))
            + "</td><td>"
            + escape(str(item.reasoning_tokens))
            + "</td><td>"
            + escape(str(item.total_tokens))
            + "</td><td>"
            + escape(
                ", ".join(
                    f"{native.unit}: {native.quantity}"
                    for native in item.native_usage
                )
            )
            + "</td></tr>"
            for item in report.items
        )
        body = (
            "<p>Technical usage only. Monetary provider data is not exposed.</p>"
            "<table><thead><tr><th>From</th><th>To</th>"
            "<th>Input</th><th>Cached input</th><th>Output</th>"
            "<th>Reasoning</th><th>Total</th><th>Native usage</th>"
            "</tr></thead><tbody>"
            + rows
            + "</tbody></table><p>As of: "
            + escape("" if report.as_of is None else str(report.as_of))
            + "</p>"
        )
        return page(
            "Usage",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.get("/portal/estimates")
    async def estimates_page(request: Request) -> HTMLResponse:
        current = await authorized(request, "estimates:write")
        body = (
            "<p>Estimates are provider-free and based on governed benchmarks.</p>"
            "<form method='post' action='/portal/estimates'>"
            + csrf_field(request)
            + "<label>Operation <input name='operation' "
            "value='TASK_EXECUTION' required></label>"
            "<label>Input JSON <textarea name='input_json' required>"
            '{"input_text":""}'
            "</textarea></label>"
            "<label>Reference scope <select name='reference_scope'>"
            "<option>CLIENT_ONLY</option><option>GLOBAL_PUBLIC</option>"
            "</select></label>"
            "<label>Provider <input name='provider_id'></label>"
            "<label>Model <input name='model_id'></label>"
            "<label>Reasoning profile "
            "<input name='reasoning_profile'></label>"
            "<button type='submit'>Estimate</button></form>"
            "<p>Leave target fields empty for AUTO.</p>"
        )
        return page(
            "Token Estimates",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.post("/portal/estimates")
    async def create_estimate(request: Request) -> HTMLResponse:
        current, data = await authorized_mutation(
            request,
            "estimates:write",
        )
        try:
            payload = json.loads(data.get("input_json", ""))
        except json.JSONDecodeError as error:
            raise ValueError("input_json must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("input_json must be a JSON object")

        provider_id = data.get("provider_id", "").strip()
        model_id = data.get("model_id", "").strip()
        reasoning = data.get("reasoning_profile", "").strip()
        if any((provider_id, model_id, reasoning)) and not all(
            (provider_id, model_id, reasoning)
        ):
            raise ValueError(
                "explicit estimate requires provider/model/reasoning profile"
            )
        mode = EstimateExecutionMode.AUTO
        target = None
        if provider_id:
            if "tasks:write" not in current.principal.permissions:
                raise PermissionError("explicit target permission denied")
            mode = EstimateExecutionMode.EXPLICIT_TARGET
            target = EstimateTarget(
                provider_id=provider_id,
                model_id=model_id,
                reasoning_profile=reasoning,
            )

        spec = EstimateSpec(
            operation=data.get("operation", "").strip(),
            input_payload=payload,
            reference_scope=ReferenceScope(
                data.get("reference_scope", "CLIENT_ONLY")
            ),
            execution_mode=mode,
            target=target,
        )
        result = await required_estimation().estimate(
            subject=EstimateSubject(
                tenant_id=str(current.principal.tenant_id),
                client_id=str(current.principal.client_id),
            ),
            spec=spec,
        )
        await record_activity(
            current,
            action="ESTIMATE_CREATE",
            resource_type="estimate",
        )
        return page(
            "Estimate result",
            "<pre>" + safe_json(result.client_payload()) + "</pre>"
            "<p><a href='/portal/estimates'>New estimate</a></p>",
            nav=navigation(current.principal.permissions),
        )

    @app.get("/portal/activity")
    async def activity_page(request: Request) -> HTMLResponse:
        current = await authorized(request, "audit:read")
        items = await required_activity().list_owned(
            tenant_id=current.principal.tenant_id,
            client_id=current.principal.client_id,
            limit=100,
        )
        rows = "".join(
            "<tr><td>"
            + escape(str(item.occurred_at))
            + "</td><td>"
            + escape(item.action)
            + "</td><td>"
            + escape(item.result)
            + "</td><td>"
            + escape(item.resource_type or "")
            + "</td><td>"
            + escape(item.resource_id or "")
            + "</td></tr>"
            for item in items
        )
        return page(
            "Activity",
            "<table><thead><tr><th>Time</th><th>Action</th>"
            "<th>Result</th><th>Resource</th><th>ID</th>"
            "</tr></thead><tbody>"
            + rows
            + "</tbody></table>",
            nav=navigation(current.principal.permissions),
        )

    @app.get("/portal/docs")
    async def documentation_page(request: Request) -> HTMLResponse:
        current = await authorized(request, "docs:read")
        body = (
            "<p>Canonical machine-readable API contract: "
            "<a href='/openapi.json'>/openapi.json</a></p>"
            "<p>Client API base path: <code>/v1</code>.</p>"
            "<p>Service integrations authenticate with Bearer credentials "
            "created in API Credentials. Secrets are shown once.</p>"
            "<p>Omit an execution target for AUTO. Explicit targets remain "
            "inside the authorized provider/model/reasoning envelope.</p>"
            "<p>The complete endpoint manual is maintained by #27 and must "
            "remain synchronized with this OpenAPI contract.</p>"
        )
        return page(
            "Documentation",
            body,
            nav=navigation(current.principal.permissions),
        )

    @app.get("/portal/credentials")
    async def credentials_page(request: Request) -> HTMLResponse:
        current = await authorized(request, "credentials:read")
        service = required_credentials()
        items = await service.list_owned(
            tenant_id=current.principal.tenant_id,
            client_id=current.principal.client_id,
        )
        rows = "".join(
            "<tr><td>"
            + escape(item.display_label)
            + "</td><td>"
            + escape(item.fingerprint)
            + "</td><td>"
            + escape(", ".join(item.scopes))
            + "</td><td>"
            + escape(item.status.value)
            + "</td><td>"
            + escape(str(item.key_version))
            + "</td><td>"
            + escape("" if item.expires_at is None else str(item.expires_at))
            + "</td><td>"
            + escape("" if item.last_used_at is None else str(item.last_used_at))
            + "</td><td>"
            + (
                ""
                if "credentials:write" not in current.principal.permissions
                else (
                    "<form method='post' action='/portal/credentials/"
                    + escape(str(item.credential_id))
                    + "/rotate'>"
                    + csrf_field(request)
                    + "<input type='hidden' name='idempotency_key' value='"
                    + escape(str(uuid7()))
                    + "'><button type='submit'>Rotate</button></form>"
                    "<form method='post' action='/portal/credentials/"
                    + escape(str(item.credential_id))
                    + "/revoke'>"
                    + csrf_field(request)
                    + "<input type='hidden' name='idempotency_key' value='"
                    + escape(str(uuid7()))
                    + "'><button type='submit'>Revoke</button></form>"
                )
            )
            + "</td></tr>"
            for item in items
        )
        body = (
            "<table><thead><tr><th>Label</th><th>Fingerprint</th>"
            "<th>Scopes</th><th>Status</th><th>Version</th>"
            "<th>Expires</th><th>Last used</th><th>Actions</th>"
            "</tr></thead><tbody>"
            + rows
            + "</tbody></table>"
        )
        if "credentials:write" in current.principal.permissions:
            allowed = ", ".join(
                sorted(delegable_scopes(current.principal.permissions))
            )
            body += (
                "<h2>Issue credential</h2>"
                "<p>Allowed integration scopes: "
                + escape(allowed)
                + "</p><form method='post' action='/portal/credentials'>"
                + csrf_field(request)
                + "<input type='hidden' name='idempotency_key' value='"
                + escape(str(uuid7()))
                + "'><label>Label <input name='display_label' "
                "maxlength='200' required></label>"
                "<label>Scopes <input name='scopes' value='"
                + escape(allowed)
                + "' required></label>"
                "<button type='submit'>Issue credential</button></form>"
            )
        return page(
            "API Credentials",
            body,
            nav=navigation(current.principal.permissions),
        )

    def requested_scopes(
        raw: str,
        *,
        allowed: frozenset[str],
    ) -> tuple[str, ...]:
        values = tuple(
            sorted(
                {
                    item.strip()
                    for item in raw.split(",")
                    if item.strip()
                }
            )
        )
        if not values:
            raise ValueError("at least one integration scope is required")
        if set(values) - allowed:
            raise PermissionError(
                "requested integration scope exceeds customer authority"
            )
        return values

    def one_time_secret_page(
        *,
        title: str,
        result: CredentialMutationResult,
        permissions: frozenset[str],
    ) -> HTMLResponse:
        secret = (
            None
            if result.secret is None
            else result.secret.reveal_once()
        )
        body = (
            "<p>Credential: "
            + escape(result.credential.display_label)
            + "</p><p>Fingerprint: "
            + escape(result.credential.fingerprint)
            + "</p>"
        )
        if secret is None:
            body += (
                "<p>The secret is not available on an idempotent replay. "
                "Rotate the credential to obtain a new one-time secret.</p>"
            )
        else:
            body += (
                "<p>This secret is shown once. Store it securely now.</p>"
                "<pre>"
                + escape(secret)
                + "</pre>"
            )
        body += "<p><a href='/portal/credentials'>Back to credentials</a></p>"
        return page(
            title,
            body,
            nav=navigation(permissions),
        )

    @app.post("/portal/credentials")
    async def issue_credential(request: Request) -> HTMLResponse:
        current, data = await authorized_mutation(
            request,
            "credentials:write",
        )
        allowed = delegable_scopes(current.principal.permissions)
        result = await required_credentials().issue(
            tenant_id=current.principal.tenant_id,
            client_id=current.principal.client_id,
            display_label=data.get("display_label", "").strip(),
            scopes=requested_scopes(
                data.get("scopes", ""),
                allowed=allowed,
            ),
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        await record_activity(
            current,
            action="CREDENTIAL_ISSUE",
            resource_type="credential",
            resource_id=str(result.credential.credential_id),
        )
        return one_time_secret_page(
            title="Credential issued",
            result=result,
            permissions=current.principal.permissions,
        )

    @app.post("/portal/credentials/{credential_id}/rotate")
    async def rotate_credential(
        request: Request,
        credential_id: UUID,
    ) -> HTMLResponse:
        current, data = await authorized_mutation(
            request,
            "credentials:write",
        )
        result = await required_credentials().rotate(
            tenant_id=current.principal.tenant_id,
            client_id=current.principal.client_id,
            credential_id=credential_id,
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        await record_activity(
            current,
            action="CREDENTIAL_ROTATE",
            resource_type="credential",
            resource_id=str(result.credential.credential_id),
        )
        return one_time_secret_page(
            title="Credential rotated",
            result=result,
            permissions=current.principal.permissions,
        )

    @app.post("/portal/credentials/{credential_id}/revoke")
    async def revoke_credential(
        request: Request,
        credential_id: UUID,
    ) -> Response:
        current, data = await authorized_mutation(
            request,
            "credentials:write",
        )
        result = await required_credentials().revoke(
            tenant_id=current.principal.tenant_id,
            client_id=current.principal.client_id,
            credential_id=credential_id,
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        await record_activity(
            current,
            action="CREDENTIAL_REVOKE",
            resource_type="credential",
            resource_id=str(result.credential.credential_id),
        )
        return RedirectResponse("/portal/credentials", status_code=303)

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
