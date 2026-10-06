"""FastAPI application factory for the canonical client API shell."""

import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

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
from orqetia.identity import (
    ClientAccessCredentialService,
    IdempotencyConflict,
)
from orqetia.identity.authentication import (
    AuthenticatedPrincipal,
    AuthenticationBackendUnavailable,
    AuthenticationRejected,
    BearerAuthenticator,
)
from orqetia.infrastructure.health import ReadinessProbe

from .models import (
    CredentialCreateRequest,
    CredentialIssueResponse,
    CredentialListResponse,
    CredentialMetadataView,
    ErrorDetail,
    ErrorEnvelope,
    EstimateExplicitExecution,
    EstimateRequest,
    EstimateResponse,
    ExplicitExecution,
    SessionCreateRequest,
    TaskCreateRequest,
)

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def create_app(
    *,
    openapi_document: dict[str, Any],
    authenticator: BearerAuthenticator,
    readiness_probe: ReadinessProbe | None = None,
    cors_allowed_origins: Sequence[str] = (),
    enable_hsts: bool = False,
    shutdown_callback: Callable[[], Awaitable[None]] | None = None,
    estimation_service: EstimateService | None = None,
    client_credential_service: ClientAccessCredentialService | None = None,
) -> FastAPI:
    """Build the client API shell around the versioned canonical OpenAPI document."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if shutdown_callback is not None:
                await shutdown_callback()

    app = FastAPI(
        title="ORQETIA Client API",
        version="1.0.0-development",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-Correlation-ID",
            ],
            expose_headers=["Location", "Retry-After", "X-Correlation-ID"],
        )

    def canonical_openapi() -> dict[str, Any]:
        return deepcopy(openapi_document)

    app.openapi = canonical_openapi  # type: ignore[method-assign]

    def correlation_id(request: Request) -> str:
        return str(getattr(request.state, "correlation_id", uuid4()))

    def error_response(
        request: Request,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
    ) -> JSONResponse:
        envelope = ErrorEnvelope(
            code=code,
            message=message,
            correlation_id=correlation_id(request),
            details=details or [],
        )
        return JSONResponse(
            status_code=status_code,
            content=envelope.model_dump(mode="json"),
        )

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        if enable_hsts:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("X-Correlation-ID", "")
        request.state.correlation_id = (
            supplied if _CORRELATION_ID.fullmatch(supplied) else str(uuid4())
        )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(item) for item in error.get("loc", ())[1:]) or None,
                reason=str(error.get("msg", "Invalid value")),
            )
            for error in exc.errors()
        ]
        return error_response(
            request,
            status_code=400,
            code="INVALID_REQUEST",
            message="Request validation failed.",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        return error_response(
            request,
            status_code=exc.status_code,
            code="HTTP_ERROR",
            message="Request could not be processed.",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
        return error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="An internal error occurred.",
        )

    async def current_principal(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> AuthenticatedPrincipal:
        if authorization is None:
            raise ApiError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")

        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise ApiError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")

        try:
            return await authenticator.authenticate_bearer(token.strip())
        except AuthenticationRejected as exc:
            raise ApiError(401, "AUTHENTICATION_REJECTED", "Authentication was rejected.") from exc
        except AuthenticationBackendUnavailable as exc:
            raise ApiError(
                503,
                "AUTHENTICATION_UNAVAILABLE",
                "Authentication service is unavailable.",
            ) from exc

    def require_scopes(*required: str) -> Any:
        async def dependency(
            principal: Annotated[AuthenticatedPrincipal, Depends(current_principal)],
        ) -> AuthenticatedPrincipal:
            if set(required) - principal.scopes:
                raise ApiError(403, "FORBIDDEN", "Required permission is missing.")
            return principal

        return dependency


    def required_credential_service() -> ClientAccessCredentialService:
        if client_credential_service is None:
            raise ApiError(
                503,
                "CREDENTIAL_SERVICE_UNAVAILABLE",
                "Client credential service is not configured.",
            )
        return client_credential_service

    def principal_owner(principal: AuthenticatedPrincipal) -> tuple[UUID, UUID]:
        if principal.tenant_id is None or principal.client_id is None:
            raise ApiError(403, "FORBIDDEN", "Tenant/client ownership is required.")
        try:
            return UUID(principal.tenant_id), UUID(principal.client_id)
        except ValueError as exc:
            raise ApiError(
                403,
                "FORBIDDEN",
                "Authenticated ownership identifiers are invalid.",
            ) from exc

    def credential_metadata_view(credential: Any) -> CredentialMetadataView:
        return CredentialMetadataView.model_validate(credential.safe_view())

    def not_implemented() -> None:
        raise ApiError(
            501,
            "NOT_IMPLEMENTED",
            "Endpoint contract is defined but runtime implementation is not available yet.",
        )

    @app.post("/v1/sessions", response_model=None)
    async def create_session(
        _payload: SessionCreateRequest,
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("sessions:write"))],
    ) -> None:
        not_implemented()

    @app.get("/v1/sessions/{session_id}", response_model=None)
    async def get_session(
        session_id: UUID,
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("sessions:read"))],
    ) -> None:
        del session_id
        not_implemented()

    @app.post("/v1/sessions/{session_id}/tasks", response_model=None)
    async def create_task(
        session_id: UUID,
        payload: TaskCreateRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("tasks:write"))],
    ) -> None:
        del session_id
        if (
            isinstance(payload.execution, ExplicitExecution)
            and "tasks:target" not in principal.scopes
        ):
            raise ApiError(403, "FORBIDDEN", "Explicit target permission is missing.")
        not_implemented()

    @app.get("/v1/tasks/{task_id}", response_model=None)
    async def get_task(
        task_id: UUID,
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("tasks:read"))],
    ) -> None:
        del task_id
        not_implemented()

    @app.get("/v1/tasks/{task_id}/result", response_model=None)
    async def get_task_result(
        task_id: UUID,
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("tasks:read"))],
    ) -> None:
        del task_id
        not_implemented()

    @app.post("/v1/tasks/{task_id}/cancel", response_model=None)
    async def cancel_task(
        task_id: UUID,
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("tasks:cancel"))],
    ) -> None:
        del task_id
        not_implemented()

    @app.get("/v1/tasks/{task_id}/attempts", response_model=None)
    async def list_task_attempts(
        task_id: UUID,
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("tasks:read"))],
        _cursor: Annotated[str | None, Query(alias="cursor", max_length=500)] = None,
        _limit: Annotated[int, Query(alias="limit", ge=1, le=100)] = 50,
    ) -> None:
        del task_id, _cursor, _limit
        not_implemented()

    @app.get("/v1/tasks/{task_id}/attempts/{attempt_id}/exchanges", response_model=None)
    async def list_attempt_exchanges(
        task_id: UUID,
        attempt_id: UUID,
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("tasks:read"))],
    ) -> None:
        del task_id, attempt_id
        not_implemented()

    @app.post("/v1/estimates", response_model=EstimateResponse)
    async def create_estimate(
        payload: EstimateRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("estimates:write"))],
        _idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> EstimateResponse:
        if (
            isinstance(payload.execution, EstimateExplicitExecution)
            and "tasks:target" not in principal.scopes
        ):
            raise ApiError(403, "FORBIDDEN", "Explicit target permission is missing.")
        if principal.tenant_id is None or principal.client_id is None:
            raise ApiError(403, "FORBIDDEN", "Tenant/client ownership is required.")
        if estimation_service is None:
            raise ApiError(
                503,
                "ESTIMATION_UNAVAILABLE",
                "Estimation service is not configured.",
            )

        execution_mode = EstimateExecutionMode.AUTO
        target = None
        if isinstance(payload.execution, EstimateExplicitExecution):
            execution_mode = EstimateExecutionMode.EXPLICIT_TARGET
            target = EstimateTarget(
                provider_id=payload.execution.target.provider_id,
                model_id=payload.execution.target.model_id,
                reasoning_profile=payload.execution.target.reasoning_profile,
            )
        spec = EstimateSpec(
            operation=payload.operation,
            input_payload=payload.input,
            reference_scope=ReferenceScope(payload.reference_scope),
            execution_mode=execution_mode,
            target=target,
        )
        try:
            result = await estimation_service.estimate(
                subject=EstimateSubject(
                    tenant_id=principal.tenant_id,
                    client_id=principal.client_id,
                ),
                spec=spec,
            )
        except EstimateForbidden as exc:
            raise ApiError(
                403,
                "TARGET_FORBIDDEN",
                "Requested target is not authorized.",
            ) from exc
        except EstimateUnavailable as exc:
            details = [
                ErrorDetail(field="reference_scope", reason=exc.reason),
                *[
                    ErrorDetail(field="limitations", reason=limitation)
                    for limitation in exc.limitations[:10]
                ],
            ]
            raise ApiError(
                422,
                "ESTIMATE_UNAVAILABLE",
                "A safe technical estimate is not available for this request.",
                details=details,
            ) from exc
        return EstimateResponse.model_validate(result.client_payload())


    @app.get("/v1/credentials", response_model=CredentialListResponse)
    async def list_client_credentials(
        principal: Annotated[
            AuthenticatedPrincipal,
            Depends(require_scopes("credentials:read")),
        ],
    ) -> CredentialListResponse:
        service = required_credential_service()
        tenant_id, client_id = principal_owner(principal)
        items = await service.list_owned(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        return CredentialListResponse(
            items=[credential_metadata_view(item) for item in items]
        )

    @app.post("/v1/credentials", response_model=CredentialIssueResponse)
    async def issue_client_credential(
        payload: CredentialCreateRequest,
        principal: Annotated[
            AuthenticatedPrincipal,
            Depends(require_scopes("credentials:write")),
        ],
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> CredentialIssueResponse:
        if set(payload.scopes) - principal.scopes:
            raise ApiError(
                403,
                "FORBIDDEN",
                "A credential cannot receive scopes the caller does not hold.",
            )
        service = required_credential_service()
        tenant_id, client_id = principal_owner(principal)
        try:
            result = await service.issue(
                tenant_id=tenant_id,
                client_id=client_id,
                display_label=payload.display_label,
                scopes=tuple(payload.scopes),
                idempotency_key=idempotency_key,
                occurred_at=datetime.now(UTC),
                expires_at=payload.expires_at,
            )
        except IdempotencyConflict as exc:
            raise ApiError(
                409,
                "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST",
                "Idempotency key was reused with a different request.",
            ) from exc
        secret = None if result.secret is None else result.secret.reveal_once()
        return CredentialIssueResponse(
            credential=credential_metadata_view(result.credential),
            secret=secret,
            secret_available=secret is not None,
            replayed=result.replayed,
        )

    @app.post(
        "/v1/credentials/{credential_id}/rotate",
        response_model=CredentialIssueResponse,
    )
    async def rotate_client_credential(
        credential_id: UUID,
        principal: Annotated[
            AuthenticatedPrincipal,
            Depends(require_scopes("credentials:write")),
        ],
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> CredentialIssueResponse:
        service = required_credential_service()
        tenant_id, client_id = principal_owner(principal)
        try:
            result = await service.rotate(
                tenant_id=tenant_id,
                client_id=client_id,
                credential_id=credential_id,
                idempotency_key=idempotency_key,
                occurred_at=datetime.now(UTC),
            )
        except IdempotencyConflict as exc:
            raise ApiError(
                409,
                "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST",
                "Idempotency key was reused with a different request.",
            ) from exc
        except (LookupError, PermissionError) as exc:
            raise ApiError(404, "NOT_FOUND", "Credential was not found.") from exc
        except ValueError as exc:
            raise ApiError(
                409,
                "CREDENTIAL_STATE_CONFLICT",
                "Credential cannot be rotated from its current state.",
            ) from exc
        secret = None if result.secret is None else result.secret.reveal_once()
        return CredentialIssueResponse(
            credential=credential_metadata_view(result.credential),
            secret=secret,
            secret_available=secret is not None,
            replayed=result.replayed,
        )

    @app.post(
        "/v1/credentials/{credential_id}/revoke",
        response_model=CredentialMetadataView,
    )
    async def revoke_client_credential(
        credential_id: UUID,
        principal: Annotated[
            AuthenticatedPrincipal,
            Depends(require_scopes("credentials:write")),
        ],
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> CredentialMetadataView:
        service = required_credential_service()
        tenant_id, client_id = principal_owner(principal)
        try:
            result = await service.revoke(
                tenant_id=tenant_id,
                client_id=client_id,
                credential_id=credential_id,
                idempotency_key=idempotency_key,
                occurred_at=datetime.now(UTC),
            )
        except IdempotencyConflict as exc:
            raise ApiError(
                409,
                "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST",
                "Idempotency key was reused with a different request.",
            ) from exc
        except (LookupError, PermissionError) as exc:
            raise ApiError(404, "NOT_FOUND", "Credential was not found.") from exc
        return credential_metadata_view(result.credential)

    @app.get("/v1/providers", response_model=None)
    async def list_providers(
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("catalog:read"))],
    ) -> None:
        not_implemented()

    @app.get("/v1/models", response_model=None)
    async def list_models(
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("catalog:read"))],
        _provider_id: Annotated[str | None, Query(alias="provider_id", max_length=100)] = None,
    ) -> None:
        del _provider_id
        not_implemented()

    @app.get("/health/live", include_in_schema=False)
    async def health_live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", include_in_schema=False)
    async def health_ready() -> JSONResponse:
        if readiness_probe is None:
            return JSONResponse(status_code=200, content={"status": "ready", "checks": {}})
        try:
            await readiness_probe.check()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "checks": {"database": "unavailable"}},
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "checks": {"database": "ready"}},
        )

    @app.get("/v1/usage", response_model=None)
    async def get_usage(
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("usage:read"))],
        _cursor: Annotated[str | None, Query(alias="cursor", max_length=500)] = None,
        _limit: Annotated[int, Query(alias="limit", ge=1, le=100)] = 50,
        _from: Annotated[datetime | None, Query(alias="from")] = None,
        _to: Annotated[datetime | None, Query(alias="to")] = None,
    ) -> None:
        del _cursor, _limit, _from, _to
        not_implemented()

    return app
