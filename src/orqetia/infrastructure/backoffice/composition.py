"""PostgreSQL composition for the deployable Backoffice web surface."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.control_plane import (
    ExecutionPolicyAdminService,
    PostgresExecutionPolicyRepository,
    PostgresExternalCapacityRepository,
    PostgresProviderAccountRepository,
    PostgresProviderCatalogRepository,
    PostgresProviderCredentialRepository,
    PostgresProviderPricingCatalogRepository,
    PostgresQuotaPolicyRepository,
    ProviderAccountService,
    ProviderCatalogAdminService,
    ProviderCredentialService,
    ProviderPricingAdminService,
    ProviderSecretStore,
    QuotaPolicyAdminService,
)
from orqetia.identity import (
    BackofficeAuthorizationService,
    BackofficeWebSessionService,
    ClientAccessCredentialService,
    PostgresBackofficeBindingRepository,
    PostgresBackofficeWebSessionStore,
    PostgresClientCredentialStore,
)
from orqetia.infrastructure.local_audit import LocalJsonlAuditSink
from orqetia.read_models import (
    BackofficeReportingService,
    OperationalFinancialIntelligenceService,
    PostgresReportRollupStore,
)
from orqetia.tenancy import PostgresTenantClientRepository, TenancyAdminService

from .admin_console import BackofficeAdminServices
from .app import BackofficeOidcBroker, create_backoffice_app

SessionFactory = async_sessionmaker[AsyncSession]


def build_backoffice_app(
    *,
    session_factory: SessionFactory,
    oidc: BackofficeOidcBroker,
    allowed_origin: str,
    secret_store: ProviderSecretStore,
    environment: str,
    audit_path: Path = Path("/var/lib/orqetia/audit/backoffice.jsonl"),
    enable_hsts: bool = False,
) -> FastAPI:
    """Compose all Backoffice application services over PostgreSQL."""

    audit = LocalJsonlAuditSink(audit_path, environment=environment)

    tenancy_repository = PostgresTenantClientRepository(session_factory)
    tenancy = TenancyAdminService(
        repository=tenancy_repository,
        audit=audit,
    )

    binding_repository = PostgresBackofficeBindingRepository(session_factory)
    authorization = BackofficeAuthorizationService(
        repository=binding_repository,
        audit=audit,
    )
    sessions = BackofficeWebSessionService(
        store=PostgresBackofficeWebSessionStore(session_factory),
        authorization=authorization,
    )

    provider_account_repository = PostgresProviderAccountRepository(session_factory)
    provider_credential_repository = PostgresProviderCredentialRepository(
        session_factory
    )
    rollups = PostgresReportRollupStore(session_factory)

    admin = BackofficeAdminServices(
        tenancy=tenancy,
        tenancy_repository=tenancy_repository,
        bindings=binding_repository,
        execution_policies=ExecutionPolicyAdminService(
            PostgresExecutionPolicyRepository(session_factory),
            owner_resolver=tenancy,
        ),
        quota_policies=QuotaPolicyAdminService(
            repository=PostgresQuotaPolicyRepository(session_factory),
            owner_resolver=tenancy,
        ),
        provider_catalog=ProviderCatalogAdminService(
            repository=PostgresProviderCatalogRepository(session_factory),
            audit=audit,
        ),
        provider_pricing=ProviderPricingAdminService(
            repository=PostgresProviderPricingCatalogRepository(session_factory),
            audit=audit,
        ),
        provider_accounts=ProviderAccountService(
            accounts=provider_account_repository,
            credentials=provider_credential_repository,
            capacity=PostgresExternalCapacityRepository(session_factory),
        ),
        provider_account_repository=provider_account_repository,
        provider_credentials=ProviderCredentialService(
            secrets=secret_store,
            repository=provider_credential_repository,
            audit=audit,
        ),
        provider_credential_repository=provider_credential_repository,
        client_credentials=ClientAccessCredentialService(
            PostgresClientCredentialStore(session_factory)
        ),
        intelligence=OperationalFinancialIntelligenceService(rollups=rollups),
        reporting=BackofficeReportingService(
            store=rollups,
            export_audit=audit,
        ),
    )

    return create_backoffice_app(
        oidc=oidc,
        authorization=authorization,
        sessions=sessions,
        allowed_origin=allowed_origin,
        enable_hsts=enable_hsts,
        admin=admin,
    )
