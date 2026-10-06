"""Authentication application port.

Token/JWT/OIDC validation is implemented by a standards-based infrastructure
adapter later. HTTP code delegates opaque bearer credentials to this port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    subject_type: str
    subject_id: str
    tenant_id: str | None
    client_id: str | None
    scopes: frozenset[str]
    credential_id: str | None = None
    credential_fingerprint: str | None = None


class AuthenticationRejected(Exception):
    """The presented credential is absent/invalid/revoked for this request."""


class AuthenticationBackendUnavailable(Exception):
    """The configured standards-based authentication backend is unavailable."""


class BearerAuthenticator(Protocol):
    async def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal:
        """Validate an opaque bearer credential and return trusted identity state."""
