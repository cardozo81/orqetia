"""Production worker handler composition for the execution queue."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.control_plane import (
    PostgresExecutionPolicyRepository,
    PostgresExternalCapacityRepository,
    PostgresProviderAccountRepository,
    PostgresProviderCatalogRepository,
    PostgresProviderCredentialRepository,
    PostgresProviderPricingCatalogRepository,
    PostgresQuotaPolicyRepository,
    ProviderAccountService,
    ProviderSecretStore,
)
from orqetia.execution import (
    PostgresExecutionSessionStore,
    PostgresExecutionTaskStore,
    PostgresProviderAttemptStore,
)
from orqetia.infrastructure.artifacts import PostgresClientArtifactStore
from orqetia.infrastructure.messaging import PostgresWorkQueue
from orqetia.usage_accounting.postgres import PostgresAccountingLedger
from orqetia.usage_accounting.quota_postgres import PostgresQuotaEnforcer

from .attempt_accounting import (
    AttemptAccountingObserver,
    CompositeCompletedAttemptObserver,
)
from .orchestration_candidates import ControlPlaneOrchestrationCandidateResolver
from .provider_attempts import (
    PROVIDER_ATTEMPT_OPERATION,
    PROVIDER_ATTEMPT_OPERATION_VERSION,
    ProviderAttemptHandler,
)
from .runtime_adapters import DurableProviderAdapterResolver
from .runtime_quotas import AttemptQuotaCoordinator
from .task_orchestration import (
    TASK_ORCHESTRATION_OPERATION,
    TASK_ORCHESTRATION_OPERATION_VERSION,
    TaskOrchestrationHandler,
)
from .worker import HandlerRegistry

SessionFactory = async_sessionmaker[AsyncSession]


class UnavailableProviderSecretStore:
    """Fail-closed placeholder used until a deployable secret backend is injected."""

    async def put(self, secret):
        del secret
        raise RuntimeError("provider secret store is not configured")

    async def get(self, reference):
        del reference
        raise LookupError("provider secret store is not configured")

    async def delete(self, reference):
        del reference
        raise RuntimeError("provider secret store is not configured")


def build_execution_handler_registry(
    *,
    session_factory: SessionFactory,
    queue: PostgresWorkQueue,
    secret_store: ProviderSecretStore | None = None,
) -> HandlerRegistry:
    sessions = PostgresExecutionSessionStore(session_factory)
    tasks = PostgresExecutionTaskStore(session_factory)
    attempts = PostgresProviderAttemptStore(session_factory)
    policies = PostgresExecutionPolicyRepository(session_factory)
    catalogs = PostgresProviderCatalogRepository(session_factory)
    pricing = PostgresProviderPricingCatalogRepository(session_factory)
    account_service = ProviderAccountService(
        accounts=PostgresProviderAccountRepository(session_factory),
        credentials=PostgresProviderCredentialRepository(session_factory),
        capacity=PostgresExternalCapacityRepository(session_factory),
    )
    credential_repository = PostgresProviderCredentialRepository(session_factory)
    artifacts = PostgresClientArtifactStore(session_factory)
    accounting = AttemptAccountingObserver(
        ledger=PostgresAccountingLedger(session_factory),
        pricing_versions=pricing,
    )
    attempt_quotas = AttemptQuotaCoordinator(
        policies=PostgresQuotaPolicyRepository(session_factory),
        quotas=PostgresQuotaEnforcer(session_factory),
    )
    adapter_resolver = DurableProviderAdapterResolver(
        credentials=credential_repository,
        secrets=secret_store or UnavailableProviderSecretStore(),
        artifacts=artifacts,
    )

    task_handler = TaskOrchestrationHandler(
        sessions=sessions,
        tasks=tasks,
        attempts=attempts,
        policies=policies,
        candidates=ControlPlaneOrchestrationCandidateResolver(
            catalogs=catalogs,
            pricing=pricing,
        ),
        credentials=account_service,
        work_queue=queue,
        pricing_catalogs=pricing,
    )
    attempt_handler = ProviderAttemptHandler(
        store=attempts,
        resolve_attempt_adapter=adapter_resolver,
        continuation_queue=queue,
        completed_observer=CompositeCompletedAttemptObserver(
            attempt_quotas,
            accounting,
        ),
        pre_dispatch_gate=attempt_quotas,
    )

    registry = HandlerRegistry()
    registry.register(
        TASK_ORCHESTRATION_OPERATION,
        TASK_ORCHESTRATION_OPERATION_VERSION,
        task_handler,
    )
    registry.register(
        PROVIDER_ATTEMPT_OPERATION,
        PROVIDER_ATTEMPT_OPERATION_VERSION,
        attempt_handler,
    )
    return registry
