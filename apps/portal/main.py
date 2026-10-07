"""Deployable LOCAL/TEST Customer Portal process."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse

from orqetia.infrastructure.customer_portal.composition import (
    build_customer_portal_app,
)
from orqetia.infrastructure.health import DatabaseReadinessProbe
from orqetia.infrastructure.local_oidc import LocalCustomerPortalOidcBroker
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import Environment, ProcessRole, RuntimeSettings

_ROOT = Path(__file__).resolve().parents[2]
_OPENAPI = json.loads(
    (_ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json").read_text(
        encoding="utf-8"
    )
)

settings = RuntimeSettings()
if settings.process_role is not ProcessRole.PORTAL:
    raise RuntimeError(
        "Portal entry point requires ORQETIA_PROCESS_ROLE=portal"
    )
if settings.environment not in {Environment.LOCAL, Environment.TEST}:
    raise RuntimeError("this Portal composition is restricted to LOCAL/TEST")
if settings.public_base_url is None:
    raise RuntimeError("Portal local composition requires public_base_url")
if settings.local_oidc_signing_key is None:
    raise RuntimeError("Portal local composition requires local OIDC signing key")

origin = str(settings.public_base_url).rstrip("/")
engine = create_engine(settings)
session_factory = create_session_factory(engine)
readiness = DatabaseReadinessProbe(engine)
oidc = LocalCustomerPortalOidcBroker(
    environment=settings.environment.value,
    signing_key=settings.local_oidc_signing_key.get_secret_value(),
    audience="portal",
    callback_url=f"{origin}/portal/callback",
    idp_public_url=f"{origin}/dev-idp",
)

app = build_customer_portal_app(
    session_factory=session_factory,
    oidc=oidc,
    allowed_origin=origin,
    client_openapi_document=_OPENAPI,
    enable_hsts=False,
)


@app.get("/health/live", include_in_schema=False)
async def health_live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready", include_in_schema=False)
async def health_ready() -> JSONResponse:
    try:
        await readiness.check()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": {"database": "unavailable"}},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "checks": {"database": "ready"}},
    )


app.add_event_handler("shutdown", engine.dispose)
