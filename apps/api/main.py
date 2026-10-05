"""FastAPI composition root for the ORQETIA client API."""

from __future__ import annotations

import json
from pathlib import Path

from orqetia.identity.authentication import (
    AuthenticatedPrincipal,
    AuthenticationRejected,
)
from orqetia.infrastructure.http import create_app
from orqetia.settings import RuntimeSettings


class UnconfiguredAuthenticator:
    async def authenticate_bearer(self, _token: str) -> AuthenticatedPrincipal:
        raise AuthenticationRejected("authentication adapter is not configured")


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json"

settings = RuntimeSettings()
canonical_openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

app = create_app(
    openapi_document=canonical_openapi,
    authenticator=UnconfiguredAuthenticator(),
)
app.state.runtime_settings = settings.safe_summary()
