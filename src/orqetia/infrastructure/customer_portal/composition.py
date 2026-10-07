"""PostgreSQL composition for the official Customer Portal."""

from __future__ import annotations

from uuid import UUID, uuid7

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.audit import (
    CustomerActivityEvent,
    PostgresCustomerActivityStore,
)
from orqetia.control_plane import (
    EffectiveExecutionPolicy,
    EffectiveProviderCatalog,
    PostgresExecutionPolicyRepository,
    PostgresProviderCatalogRepository,
)
from orqetia.estimation import EstimateService
from orqetia.execution import (
    PostgresClientApiIdempotencyJournal,
    PostgresExecutionSessionStore,
    PostgresExecutionTaskStore,
    PostgresProviderAttemptStore,
)
from orqetia.identity import (
    ClientAccessCredentialService,
    CustomerAuthorizationService,
    CustomerAuthzAuditEvent,
    PostgresClientCredentialStore,
    PostgresCustomerIdentityRepository,
)
from orqetia.identity.customer_web_session_postgres import (
    PostgresCustomerPortalWebSessionStore,
)
from orqetia.identity.customer_web_sessions import (
    CustomerPortalWebSessionService,
)
from orqetia.infrastructure.artifacts import PostgresClientArtifactStore
from orqetia.infrastructure.http.execution_runtime import (
    ClientExecutionRuntime,
)
from orqetia.infrastructure.messaging.postgres import PostgresWorkQueue
from orqetia.read_models import (
    ClientUsageReportService,
    PostgresReportRollupStore,
)
from orqetia.tenancy import (
    PostgresTenantClientRepository,
    TenancyAdminService,
    TenancyAuditEvent,
)

from .app import CustomerPortalOidcBroker, create_customer_portal_app

SessionFactory = async_sessionmaker[AsyncSession]


class _NoopTenancyAuditSink:
    async def record(self, _event: TenancyAuditEvent) -> None:
        return None


class _CustomerAuthzActivityAuditSink:
    """Persist owner-scoped authz decisions without creating a second audit model."""

    def __init__(self, activity: PostgresCustomerActivityStore) -> None:
        self._activity = activity

    async def record(self, event: CustomerAuthzAuditEvent) -> None:
        if (
            event.membership_id is None
            or event.tenant_id is None
            or event.client_id is None
        ):
            return
        await self._activity.record(
            CustomerActivityEvent(
                event_id=uuid7(),
                tenant_id=event.tenant_id,
                client_id=event.client_id,
                identity_id=event.identity_id,
                membership_id=event.membership_id,
                action=f"AUTHZ_{event.action}",
                result=event.result,
                occurred_at=event.occurred_at,
                resource_type="customer_membership",
                resource_id=str(event.membership_id),
            )
        )


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


def build_customer_portal_app(
    *,
    session_factory: SessionFactory,
    oidc: CustomerPortalOidcBroker,
    allowed_origin: str,
    client_openapi_document: dict[str, object],
    estimation: EstimateService | None = None,
    enable_hsts: bool = False,
) -> FastAPI:
    """Compose PostgreSQL-owned Portal services around explicit external ports."""

    activity = PostgresCustomerActivityStore(session_factory)
    tenancy = TenancyAdminService(
        repository=PostgresTenantClientRepository(session_factory),
        audit=_NoopTenancyAuditSink(),
    )
    authorization = CustomerAuthorizationService(
        repository=PostgresCustomerIdentityRepository(session_factory),
        owner_resolver=tenancy,
        audit=_CustomerAuthzActivityAuditSink(activity),
    )
    sessions = CustomerPortalWebSessionService(
        store=PostgresCustomerPortalWebSessionStore(session_factory),
        authorization=authorization,
    )
    artifacts = PostgresClientArtifactStore(session_factory)
    execution = ClientExecutionRuntime(
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
    )
    credentials = ClientAccessCredentialService(
        PostgresClientCredentialStore(session_factory)
    )
    usage = ClientUsageReportService(
        PostgresReportRollupStore(session_factory)
    )
    return create_customer_portal_app(
        oidc=oidc,
        authorization=authorization,
        sessions=sessions,
        allowed_origin=allowed_origin,
        enable_hsts=enable_hsts,
        execution=execution,
        credentials=credentials,
        usage=usage,
        estimation=estimation,
        activity=activity,
        client_openapi_document=client_openapi_document,
    )
