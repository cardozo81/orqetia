from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid7

import pytest

from orqetia.estimation import (
    BenchmarkBuildResult,
    BenchmarkConfidence,
    BenchmarkFeatureKey,
    BenchmarkMetrics,
    BenchmarkSnapshot,
    CanonicalEstimateTargetResolver,
    EstimateEngine,
    EstimateExecutionMode,
    EstimateForbidden,
    EstimateSpec,
    EstimateSubject,
    EstimateTarget,
    ReferenceScope,
)
from orqetia.execution import ExecutionTargetSnapshot, OrchestrationCandidate

NOW = datetime(2026, 10, 5, 20, tzinfo=UTC)
TENANT_ID = UUID("0199b39a-9bf1-7000-8000-000000000010")
CLIENT_ID = UUID("0199b39a-9bf1-7000-8000-000000000011")
SUBJECT = EstimateSubject(str(TENANT_ID), str(CLIENT_ID))
TARGET_A = EstimateTarget("openai", "gpt-a", "medium")
TARGET_B = EstimateTarget("anthropic", "claude-b", "standard")


def _snapshot(
    target: EstimateTarget,
    scope: ReferenceScope,
    *,
    tenant_id: UUID | None = TENANT_ID,
    client_id: UUID | None = CLIENT_ID,
) -> BenchmarkSnapshot:
    return BenchmarkSnapshot(
        snapshot_id=uuid7(),
        scope=scope,
        feature_key=BenchmarkFeatureKey(
            provider_id=target.provider_id,
            model_id=target.model_id,
            reasoning_profile=target.reasoning_profile,
            input_size_bucket="generic",
            output_class="generic",
            schema_class="generic",
        ),
        methodology_version="v1",
        benchmark_version=f"v1:{target.provider_id}",
        as_of=NOW,
        sample_count=40,
        public_sample_size=40,
        distinct_client_count=5,
        public_cohort_size=5,
        confidence=BenchmarkConfidence.MEDIUM,
        metrics=BenchmarkMetrics(
            median_input_tokens=Decimal("50"),
            median_output_tokens=Decimal("30"),
            median_cached_input_tokens=Decimal("10"),
            median_reasoning_tokens=Decimal("5"),
            median_total_tokens=Decimal("80"),
            p90_output_tokens=50,
            p90_total_tokens=120,
        ),
        tenant_id=tenant_id if scope is ReferenceScope.CLIENT_ONLY else None,
        client_id=client_id if scope is ReferenceScope.CLIENT_ONLY else None,
    )


def _candidate(target: EstimateTarget, cost: str, policy_rank: int) -> OrchestrationCandidate:
    return OrchestrationCandidate(
        target=ExecutionTargetSnapshot(
            target.provider_id,
            target.model_id,
            target.reasoning_profile,
        ),
        comparison_group="CURRENCY:USD",
        comparison_group_rank=0,
        estimated_cost=Decimal(cost),
        policy_rank=policy_rank,
        currency="USD",
        pricing_reference=f"{target.provider_id}:pricing-v1",
    )


class FakeBenchmarks:
    def __init__(
        self,
        *,
        owner_tenant: UUID = TENANT_ID,
        owner_client: UUID = CLIENT_ID,
    ) -> None:
        self.owner_tenant = owner_tenant
        self.owner_client = owner_client
        self.lookups: list[EstimateTarget] = []

    async def lookup(self, *, subject, target, scope):
        assert subject == SUBJECT
        self.lookups.append(target)
        return BenchmarkBuildResult(
            requested_scope=scope,
            snapshot=_snapshot(
                target,
                scope,
                tenant_id=self.owner_tenant,
                client_id=self.owner_client,
            ),
        )


def _resolver() -> CanonicalEstimateTargetResolver:
    return CanonicalEstimateTargetResolver(
        {
            (SUBJECT.tenant_id, SUBJECT.client_id): (
                _candidate(TARGET_A, "9", 0),
                _candidate(TARGET_B, "1", 1),
            )
        }
    )


@pytest.mark.asyncio
async def test_auto_uses_canonical_cost_rank_without_provider_call() -> None:
    benchmarks = FakeBenchmarks()
    engine = EstimateEngine(
        targets=_resolver(),
        benchmarks=benchmarks,
    )
    result = await engine.estimate(
        subject=SUBJECT,
        spec=EstimateSpec(
            operation="TASK_EXECUTION",
            input_payload={"message": "hello"},
            reference_scope=ReferenceScope.GLOBAL_PUBLIC,
        ),
    )
    assert result.requested_execution_mode is EstimateExecutionMode.AUTO
    assert result.effective_target == TARGET_B
    assert benchmarks.lookups == [TARGET_B]
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens == 30
    assert result.usage.total_tokens == result.usage.input_tokens + 30
    forbidden = {"cost", "currency", "price", "pricing", "charge", "credit"}
    assert not any(
        token in key.lower()
        for key in result.client_payload()
        for token in forbidden
    )


@pytest.mark.asyncio
async def test_explicit_target_is_fixed_even_when_more_expensive() -> None:
    engine = EstimateEngine(
        targets=_resolver(),
        benchmarks=FakeBenchmarks(),
    )
    result = await engine.estimate(
        subject=SUBJECT,
        spec=EstimateSpec(
            operation="TASK_EXECUTION",
            input_payload={"message": "hello"},
            reference_scope=ReferenceScope.CLIENT_ONLY,
            execution_mode=EstimateExecutionMode.EXPLICIT_TARGET,
            target=TARGET_A,
        ),
    )
    assert result.effective_target == TARGET_A
    assert result.effective_execution_mode is EstimateExecutionMode.EXPLICIT_TARGET


@pytest.mark.asyncio
async def test_explicit_target_outside_authorized_envelope_fails_closed() -> None:
    engine = EstimateEngine(
        targets=_resolver(),
        benchmarks=FakeBenchmarks(),
    )
    with pytest.raises(EstimateForbidden):
        await engine.estimate(
            subject=SUBJECT,
            spec=EstimateSpec(
                operation="TASK_EXECUTION",
                input_payload={},
                reference_scope=ReferenceScope.CLIENT_ONLY,
                execution_mode=EstimateExecutionMode.EXPLICIT_TARGET,
                target=EstimateTarget("unknown", "unknown", "unknown"),
            ),
        )


@pytest.mark.asyncio
async def test_client_only_benchmark_owner_mismatch_fails_closed() -> None:
    engine = EstimateEngine(
        targets=_resolver(),
        benchmarks=FakeBenchmarks(owner_client=uuid7()),
    )
    with pytest.raises(EstimateForbidden, match="ownership mismatch"):
        await engine.estimate(
            subject=SUBJECT,
            spec=EstimateSpec(
                operation="TASK_EXECUTION",
                input_payload={},
                reference_scope=ReferenceScope.CLIENT_ONLY,
                execution_mode=EstimateExecutionMode.EXPLICIT_TARGET,
                target=TARGET_B,
            ),
        )


@pytest.mark.asyncio
async def test_global_public_snapshot_has_no_owner_dependency() -> None:
    engine = EstimateEngine(
        targets=_resolver(),
        benchmarks=FakeBenchmarks(owner_tenant=uuid7(), owner_client=uuid7()),
    )
    result = await engine.estimate(
        subject=SUBJECT,
        spec=EstimateSpec(
            operation="TASK_EXECUTION",
            input_payload={"message": "hello"},
            reference_scope=ReferenceScope.GLOBAL_PUBLIC,
        ),
    )
    assert result.reference_scope is ReferenceScope.GLOBAL_PUBLIC
    assert result.sample_size == 40
    assert result.cohort_size == 5
