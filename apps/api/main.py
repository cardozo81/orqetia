"""FastAPI composition root for the ORQETIA client API."""

from __future__ import annotations

import json
from pathlib import Path

from orqetia.identity.authentication import (
    AuthenticatedPrincipal,
    AuthenticationRejected,
)
from orqetia.infrastructure.availability import (
    MaintenanceMode,
    OperationalAvailabilityController,
)
from orqetia.infrastructure.health import DatabaseReadinessProbe
from orqetia.infrastructure.http import create_app
from orqetia.infrastructure.persistence import create_engine
from orqetia.settings import Environment, RuntimeSettings


class UnconfiguredAuthenticator:
    async def authenticate_bearer(self, _token: str) -> AuthenticatedPrincipal:
        raise AuthenticationRejected("authentication adapter is not configured")


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json"

settings = RuntimeSettings()
canonical_openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
engine = create_engine(settings)

app = create_app(
    openapi_document=canonical_openapi,
    authenticator=UnconfiguredAuthenticator(),
    readiness_probe=DatabaseReadinessProbe(engine),
    availability_controller=OperationalAvailabilityController(
        process_role="api",
        maintenance_mode=MaintenanceMode(settings.maintenance_mode),
        retry_after_seconds=settings.maintenance_retry_after_seconds,
    ),
    cors_allowed_origins=settings.cors_allowed_origins,
    enable_hsts=settings.environment is Environment.PRODUCTION,
    shutdown_callback=engine.dispose,
)
app.state.runtime_settings = settings.safe_summary()
