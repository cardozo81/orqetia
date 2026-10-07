"""FastAPI process composition root."""

from __future__ import annotations

import json
from pathlib import Path

from orqetia.infrastructure.availability import (
    MaintenanceMode,
    OperationalAvailabilityController,
)
from orqetia.infrastructure.health import DatabaseReadinessProbe
from orqetia.infrastructure.http import create_app
from orqetia.infrastructure.http.composition import (
    build_client_api_runtime_services,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import Environment, ProcessRole, RuntimeSettings

_ROOT = Path(__file__).resolve().parents[2]
_OPENAPI = json.loads(
    (_ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json").read_text(
        encoding="utf-8"
    )
)

settings = RuntimeSettings()
if settings.process_role is not ProcessRole.API:
    raise RuntimeError("API entry point requires ORQETIA_PROCESS_ROLE=api")

engine = create_engine(settings)
session_factory = create_session_factory(engine)
runtime = build_client_api_runtime_services(session_factory)

app = create_app(
    openapi_document=_OPENAPI,
    authenticator=runtime.authenticator,
    readiness_probe=DatabaseReadinessProbe(engine),
    cors_allowed_origins=settings.cors_allowed_origins,
    enable_hsts=settings.environment is Environment.PRODUCTION,
    shutdown_callback=engine.dispose,
    client_credential_service=runtime.credentials,
    client_usage_service=runtime.usage,
    execution_runtime=runtime.execution,
    availability_controller=OperationalAvailabilityController(
        process_role="api",
        maintenance_mode=MaintenanceMode(settings.maintenance_mode),
        retry_after_seconds=settings.maintenance_retry_after_seconds,
    ),
)
