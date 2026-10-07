"""PostgreSQL composition for the canonical Client API runtime."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.control_plane import (
    EffectiveExecutionPolicy,
    EffectiveProviderCatalog,
    PostgresExecutionPolicyRepository,
    PostgresProviderCatalogRepository,
)
from orqetia.execution import (
    PostgresClientApiIdempotencyJournal,
    PostgresExecutionSessionStore,
    PostgresExecutionTaskStore,
    PostgresProviderAttemptStore,
)
from orqetia.identity import (
    ClientAccessCredentialService,
    PostgresClientCredentialStore,
    StoredClientCredentialAuthenticator,
)
from orqetia.infrastructure.artifacts import PostgresClientArtifactStore
from orqetia.infrastructure.messaging import PostgresWorkQueue
from orqetia.read_models import (
    ClientUsageReportService,
    PostgresReportRollupStore,
)

from .execution_runtime import ClientExecutionRuntime

SessionFactory = async_sessionmaker[AsyncSession]


class _EffectivePolicyResolver:
    def __init__(self, repository: PostgresExecutionPolicyRepository) -> None:
        self._repository = repository

    async def resolve_effective(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> EffectiveExecutionPolicy:
        effective = await self._repository.get_effective(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        if effective is None:
            raise LookupError("effective execution policy not found")
        return effective


class _EffectiveCatalogResolver:
    def __init__(self, repository: PostgresProviderCatalogRepository) -> None:
        self._repository = repository

    async def resolve_effective(self) -> EffectiveProviderCatalog:
        effective = await self._repository.get_effective()
        if effective is None:
            raise LookupError("effective provider catalog not found")
        return effective


@dataclass(frozen=True)
class ClientApiRuntimeServices:
    authenticator: StoredClientCredentialAuthenticator
    credentials: ClientAccessCredentialService
    execution: ClientExecutionRuntime
    usage: ClientUsageReportService


def build_client_api_runtime_services(
    session_factory: SessionFactory,
) -> ClientApiRuntimeServices:
    """Compose durable client-facing services over one PostgreSQL session factory."""

    credential_store = PostgresClientCredentialStore(session_factory)
    artifacts = PostgresClientArtifactStore(session_factory)
    return ClientApiRuntimeServices(
        authenticator=StoredClientCredentialAuthenticator(store=credential_store),
        credentials=ClientAccessCredentialService(credential_store),
        execution=ClientExecutionRuntime(
            sessions=PostgresExecutionSessionStore(session_factory),
            tasks=PostgresExecutionTaskStore(session_factory),
            attempts=PostgresProviderAttemptStore(session_factory),
            policies=_EffectivePolicyResolver(
                PostgresExecutionPolicyRepository(session_factory)
            ),
            catalog=_EffectiveCatalogResolver(
                PostgresProviderCatalogRepository(session_factory)
            ),
            request_artifacts=artifacts,
            results=artifacts,
            exchanges=artifacts,
            idempotency=PostgresClientApiIdempotencyJournal(session_factory),
            work_queue=PostgresWorkQueue(session_factory),
        ),
        usage=ClientUsageReportService(
            PostgresReportRollupStore(session_factory)
        ),
    )
