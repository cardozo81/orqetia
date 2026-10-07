"""LOCAL/TEST wiring of canonical reporting and provider-free estimates.

Only simulator facts enter this projection. It is not a production rollup job.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.control_plane import (
    PostgresExecutionPolicyRepository,
    PostgresProviderCatalogRepository,
    PostgresProviderPricingCatalogRepository,
)
from orqetia.estimation import (
    BenchmarkBuilder,
    BenchmarkBuildResult,
    BenchmarkFeatureKey,
    BenchmarkPolicy,
    BenchmarkSample,
    EstimateEngine,
    EstimateSubject,
    EstimateTarget,
    ReferenceScope,
)
from orqetia.execution import ProviderAttempt, ProviderAttemptStatus, rank_auto_candidates
from orqetia.infrastructure.processes.orchestration_candidates import (
    ControlPlaneOrchestrationCandidateResolver,
)
from orqetia.providers import ORQETIA_TEST_PROVIDER, RegistryEligibilityMode
from orqetia.read_models import PostgresReportRollupStore
from orqetia.read_models.reporting import ReportQuery, ReportRollup
from orqetia.usage_accounting.postgres import PostgresAccountingLedger

SessionFactory = async_sessionmaker[AsyncSession]


def _guard(environment: str) -> None:
    if environment not in {"local", "test"}:
        raise RuntimeError("local reporting is restricted to LOCAL/TEST")


class LocalAttemptReportingObserver:
    def __init__(self, sessions: SessionFactory, *, environment: str) -> None:
        _guard(environment)
        self._ledger = PostgresAccountingLedger(sessions)
        self._reports = PostgresReportRollupStore(sessions)

    async def record(self, attempt: ProviderAttempt) -> None:
        if attempt.target.provider_id != ORQETIA_TEST_PROVIDER:
            return
        if attempt.status is not ProviderAttemptStatus.COMPLETED:
            return
        entry = await self._ledger.get(attempt_id=attempt.attempt_id)
        if entry is None or attempt.terminal_at is None:
            raise RuntimeError("local reporting requires committed accounting")
        usage = entry.usage
        end = attempt.terminal_at
        await self._reports.put(
            ReportRollup(
                rollup_id=entry.entry_id,
                period_start=end - timedelta(microseconds=1),
                period_end=end,
                as_of=end,
                tenant_id=attempt.ownership.tenant_id,
                client_id=attempt.ownership.client_id,
                provider_id=attempt.target.provider_id,
                model_id=attempt.target.model_id,
                status=attempt.status.value,
                attempts=1,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cached_input_tokens=usage.cached_input_tokens or 0,
                reasoning_tokens=usage.reasoning_tokens or 0,
                total_tokens=usage.total_tokens or 0,
                native_usage=usage.native,
                provider_account_id=attempt.provider_account_id,
                provider_credential_id=attempt.provider_credential_id,
                session_id=attempt.session_id,
                task_id=attempt.task_id,
                attempt_id=attempt.attempt_id,
                estimated_cost=entry.estimated_cost.amount,
                estimated_currency=entry.estimated_cost.currency,
            )
        )


class LocalEstimatePorts:
    """Bounded simulator-only estimates using actual owner-scoped usage samples."""

    def __init__(self, sessions: SessionFactory, *, environment: str) -> None:
        _guard(environment)
        self._policies = PostgresExecutionPolicyRepository(sessions)
        self._candidates = ControlPlaneOrchestrationCandidateResolver(
            catalogs=PostgresProviderCatalogRepository(sessions),
            pricing=PostgresProviderPricingCatalogRepository(sessions),
        )
        self._reports = PostgresReportRollupStore(sessions)

    async def ranked_auto_targets(
        self,
        *,
        subject: EstimateSubject,
        operation: str,
    ) -> tuple[EstimateTarget, ...]:
        del operation
        policy = await self._policies.get_effective(
            tenant_id=UUID(subject.tenant_id),
            client_id=UUID(subject.client_id),
        )
        if policy is None:
            return ()
        eligible = await self._candidates.resolve_authorized(
            mode=RegistryEligibilityMode.AUTO,
            authorized_targets=policy.version.session_policy_snapshot().authorized_targets,
            occurred_at=datetime.now(UTC),
        )
        return tuple(
            EstimateTarget(
                item.target.provider_id, item.target.model_id, item.target.reasoning_profile
            )
            for item in rank_auto_candidates(eligible)
            if item.target.provider_id == ORQETIA_TEST_PROVIDER
        )

    async def is_authorized(
        self,
        *,
        subject: EstimateSubject,
        operation: str,
        target: EstimateTarget,
    ) -> bool:
        return target in await self.ranked_auto_targets(subject=subject, operation=operation)

    async def lookup(
        self,
        *,
        subject: EstimateSubject,
        target: EstimateTarget,
        scope: ReferenceScope,
    ) -> BenchmarkBuildResult:
        tenant_id, client_id = UUID(subject.tenant_id), UUID(subject.client_id)
        now = datetime.now(UTC)
        key = BenchmarkFeatureKey(
            target.provider_id,
            target.model_id,
            target.reasoning_profile,
            "generic",
            "generic",
            "generic",
        )
        # GLOBAL_PUBLIC is never manufactured from this single-owner fixture.
        rows = (
            ()
            if scope is ReferenceScope.GLOBAL_PUBLIC
            else await self._reports.query(
                filters=ReportQuery(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    provider_id=target.provider_id,
                    model_id=target.model_id,
                    period_from=now - timedelta(days=1),
                    period_to=now,
                ),
                limit=1000,
            )
        )
        samples = tuple(
            BenchmarkSample(
                tenant_id=tenant_id,
                client_id=client_id,
                feature_key=key,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cached_input_tokens=row.cached_input_tokens,
                reasoning_tokens=row.reasoning_tokens,
                total_tokens=row.total_tokens,
                occurred_at=row.as_of,
            )
            for row in rows
        )
        return BenchmarkBuilder(BenchmarkPolicy(methodology_version="local-simulator-v1")).build(
            scope=scope,
            samples=samples,
            feature_key=key,
            as_of=now,
            tenant_id=tenant_id if scope is ReferenceScope.CLIENT_ONLY else None,
            client_id=client_id if scope is ReferenceScope.CLIENT_ONLY else None,
        )


def build_local_estimates(sessions: SessionFactory, *, environment: str) -> EstimateEngine:
    ports = LocalEstimatePorts(sessions, environment=environment)
    return EstimateEngine(targets=ports, benchmarks=ports)
