from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid7

import pytest

from orqetia.estimation import (
    BenchmarkBuildResult,
    BenchmarkConfidence,
    BenchmarkFeatureKey,
    BenchmarkMetrics,
    BenchmarkSnapshot,
    EstimateEngine,
    EstimateExecutionMode,
    EstimateForbidden,
    EstimateSpec,
    EstimateSubject,
    EstimateTarget,
    ReferenceScope,
)

NOW = datetime(2026, 10, 5, 20, tzinfo=UTC)
SUBJECT = EstimateSubject("tenant-1", "client-1")
TARGET_A = EstimateTarget("openai", "gpt-a", "medium")
TARGET_B = EstimateTarget("anthropic", "claude-b", "standard")


def _snapshot(target: EstimateTarget, scope: ReferenceScope) -> BenchmarkSnapshot:
    tenant_id = uuid7() if scope is ReferenceScope.CLIENT_ONLY else None
    client_id = uuid7() if scope is ReferenceScope.CLIENT_ONLY else None
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
        tenant_id=tenant_id,
        client_id=client_id,
    )


class FakeTargets:
    async def ranked_auto_targets(
        self,
        *,
        subject: EstimateSubject,
        operation: str,
    ) -> tuple[EstimateTarget, ...]:
        assert subject == SUBJECT
        assert operation == "TASK_EXECUTION"
        return (TARGET_A, TARGET_B)

    async def is_authorized(
        self,
        *,
        subject: EstimateSubject,
        operation: str,
        target: EstimateTarget,
    ) -> bool:
        assert subject == SUBJECT
        assert operation == "TASK_EXECUTION"
        return target == TARGET_B


class FakeBenchmarks:
    def __init__(self, available_target: EstimateTarget) -> None:
        self.available_target = available_target

    async def lookup(
        self,
        *,
        subject: EstimateSubject,
        target: EstimateTarget,
        scope: ReferenceScope,
    ) -> BenchmarkBuildResult:
        assert subject == SUBJECT
        if target != self.available_target:
            return BenchmarkBuildResult(
                requested_scope=scope,
                snapshot=None,
                limitations=("synthetic_missing",),
            )
        return BenchmarkBuildResult(
            requested_scope=scope,
            snapshot=_snapshot(target, scope),
        )


@pytest.mark.asyncio
async def test_auto_uses_ranked_authorized_candidates_without_provider_call() -> None:
    engine = EstimateEngine(
        targets=FakeTargets(),
        benchmarks=FakeBenchmarks(TARGET_B),
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
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens == 30
    assert result.usage.total_tokens == result.usage.input_tokens + 30
    forbidden = {"cost", "currency", "price", "charge", "credit"}
    assert not any(
        token in key.lower()
        for key in result.client_payload()
        for token in forbidden
    )


@pytest.mark.asyncio
async def test_explicit_target_is_fixed_and_authorized() -> None:
    engine = EstimateEngine(
        targets=FakeTargets(),
        benchmarks=FakeBenchmarks(TARGET_B),
    )
    result = await engine.estimate(
        subject=SUBJECT,
        spec=EstimateSpec(
            operation="TASK_EXECUTION",
            input_payload={"message": "hello"},
            reference_scope=ReferenceScope.CLIENT_ONLY,
            execution_mode=EstimateExecutionMode.EXPLICIT_TARGET,
            target=TARGET_B,
        ),
    )
    assert result.effective_target == TARGET_B
    assert result.effective_execution_mode is EstimateExecutionMode.EXPLICIT_TARGET

    with pytest.raises(EstimateForbidden):
        await engine.estimate(
            subject=SUBJECT,
            spec=EstimateSpec(
                operation="TASK_EXECUTION",
                input_payload={},
                reference_scope=ReferenceScope.CLIENT_ONLY,
                execution_mode=EstimateExecutionMode.EXPLICIT_TARGET,
                target=TARGET_A,
            ),
        )
