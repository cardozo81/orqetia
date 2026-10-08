"""Secure server-side Backoffice BFF shell."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from typing import Protocol
from urllib.parse import parse_qs
from uuid import UUID, uuid7

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from orqetia.control_plane import (
    AuthorizedExecutionTarget,
    ExternalCapacitySource,
    ProviderAccountStatus,
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaScope,
    SecretValue,
)
from orqetia.control_plane.pricing_catalog_postgres import decode_pricing_rules
from orqetia.control_plane.provider_catalog_postgres import (
    decode_provider_catalog,
    decode_provider_endpoints,
)
from orqetia.identity import (
    AuthenticatedWebSession,
    BackofficeAuthorizationService,
    BackofficeBindingStatus,
    BackofficeRole,
    BackofficeWebSessionService,
    HumanAuthenticationContext,
    WebSessionRejected,
)
from orqetia.read_models import (
    BackofficeReportAccess,
    IntelligenceDimension,
    IntelligenceQuery,
    ReportQuery,
)
from orqetia.tenancy import AdministrativeStatus

from .admin_console import BackofficeAdminServices

_SESSION_COOKIE = "__Host-orqetia-bo-session"
_CSRF_COOKIE = "__Host-orqetia-bo-csrf"
_OIDC_COOKIE = "__Host-orqetia-bo-oidc"


class OidcLoginRejected(PermissionError):
    """Trusted OIDC adapter rejected or could not validate the login transaction."""


class BackofficeUnavailable(RuntimeError):
    """Administrative composition is not configured for this deployment."""


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
    admin: BackofficeAdminServices | None = None,
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
        response.headers["Referrer-Policy"] = "strict-origin"
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

    async def authenticated(request: Request) -> AuthenticatedWebSession:
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


    def required_admin() -> BackofficeAdminServices:
        if admin is None:
            raise BackofficeUnavailable("Backoffice administrative services unavailable")
        return admin

    async def authorized_read(
        request: Request,
        permission: str,
    ) -> AuthenticatedWebSession:
        authenticated_session = await authenticated(request)
        if permission not in authenticated_session.principal.permissions:
            raise PermissionError("Backoffice permission denied")
        return authenticated_session

    async def authorized_mutation(
        request: Request,
        permission: str,
    ) -> tuple[AuthenticatedWebSession, dict[str, str]]:
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
        authorization.require_permission(
            principal=authenticated_session.principal,
            permission=permission,
            occurred_at=datetime.now(UTC),
        )
        return authenticated_session, data

    def csrf_field(request: Request) -> str:
        token = request.cookies.get(_CSRF_COOKIE, "")
        return (
            "<input type='hidden' name='csrf_token' value='"
            + escape(token)
            + "'>"
        )

    def page(title: str, body: str) -> HTMLResponse:
        navigation = (
            "<nav>"
            "<a href='/backoffice'>Home</a> | "
            "<a href='/backoffice/tenants'>Tenants</a> | "
            "<a href='/backoffice/users'>Users</a> | "
            "<a href='/backoffice/policies'>Policies</a> | "
            "<a href='/backoffice/quotas'>Quotas</a> | "
            "<a href='/backoffice/providers'>Providers</a> | "
            "<a href='/backoffice/client-credentials'>Client credentials</a> | "
            "<a href='/backoffice/intelligence'>Intelligence</a>"
            "</nav>"
        )
        return HTMLResponse(
            "<!doctype html><html><head><title>"
            + escape(title)
            + "</title></head><body><main>"
            + navigation
            + "<h1>"
            + escape(title)
            + "</h1>"
            + body
            + "</main></body></html>"
        )

    def required_uuid(value: str, field: str) -> UUID:
        try:
            return UUID(value)
        except ValueError as error:
            raise ValueError(f"{field} must be a UUID") from error

    def optional_text(data: dict[str, str], key: str) -> str | None:
        value = data.get(key, "").strip()
        return value or None

    def optional_uuid(data: dict[str, str], key: str) -> UUID | None:
        value = optional_text(data, key)
        return None if value is None else required_uuid(value, key)

    def optional_int(data: dict[str, str], key: str) -> int | None:
        value = optional_text(data, key)
        return None if value is None else int(value)

    def optional_datetime(data: dict[str, str], key: str) -> datetime | None:
        value = optional_text(data, key)
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{key} must be an ISO-8601 datetime") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{key} must include a timezone")
        return parsed

    def parse_targets(raw: str) -> tuple[AuthorizedExecutionTarget, ...]:
        targets: list[AuthorizedExecutionTarget] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = [item.strip() for item in line.split("|")]
            if len(parts) != 3:
                raise ValueError("targets must use provider|model|reasoning_profile")
            targets.append(AuthorizedExecutionTarget(*parts))
        if not targets:
            raise ValueError("at least one authorized target is required")
        return tuple(targets)

    def parse_roles(raw: str) -> tuple[BackofficeRole, ...]:
        values = tuple(
            BackofficeRole(value.strip())
            for value in raw.split(",")
            if value.strip()
        )
        if not values:
            raise ValueError("at least one Backoffice role is required")
        return values

    def report_filters(data: dict[str, str]) -> ReportQuery:
        return ReportQuery(
            period_from=optional_datetime(data, "period_from"),
            period_to=optional_datetime(data, "period_to"),
            tenant_id=optional_uuid(data, "tenant_id"),
            client_id=optional_uuid(data, "client_id"),
            client_credential_id=optional_uuid(data, "client_credential_id"),
            provider_id=optional_text(data, "provider_id"),
            provider_account_id=optional_uuid(data, "provider_account_id"),
            provider_credential_id=optional_uuid(data, "provider_credential_id"),
            session_id=optional_uuid(data, "session_id"),
            task_id=optional_uuid(data, "task_id"),
            attempt_id=optional_uuid(data, "attempt_id"),
            policy_version_id=optional_uuid(data, "policy_version_id"),
            model_id=optional_text(data, "model_id"),
            status=optional_text(data, "status"),
            error_class=optional_text(data, "error_class"),
        )

    @app.exception_handler(WebSessionRejected)
    async def handle_session_rejected(
        _request: Request,
        _exc: WebSessionRejected,
    ) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><title>Unauthorized</title><h1>Unauthorized</h1>",
            status_code=401,
        )

    @app.exception_handler(PermissionError)
    async def handle_forbidden(
        _request: Request,
        _exc: PermissionError,
    ) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><title>Forbidden</title><h1>Forbidden</h1>",
            status_code=403,
        )

    @app.exception_handler(LookupError)
    async def handle_not_found(
        _request: Request,
        _exc: LookupError,
    ) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><title>Not found</title><h1>Not found</h1>",
            status_code=404,
        )

    @app.exception_handler(ValueError)
    async def handle_invalid_input(
        _request: Request,
        _exc: ValueError,
    ) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><title>Invalid request</title><h1>Invalid request</h1>",
            status_code=400,
        )

    @app.exception_handler(BackofficeUnavailable)
    async def handle_unavailable(
        _request: Request,
        _exc: BackofficeUnavailable,
    ) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><title>Unavailable</title><h1>Backoffice unavailable</h1>",
            status_code=503,
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
            "<a href='/backoffice/users'>Users</a> "
            "<a href='/backoffice/policies'>Policies</a> "
            "<a href='/backoffice/quotas'>Quotas</a> "
            "<a href='/backoffice/providers'>Providers</a> "
            "<a href='/backoffice/client-credentials'>Client credentials</a> "
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


    @app.get("/backoffice/tenants")
    async def tenants_page(request: Request) -> HTMLResponse:
        await authorized_read(request, "tenancy:admin")
        services = required_admin()
        rows: list[str] = []
        for tenant in await services.tenancy_repository.list_tenants():
            clients = await services.tenancy_repository.list_clients(
                tenant_id=tenant.tenant_id
            )
            client_html = "".join(
                "<li>"
                + escape(client.display_name)
                + " — "
                + escape(str(client.client_id))
                + " — "
                + escape(client.status.value)
                + "</li>"
                for client in clients
            )
            rows.append(
                "<section><h2>"
                + escape(tenant.display_name)
                + "</h2><p>"
                + escape(str(tenant.tenant_id))
                + " — "
                + escape(tenant.status.value)
                + "</p><ul>"
                + client_html
                + "</ul>"
                + "<form method='post' action='/backoffice/tenants/"
                + escape(str(tenant.tenant_id))
                + "/status'>"
                + csrf_field(request)
                + "<select name='status'><option>ACTIVE</option>"
                + "<option>DISABLED</option></select><button>Set tenant status</button>"
                + "</form></section>"
            )
        body = (
            "".join(rows)
            + "<h2>Create tenant</h2><form method='post' action='/backoffice/tenants/create'>"
            + csrf_field(request)
            + "<label>Name <input name='display_name' maxlength='200' required></label>"
            + "<button>Create tenant</button></form>"
            + "<h2>Create client</h2><form method='post' action='/backoffice/clients/create'>"
            + csrf_field(request)
            + "<label>Tenant ID <input name='tenant_id' required></label>"
            + "<label>Name <input name='display_name' maxlength='200' required></label>"
            + "<button>Create client</button></form>"
            + "<h2>Client status</h2><form method='post' action='/backoffice/clients/status'>"
            + csrf_field(request)
            + "<label>Tenant ID <input name='tenant_id' required></label>"
            + "<label>Client ID <input name='client_id' required></label>"
            + "<select name='status'><option>ACTIVE</option><option>DISABLED</option></select>"
            + "<button>Set client status</button></form>"
        )
        return page("Tenants & clients", body)

    @app.post("/backoffice/tenants/create")
    async def create_tenant(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "tenancy:admin")
        await required_admin().tenancy.create_tenant(
            display_name=data.get("display_name", ""),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/tenants", status_code=303)

    @app.post("/backoffice/tenants/{tenant_id}/status")
    async def set_tenant_status(tenant_id: UUID, request: Request) -> Response:
        _session, data = await authorized_mutation(request, "tenancy:admin")
        await required_admin().tenancy.set_tenant_status(
            tenant_id=tenant_id,
            status=AdministrativeStatus(data.get("status", "")),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/tenants", status_code=303)

    @app.post("/backoffice/clients/create")
    async def create_client(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "tenancy:admin")
        await required_admin().tenancy.create_client(
            tenant_id=required_uuid(data.get("tenant_id", ""), "tenant_id"),
            display_name=data.get("display_name", ""),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/tenants", status_code=303)

    @app.post("/backoffice/clients/status")
    async def set_client_status(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "tenancy:admin")
        await required_admin().tenancy.set_client_status(
            tenant_id=required_uuid(data.get("tenant_id", ""), "tenant_id"),
            client_id=required_uuid(data.get("client_id", ""), "client_id"),
            status=AdministrativeStatus(data.get("status", "")),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/tenants", status_code=303)

    @app.get("/backoffice/users")
    async def users_page(request: Request) -> HTMLResponse:
        await authorized_read(request, "users:admin")
        bindings = await required_admin().bindings.list_all()
        items = "".join(
            "<li>"
            + escape(item.issuer)
            + " | "
            + escape(item.subject)
            + " | "
            + escape(",".join(role.value for role in item.roles))
            + " | "
            + escape(item.status.value)
            + "</li>"
            for item in bindings
        )
        body = (
            "<ul>" + items + "</ul>"
            + "<h2>Create binding</h2><form method='post' action='/backoffice/users/create'>"
            + csrf_field(request)
            + "<label>Issuer <input name='issuer' required></label>"
            + "<label>Subject <input name='subject' required></label>"
            + "<label>Roles comma-separated <input name='roles' required></label>"
            + "<button>Create binding</button></form>"
            + "<h2>Update roles</h2><form method='post' action='/backoffice/users/roles'>"
            + csrf_field(request)
            + "<label>Binding ID <input name='binding_id' required></label>"
            + "<label>Roles <input name='roles' required></label>"
            + "<button>Update roles</button></form>"
            + "<h2>Update status</h2><form method='post' action='/backoffice/users/status'>"
            + csrf_field(request)
            + "<label>Binding ID <input name='binding_id' required></label>"
            + "<select name='status'><option>ACTIVE</option><option>DISABLED</option></select>"
            + "<button>Update status</button></form>"
        )
        return page("Backoffice users", body)

    @app.post("/backoffice/users/create")
    async def create_user_binding(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "users:admin")
        await authorization.create_binding(
            issuer=data.get("issuer", ""),
            subject=data.get("subject", ""),
            roles=parse_roles(data.get("roles", "")),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/users", status_code=303)

    @app.post("/backoffice/users/roles")
    async def update_user_roles(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "users:admin")
        await authorization.set_roles(
            binding_id=required_uuid(data.get("binding_id", ""), "binding_id"),
            roles=parse_roles(data.get("roles", "")),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/users", status_code=303)

    @app.post("/backoffice/users/status")
    async def update_user_status(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "users:admin")
        await authorization.set_status(
            binding_id=required_uuid(data.get("binding_id", ""), "binding_id"),
            status=BackofficeBindingStatus(data.get("status", "")),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/users", status_code=303)

    @app.get("/backoffice/policies")
    async def policies_page(
        request: Request,
        tenant_id: str = "",
        client_id: str = "",
    ) -> HTMLResponse:
        await authorized_read(request, "policy:admin")
        current = "<p>No owner selected.</p>"
        if tenant_id and client_id:
            try:
                effective = await required_admin().execution_policies.resolve_effective(
                    tenant_id=required_uuid(tenant_id, "tenant_id"),
                    client_id=required_uuid(client_id, "client_id"),
                )
                version = effective.version
                current = (
                    "<p>Effective version "
                    + escape(str(version.version_number))
                    + " / "
                    + escape(str(version.policy_version_id))
                    + "</p><ul>"
                    + "".join(
                        "<li>"
                        + escape(target.provider_id)
                        + " | "
                        + escape(target.model_id)
                        + " | "
                        + escape(target.reasoning_profile)
                        + "</li>"
                        for target in version.authorized_targets
                    )
                    + "</ul>"
                )
            except LookupError:
                current = "<p>No effective policy for selected owner.</p>"
        body = (
            current
            + "<form method='get' action='/backoffice/policies'>"
            + "<label>Tenant ID <input name='tenant_id'></label>"
            + "<label>Client ID <input name='client_id'></label>"
            + "<button>Load</button></form>"
            + "<h2>Publish policy</h2><form method='post' action='/backoffice/policies/publish'>"
            + csrf_field(request)
            + "<label>Tenant ID <input name='tenant_id' required></label>"
            + "<label>Client ID <input name='client_id' required></label>"
            + (
                "<label>Max cycles <input name='max_cycles' type='number' "
                "min='1' required></label>"
            )
            + (
                "<label>Max attempts <input name='max_attempts' type='number' "
                "min='1' required></label>"
            )
            + (
                "<label>Cycle delay seconds "
                "<input name='cycle_delay_seconds' type='number' min='0' "
                "value='0'></label>"
            )
            + (
                "<label>Retry-After cap seconds "
                "<input name='retry_after_cap_seconds' type='number' min='0' "
                "max='300' value='300'></label>"
            )
            + "<label>Targets (provider|model|reasoning per line)"
            + "<textarea name='targets' required></textarea></label>"
            + "<button>Publish & activate</button></form>"
        )
        return page("Execution policies", body)

    @app.post("/backoffice/policies/publish")
    async def publish_policy(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "policy:admin")
        services = required_admin()
        targets = parse_targets(data.get("targets", ""))
        effective_catalog = await services.provider_catalog.resolve_effective()
        for target in targets:
            effective_catalog.registry.adapter_for(target)
        await services.execution_policies.publish_and_activate(
            tenant_id=required_uuid(data.get("tenant_id", ""), "tenant_id"),
            client_id=required_uuid(data.get("client_id", ""), "client_id"),
            max_cycles=int(data.get("max_cycles", "")),
            max_attempts=int(data.get("max_attempts", "")),
            cycle_delay_seconds=int(data.get("cycle_delay_seconds", "0")),
            retry_after_cap_seconds=int(data.get("retry_after_cap_seconds", "300")),
            authorized_targets=targets,
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/policies", status_code=303)

    @app.get("/backoffice/quotas")
    async def quotas_page(
        request: Request,
        scope: str = "",
        tenant_id: str = "",
        client_id: str = "",
    ) -> HTMLResponse:
        await authorized_read(request, "quotas:admin")
        current = "<p>Select an owner to inspect quota policies.</p>"
        if scope and tenant_id:
            selected_scope = QuotaScope(scope)
            selected_client = None if not client_id else required_uuid(client_id, "client_id")
            rows = await required_admin().quota_policies.list_for_subject(
                scope=selected_scope,
                tenant_id=required_uuid(tenant_id, "tenant_id"),
                client_id=selected_client,
            )
            current = "<ul>" + "".join(
                "<li>"
                + escape(item.reference)
                + " | "
                + escape(item.metric.value)
                + " | limit="
                + escape(str(item.limit))
                + " | "
                + escape(item.enforcement.value)
                + "</li>"
                for item in rows
            ) + "</ul>"
        body = (
            current
            + "<form method='get' action='/backoffice/quotas'>"
            + "<select name='scope'><option>TENANT</option><option>CLIENT</option></select>"
            + "<label>Tenant ID <input name='tenant_id' required></label>"
            + "<label>Client ID <input name='client_id'></label><button>Load</button></form>"
            + "<h2>Publish quota</h2><form method='post' action='/backoffice/quotas/publish'>"
            + csrf_field(request)
            + "<select name='scope'><option>TENANT</option><option>CLIENT</option></select>"
            + "<label>Tenant ID <input name='tenant_id' required></label>"
            + "<label>Client ID <input name='client_id'></label>"
            + "<label>Metric <select name='metric'>"
            + "".join("<option>" + escape(metric.value) + "</option>" for metric in QuotaMetric)
            + "</select></label>"
            + "<label>Limit <input name='limit' required></label>"
            + "<label>Burst <input name='burst' value='0'></label>"
            + "<label>Period seconds <input name='period_seconds'></label>"
            + "<select name='enforcement'><option>HARD</option><option>SOFT</option></select>"
            + "<label>Provider ID <input name='provider_id'></label>"
            + "<label>Native unit <input name='native_unit'></label>"
            + "<button>Publish quota</button></form>"
        )
        return page("Quota policies", body)

    @app.post("/backoffice/quotas/publish")
    async def publish_quota(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "quotas:admin")
        await required_admin().quota_policies.publish(
            scope=QuotaScope(data.get("scope", "")),
            tenant_id=required_uuid(data.get("tenant_id", ""), "tenant_id"),
            client_id=optional_uuid(data, "client_id"),
            metric=QuotaMetric(data.get("metric", "")),
            limit=Decimal(data.get("limit", "")),
            enforcement=QuotaEnforcementMode(data.get("enforcement", "")),
            effective_from=datetime.now(UTC),
            period_seconds=optional_int(data, "period_seconds"),
            burst=Decimal(data.get("burst", "0")),
            native_unit=optional_text(data, "native_unit"),
            provider_id=optional_text(data, "provider_id"),
        )
        return RedirectResponse("/backoffice/quotas", status_code=303)

    @app.get("/backoffice/providers")
    async def providers_page(request: Request) -> HTMLResponse:
        await authorized_read(request, "providers:admin")
        services = required_admin()
        try:
            catalog = await services.provider_catalog.resolve_effective()
        except LookupError:
            catalog = None
        try:
            pricing = await services.provider_pricing.resolve_effective()
        except LookupError:
            pricing = None

        provider_sections: list[str] = []
        if catalog is not None:
            for provider in catalog.version.providers:
                accounts = await services.provider_account_repository.list_for_provider(
                    provider.provider_id
                )
                account_rows: list[str] = []
                for account in accounts:
                    credentials = await services.provider_credential_repository.list_active(
                        provider_account_id=account.provider_account_id
                    )
                    credential_rows = "".join(
                        "<li>credential "
                        + escape(str(item.credential_id))
                        + " fingerprint="
                        + escape(item.fingerprint)
                        + " key_version="
                        + escape(str(item.key_version))
                        + " status="
                        + escape(item.status.value)
                        + "</li>"
                        for item in credentials
                    )
                    account_rows.append(
                        "<li>"
                        + escape(account.display_label)
                        + " — "
                        + escape(str(account.provider_account_id))
                        + " — "
                        + escape(account.status.value)
                        + "<ul>"
                        + credential_rows
                        + "</ul></li>"
                    )
                model_rows = "".join(
                    "<li>"
                    + escape(model.model_id)
                    + " — approved="
                    + escape(str(model.approved))
                    + " — capabilities="
                    + escape(",".join(sorted(cap.value for cap in model.approved_capabilities)))
                    + "</li>"
                    for model in provider.models
                )
                provider_sections.append(
                    "<section><h2>"
                    + escape(provider.display_name)
                    + " ("
                    + escape(provider.provider_id)
                    + ")</h2><ul>"
                    + model_rows
                    + "</ul><h3>Accounts</h3><ul>"
                    + "".join(account_rows)
                    + "</ul></section>"
                )

        pricing_summary = (
            "<p>No effective pricing catalog.</p>"
            if pricing is None
            else "<p>Pricing catalog version "
            + escape(str(pricing.version.version_number))
            + " with "
            + escape(str(len(pricing.version.rules)))
            + " rules.</p>"
        )

        body = (
            "".join(provider_sections)
            + pricing_summary
            + "<h2>Create provider account</h2>"
            + "<form method='post' action='/backoffice/providers/accounts/create'>"
            + csrf_field(request)
            + "<label>Provider ID <input name='provider_id' required></label>"
            + "<label>Display label <input name='display_label' required></label>"
            + "<label>Priority <input name='priority' type='number' min='0' value='100'></label>"
            + "<label>Commercial mode <input name='commercial_mode'></label>"
            + "<label>Commercial tier <input name='commercial_tier'></label>"
            + "<label>Region <input name='region'></label>"
            + "<label>Contract reference <input name='contract_reference'></label>"
            + "<button>Create account</button></form>"
            + "<h2>Provider account status</h2>"
            + "<form method='post' action='/backoffice/providers/accounts/status'>"
            + csrf_field(request)
            + "<label>Account ID <input name='provider_account_id' required></label>"
            + "<select name='status'><option>ACTIVE</option><option>DISABLED</option></select>"
            + "<button>Set status</button></form>"
            + "<h2>Provider credential (secret is write-only)</h2>"
            + "<form method='post' action='/backoffice/providers/credentials/create'>"
            + csrf_field(request)
            + "<label>Provider ID <input name='provider_id' required></label>"
            + "<label>Account ID <input name='provider_account_id' required></label>"
            + (
                "<label>Secret <input type='password' autocomplete='new-password' "
                "name='secret' required></label>"
            )
            + "<button>Create credential</button></form>"
            + "<form method='post' action='/backoffice/providers/credentials/rotate'>"
            + csrf_field(request)
            + "<label>Credential ID <input name='credential_id' required></label>"
            + (
                "<label>New secret <input type='password' "
                "autocomplete='new-password' name='secret' required></label>"
            )
            + "<button>Rotate credential</button></form>"
            + "<form method='post' action='/backoffice/providers/credentials/revoke'>"
            + csrf_field(request)
            + "<label>Credential ID <input name='credential_id' required></label>"
            + "<button>Revoke credential</button></form>"
            + "<form method='post' action='/backoffice/providers/credentials/preflight'>"
            + csrf_field(request)
            + "<label>Credential ID <input name='credential_id' required></label>"
            + "<button>Preflight</button></form>"
            + "<h2>Observed provider capacity</h2>"
            + "<form method='post' action='/backoffice/providers/capacity'>"
            + csrf_field(request)
            + "<label>Account ID <input name='provider_account_id' required></label>"
            + "<label>Native unit <input name='native_unit' required></label>"
            + (
                "<select name='source'><option>PROVIDER_API</option>"
                "<option>PROVIDER_CONSOLE</option></select>"
            )
            + "<label>Source reference <input name='source_reference' required></label>"
            + "<label>Remaining <input name='remaining'></label>"
            + "<label>Limit <input name='limit'></label>"
            + "<button>Record observation</button></form>"
            + "<h2>Publish provider catalog</h2>"
            + "<form method='post' action='/backoffice/providers/catalog/publish'>"
            + csrf_field(request)
            + "<label>Providers JSON <textarea name='catalog_json' required></textarea></label>"
            + "<label>Endpoints JSON <textarea name='endpoints_json'>[]</textarea></label>"
            + "<button>Publish catalog</button></form>"
            + "<h2>Publish provider pricing</h2>"
            + "<form method='post' action='/backoffice/providers/pricing/publish'>"
            + csrf_field(request)
            + "<label>Pricing rules JSON <textarea name='rules_json' required></textarea></label>"
            + "<button>Publish pricing</button></form>"
        )
        return page("Providers & pricing", body)

    @app.post("/backoffice/providers/accounts/create")
    async def create_provider_account(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        await required_admin().provider_accounts.create_account(
            provider_id=data.get("provider_id", ""),
            display_label=data.get("display_label", ""),
            occurred_at=datetime.now(UTC),
            priority=int(data.get("priority", "100")),
            commercial_mode=optional_text(data, "commercial_mode"),
            commercial_tier=optional_text(data, "commercial_tier"),
            region=optional_text(data, "region"),
            contract_reference=optional_text(data, "contract_reference"),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.post("/backoffice/providers/accounts/status")
    async def provider_account_status(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        await required_admin().provider_accounts.set_status(
            provider_account_id=required_uuid(
                data.get("provider_account_id", ""),
                "provider_account_id",
            ),
            status=ProviderAccountStatus(data.get("status", "")),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.post("/backoffice/providers/credentials/create")
    async def create_provider_credential(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        services = required_admin()
        account_id = required_uuid(
            data.get("provider_account_id", ""),
            "provider_account_id",
        )
        account = await services.provider_account_repository.get(account_id)
        if account is None:
            raise LookupError("provider account not found")
        provider_id = data.get("provider_id", "")
        if account.provider_id != provider_id:
            raise PermissionError("provider account/provider mismatch")
        await services.provider_credentials.create(
            provider_id=provider_id,
            provider_account_id=account_id,
            secret=SecretValue(data.get("secret", "")),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.post("/backoffice/providers/credentials/rotate")
    async def rotate_provider_credential(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        await required_admin().provider_credentials.rotate(
            credential_id=required_uuid(data.get("credential_id", ""), "credential_id"),
            new_secret=SecretValue(data.get("secret", "")),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.post("/backoffice/providers/credentials/revoke")
    async def revoke_provider_credential(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        await required_admin().provider_credentials.revoke(
            credential_id=required_uuid(data.get("credential_id", ""), "credential_id"),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.post("/backoffice/providers/credentials/preflight")
    async def preflight_provider_credential(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        await required_admin().provider_credentials.preflight(
            credential_id=required_uuid(data.get("credential_id", ""), "credential_id"),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.post("/backoffice/providers/capacity")
    async def record_provider_capacity(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        remaining = optional_text(data, "remaining")
        limit = optional_text(data, "limit")
        await required_admin().provider_accounts.record_external_capacity(
            provider_account_id=required_uuid(
                data.get("provider_account_id", ""),
                "provider_account_id",
            ),
            native_unit=data.get("native_unit", ""),
            source=ExternalCapacitySource(data.get("source", "")),
            source_reference=data.get("source_reference", ""),
            observed_at=datetime.now(UTC),
            remaining=None if remaining is None else Decimal(remaining),
            limit=None if limit is None else Decimal(limit),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.post("/backoffice/providers/catalog/publish")
    async def publish_provider_catalog(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        raw_catalog = data.get("catalog_json", "")
        raw_endpoints = data.get("endpoints_json", "[]")
        if (
            len(raw_catalog.encode("utf-8")) > 200_000
            or len(raw_endpoints.encode("utf-8")) > 100_000
        ):
            raise ValueError("provider catalog form is too large")
        providers = decode_provider_catalog(json.loads(raw_catalog))
        endpoints = decode_provider_endpoints(json.loads(raw_endpoints or "[]"))
        await required_admin().provider_catalog.publish_and_activate(
            providers=providers,
            endpoints=endpoints,
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.post("/backoffice/providers/pricing/publish")
    async def publish_provider_pricing(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "providers:admin")
        raw_rules = data.get("rules_json", "")
        if len(raw_rules.encode("utf-8")) > 300_000:
            raise ValueError("provider pricing form is too large")
        await required_admin().provider_pricing.publish_and_activate(
            rules=decode_pricing_rules(json.loads(raw_rules)),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/providers", status_code=303)

    @app.get("/backoffice/client-credentials")
    async def client_credentials_page(
        request: Request,
        tenant_id: str = "",
        client_id: str = "",
    ) -> HTMLResponse:
        await authorized_read(request, "client-credentials:admin")
        current = "<p>Select tenant/client to inspect credentials.</p>"
        if tenant_id and client_id:
            tenant = required_uuid(tenant_id, "tenant_id")
            client = required_uuid(client_id, "client_id")
            items = await required_admin().client_credentials.list_owned(
                tenant_id=tenant,
                client_id=client,
            )
            current = "<ul>" + "".join(
                "<li>"
                + escape(str(item.credential_id))
                + " | "
                + escape(item.display_label)
                + " | fingerprint="
                + escape(item.fingerprint)
                + " | "
                + escape(item.status.value)
                + "</li>"
                for item in items
            ) + "</ul>"
        form_key = str(uuid7())
        body = (
            current
            + "<form method='get' action='/backoffice/client-credentials'>"
            + "<label>Tenant ID <input name='tenant_id'></label>"
            + "<label>Client ID <input name='client_id'></label><button>Load</button></form>"
            + "<h2>Issue client credential</h2>"
            + "<form method='post' action='/backoffice/client-credentials/issue'>"
            + csrf_field(request)
            + "<input type='hidden' name='idempotency_key' value='" + escape(form_key) + "'>"
            + "<label>Tenant ID <input name='tenant_id' required></label>"
            + "<label>Client ID <input name='client_id' required></label>"
            + "<label>Label <input name='display_label' required></label>"
            + "<label>Scopes comma-separated <input name='scopes' required></label>"
            + "<button>Issue credential</button></form>"
            + "<h2>Rotate credential</h2>"
            + "<form method='post' action='/backoffice/client-credentials/rotate'>"
            + csrf_field(request)
            + "<input type='hidden' name='idempotency_key' value='" + escape(str(uuid7())) + "'>"
            + "<label>Tenant ID <input name='tenant_id' required></label>"
            + "<label>Client ID <input name='client_id' required></label>"
            + "<label>Credential ID <input name='credential_id' required></label>"
            + "<button>Rotate credential</button></form>"
            + "<h2>Revoke credential</h2>"
            + "<form method='post' action='/backoffice/client-credentials/revoke'>"
            + csrf_field(request)
            + "<input type='hidden' name='idempotency_key' value='" + escape(str(uuid7())) + "'>"
            + "<label>Tenant ID <input name='tenant_id' required></label>"
            + "<label>Client ID <input name='client_id' required></label>"
            + "<label>Credential ID <input name='credential_id' required></label>"
            + "<button>Revoke credential</button></form>"
        )
        return page("Client integration credentials", body)

    @app.post("/backoffice/client-credentials/issue")
    async def issue_client_credential(request: Request) -> HTMLResponse:
        _session, data = await authorized_mutation(request, "client-credentials:admin")
        services = required_admin()
        tenant_id = required_uuid(data.get("tenant_id", ""), "tenant_id")
        client_id = required_uuid(data.get("client_id", ""), "client_id")
        await services.tenancy.resolve_active_owner(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        result = await services.client_credentials.issue(
            tenant_id=tenant_id,
            client_id=client_id,
            display_label=data.get("display_label", ""),
            scopes=tuple(
                value.strip()
                for value in data.get("scopes", "").split(",")
                if value.strip()
            ),
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        secret = None if result.secret is None else result.secret.reveal_once()
        body = (
            "<p>Credential "
            + escape(str(result.credential.credential_id))
            + " created.</p>"
            + (
                "<p>Idempotent replay: secret is not available again.</p>"
                if secret is None
                else "<p>One-time secret: <code>" + escape(secret) + "</code></p>"
            )
            + "<p><a href='/backoffice/client-credentials'>Return</a></p>"
        )
        return page("Client credential issued", body)

    @app.post("/backoffice/client-credentials/rotate")
    async def rotate_client_credential(request: Request) -> HTMLResponse:
        _session, data = await authorized_mutation(request, "client-credentials:admin")
        services = required_admin()
        tenant_id = required_uuid(data.get("tenant_id", ""), "tenant_id")
        client_id = required_uuid(data.get("client_id", ""), "client_id")
        await services.tenancy.resolve_active_owner(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        result = await services.client_credentials.rotate(
            tenant_id=tenant_id,
            client_id=client_id,
            credential_id=required_uuid(data.get("credential_id", ""), "credential_id"),
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        secret = None if result.secret is None else result.secret.reveal_once()
        body = (
            "<p>Credential rotated.</p>"
            + (
                "<p>Idempotent replay: secret is not available again.</p>"
                if secret is None
                else "<p>One-time secret: <code>" + escape(secret) + "</code></p>"
            )
            + "<p><a href='/backoffice/client-credentials'>Return</a></p>"
        )
        return page("Client credential rotated", body)

    @app.post("/backoffice/client-credentials/revoke")
    async def revoke_client_credential(request: Request) -> Response:
        _session, data = await authorized_mutation(request, "client-credentials:admin")
        services = required_admin()
        tenant_id = required_uuid(data.get("tenant_id", ""), "tenant_id")
        client_id = required_uuid(data.get("client_id", ""), "client_id")
        await services.tenancy.resolve_active_owner(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        await services.client_credentials.revoke(
            tenant_id=tenant_id,
            client_id=client_id,
            credential_id=required_uuid(data.get("credential_id", ""), "credential_id"),
            idempotency_key=data.get("idempotency_key", ""),
            occurred_at=datetime.now(UTC),
        )
        return RedirectResponse("/backoffice/client-credentials", status_code=303)

    @app.get("/backoffice/intelligence")
    async def intelligence_page(
        request: Request,
        period_from: str = "",
        period_to: str = "",
        tenant_id: str = "",
        client_id: str = "",
        provider_id: str = "",
        model_id: str = "",
        group_by: str = "PROVIDER,MODEL",
    ) -> HTMLResponse:
        authenticated_session = await authorized_read(request, "reports:read")
        values = {
            "period_from": period_from,
            "period_to": period_to,
            "tenant_id": tenant_id,
            "client_id": client_id,
            "provider_id": provider_id,
            "model_id": model_id,
        }
        filters = report_filters(values)
        dimensions = tuple(
            IntelligenceDimension(value.strip())
            for value in group_by.split(",")
            if value.strip()
        )
        result = await required_admin().intelligence.analyze(
            access=BackofficeReportAccess(
                can_view_financial=True,
                can_export="reports:export" in authenticated_session.principal.permissions,
                allowed_tenant_ids=None,
            ),
            query=IntelligenceQuery(
                filters=filters,
                group_by=dimensions or (IntelligenceDimension.PROVIDER,),
            ),
        )
        rows = "".join(
            "<tr><td>"
            + escape(" / ".join(value.value for value in row.dimensions))
            + "</td><td>"
            + escape(str(row.metrics.attempts))
            + "</td><td>"
            + escape(str(row.metrics.total_tokens))
            + "</td><td>"
            + escape(str(row.metrics.failures))
            + "</td><td>"
            + escape(str(row.metrics.average_latency_ms))
            + "</td><td>"
            + escape(
                ", ".join(
                    f"{item.currency} {item.amount}"
                    for item in row.metrics.observed_costs
                )
            )
            + "</td><td>"
            + escape(str(row.metrics.unpriced_attempts))
            + "</td></tr>"
            for row in result.rows
        )
        body = (
            "<p>as_of="
            + escape(str(result.as_of))
            + " timezone="
            + escape(result.timezone)
            + " source_rows="
            + escape(str(result.source_row_count))
            + "</p>"
            + "<form method='get' action='/backoffice/intelligence'>"
            + (
                "<label>From (ISO-8601) <input name='period_from' value='"
                + escape(period_from)
                + "'></label>"
            )
            + (
                "<label>To (ISO-8601) <input name='period_to' value='"
                + escape(period_to)
                + "'></label>"
            )
            + (
                "<label>Tenant ID <input name='tenant_id' value='"
                + escape(tenant_id)
                + "'></label>"
            )
            + (
                "<label>Client ID <input name='client_id' value='"
                + escape(client_id)
                + "'></label>"
            )
            + (
                "<label>Provider <input name='provider_id' value='"
                + escape(provider_id)
                + "'></label>"
            )
            + (
                "<label>Model <input name='model_id' value='"
                + escape(model_id)
                + "'></label>"
            )
            + (
                "<label>Group by <input name='group_by' value='"
                + escape(group_by)
                + "'></label>"
            )
            + "<button>Analyze</button></form>"
            + "<table><thead><tr><th>Dimensions</th><th>Attempts</th><th>Tokens</th>"
            + "<th>Failures</th><th>Avg latency</th><th>Observed cost</th>"
            + "<th>UNPRICED</th></tr></thead><tbody>"
            + rows
            + "</tbody></table>"
            + "<h2>Export filtered rollups</h2>"
            + "<form method='post' action='/backoffice/intelligence/export'>"
            + csrf_field(request)
            + "<input name='period_from' required value='" + escape(period_from) + "'>"
            + "<input name='period_to' required value='" + escape(period_to) + "'>"
            + "<input name='tenant_id' value='" + escape(tenant_id) + "'>"
            + "<input name='client_id' value='" + escape(client_id) + "'>"
            + "<input name='provider_id' value='" + escape(provider_id) + "'>"
            + "<input name='model_id' value='" + escape(model_id) + "'>"
            + "<button>Export JSON</button></form>"
        )
        return page("Operational & financial intelligence", body)

    @app.post("/backoffice/intelligence/export")
    async def export_intelligence(request: Request) -> Response:
        authenticated_session, data = await authorized_mutation(
            request,
            "reports:export",
        )
        payload = await required_admin().reporting.export(
            access=BackofficeReportAccess(
                can_view_financial=True,
                can_export=True,
                allowed_tenant_ids=None,
            ),
            filters=report_filters(data),
            occurred_at=datetime.now(UTC),
        )
        del authenticated_session
        return Response(
            content=json.dumps(payload, default=str, separators=(",", ":")),
            media_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=orqetia-backoffice-report.json"
            },
        )

    @app.get("/health/live", include_in_schema=False)
    async def health_live() -> dict[str, str]:
        return {"status": "live"}

    return app


def hmac_safe_equal(left: str, right: str) -> bool:
    import hmac

    if not left or not right:
        return False
    return hmac.compare_digest(left, right)
