"""FastAPI application factory for the canonical client API shell."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from orqetia.identity.authentication import (
    AuthenticatedPrincipal,
    AuthenticationBackendUnavailable,
    AuthenticationRejected,
    BearerAuthenticator,
)

from .models import (
    ErrorDetail,
    ErrorEnvelope,
    EstimateRequest,
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
) -> FastAPI:
    """Build the client API shell around the versioned canonical OpenAPI document."""

    app = FastAPI(
        title="ORQETIA Client API",
        version="1.0.0-development",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
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
        if isinstance(payload.execution, ExplicitExecution) and "tasks:target" not in principal.scopes:
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

    @app.post("/v1/estimates", response_model=None)
    async def create_estimate(
        _payload: EstimateRequest,
        _principal: Annotated[AuthenticatedPrincipal, Depends(require_scopes("estimates:write"))],
    ) -> None:
        not_implemented()

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
