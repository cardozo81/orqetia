"""Deployable LOCAL/TEST Backoffice process."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.responses import JSONResponse

from orqetia.infrastructure.backoffice.composition import build_backoffice_app
from orqetia.infrastructure.health import DatabaseReadinessProbe
from orqetia.infrastructure.local_oidc import (
    LocalBackofficeOidcBroker,
    load_local_oidc_signing_key,
)
from orqetia.infrastructure.local_secrets import LocalFileProviderSecretStore
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import (
    Environment,
    ProcessRole,
    RuntimeSettings,
    SecretStoreMode,
)

_LOCAL_SECRET_ROOT = Path("/var/lib/orqetia/provider-secrets")

settings = RuntimeSettings()
if settings.process_role is not ProcessRole.BACKOFFICE:
    raise RuntimeError(
        "Backoffice entry point requires ORQETIA_PROCESS_ROLE=backoffice"
    )
if settings.environment not in {Environment.LOCAL, Environment.TEST}:
    raise RuntimeError(
        "this Backoffice composition is restricted to LOCAL/TEST"
    )
if settings.public_base_url is None:
    raise RuntimeError("Backoffice local composition requires public_base_url")
if settings.secret_store_mode is not SecretStoreMode.LOCAL:
    raise RuntimeError(
        "Backoffice local composition requires ORQETIA_SECRET_STORE_MODE=local"
    )

origin = str(settings.public_base_url).rstrip("/")
engine = create_engine(settings)
session_factory = create_session_factory(engine)
readiness = DatabaseReadinessProbe(engine)
secret_store = LocalFileProviderSecretStore(
    _LOCAL_SECRET_ROOT,
    environment=settings.environment.value,
)
oidc = LocalBackofficeOidcBroker(
    environment=settings.environment.value,
    signing_key=load_local_oidc_signing_key(
        None
        if settings.local_oidc_signing_key is None
        else settings.local_oidc_signing_key.get_secret_value()
    ),
    audience="backoffice",
    callback_url=f"{origin}/backoffice/callback",
    idp_public_url=f"{origin}/dev-idp",
)

app = build_backoffice_app(
    session_factory=session_factory,
    oidc=oidc,
    allowed_origin=origin,
    secret_store=secret_store,
    environment=settings.environment.value,
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


@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        await engine.dispose()


app.router.lifespan_context = lifespan
