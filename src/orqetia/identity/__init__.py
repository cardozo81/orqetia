"""Identity, authentication and client access-credential contracts."""

from .authentication import (
    AuthenticatedPrincipal,
    AuthenticationBackendUnavailable,
    AuthenticationRejected,
    BearerAuthenticator,
)
from .backoffice_authz import (
    BackofficeAuthorizationService,
    BackofficeAuthzAuditEvent,
    BackofficeAuthzAuditSink,
    BackofficeBindingRepository,
    BackofficeBindingStatus,
    BackofficePrincipal,
    BackofficeRole,
    BackofficeUserBinding,
    HumanAuthenticationContext,
    InMemoryBackofficeAuthzAuditSink,
    InMemoryBackofficeBindingRepository,
    ROLE_MATRIX_VERSION,
)
from .backoffice_authz_postgres import PostgresBackofficeBindingRepository
from .backoffice_authz_tables import backoffice_user_bindings
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
    "BackofficeAuthorizationService",
    "BackofficeAuthzAuditEvent",
    "BackofficeAuthzAuditSink",
    "BackofficeBindingRepository",
    "BackofficeBindingStatus",
    "BackofficePrincipal",
    "BackofficeRole",
    "BackofficeUserBinding",
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
    "HumanAuthenticationContext",
    "InMemoryBackofficeAuthzAuditSink",
    "InMemoryBackofficeBindingRepository",
    "InMemoryClientCredentialStore",
    "OneTimeCredentialSecret",
    "PostgresBackofficeBindingRepository",
    "PostgresClientCredentialStore",
    "ROLE_MATRIX_VERSION",
    "StoredClientCredentialAuthenticator",
    "backoffice_user_bindings",
    "client_access_credentials",
    "client_credential_operations",
]
