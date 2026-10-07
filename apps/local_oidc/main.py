"""Deployable LOCAL/TEST synthetic identity provider process."""

from __future__ import annotations

from orqetia.infrastructure.local_oidc import (\n    create_local_oidc_app,\n    load_local_oidc_signing_key,\n)
from orqetia.settings import Environment, ProcessRole, RuntimeSettings

settings = RuntimeSettings()
if settings.process_role is not ProcessRole.LOCAL_IDP:
    raise RuntimeError(
        "local IdP entry point requires ORQETIA_PROCESS_ROLE=local_idp"
    )
if settings.environment not in {Environment.LOCAL, Environment.TEST}:
    raise RuntimeError("local IdP is restricted to LOCAL/TEST")
if settings.public_base_url is None:
    raise RuntimeError("local IdP requires public_base_url")
if settings.local_oidc_signing_key is None:
    raise RuntimeError("local IdP requires local OIDC signing key")

origin = str(settings.public_base_url).rstrip("/")
app = create_local_oidc_app(
    environment=settings.environment.value,
    signing_key=load_local_oidc_signing_key(\n        None\n        if settings.local_oidc_signing_key is None\n        else settings.local_oidc_signing_key.get_secret_value()\n    ),
    allowed_callbacks=(
        f"{origin}/backoffice/callback",
        f"{origin}/portal/callback",
    ),
)
