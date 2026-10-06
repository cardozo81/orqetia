"""Identity, authentication and client access-credential contracts."""

from .authentication import (
    AuthenticatedPrincipal,
    AuthenticationBackendUnavailable,
    AuthenticationRejected,
    BearerAuthenticator,
)
from .client_credential_postgres import PostgresClientCredentialStore
from .client_credential_tables import (
    client_access_credentials,
    client_credential_operations,
)
from .client_credentials import (
    ClientAccessCredential,
    ClientAccessCredentialService,
    ClientCredentialStatus,
    ClientCredentialStore,
    CredentialMutationResult,
    CredentialOperation,
    IdempotencyConflict,
    InMemoryClientCredentialStore,
    OneTimeCredentialSecret,
    StoredClientCredentialAuthenticator,
)

__all__ = [
    "AuthenticatedPrincipal",
    "AuthenticationBackendUnavailable",
    "AuthenticationRejected",
    "BearerAuthenticator",
    "ClientAccessCredential",
    "ClientAccessCredentialService",
    "ClientCredentialStatus",
    "ClientCredentialStore",
    "CredentialMutationResult",
    "CredentialOperation",
    "IdempotencyConflict",
    "InMemoryClientCredentialStore",
    "OneTimeCredentialSecret",
    "PostgresClientCredentialStore",
    "StoredClientCredentialAuthenticator",
    "client_access_credentials",
    "client_credential_operations",
]
