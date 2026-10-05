from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from orqetia.estimation import (
    BenchmarkBuilder,
    BenchmarkFeatureKey,
    BenchmarkPolicy,
    BenchmarkSample,
    BenchmarkUnavailableReason,
    ReferenceScope,
)

NOW = datetime(2026, 10, 5, 20, tzinfo=UTC)
KEY = BenchmarkFeatureKey(
    provider_id="openai",
    model_id="gpt-x",
    reasoning_profile="medium",
    input_size_bucket="1k-4k",
    output_class="structured",
    schema_class="object",
)


def _sample(
    tenant_id,
    client_id,
    *,
    total: int = 100,
    occurred_at: datetime = NOW,
) -> BenchmarkSample:
    return BenchmarkSample(
        tenant_id=tenant_id,
        client_id=client_id,
        feature_key=KEY,
        input_tokens=60,
        output_tokens=30,
        cached_input_tokens=10,
        reasoning_tokens=10,
        total_tokens=total,
        occurred_at=occurred_at,
    )


def test_client_only_never_consumes_other_client_history() -> None:
    owner_tenant, owner_client = uuid7(), uuid7()
    other_tenant, other_client = uuid7(), uuid7()
    samples = tuple(
        [_sample(owner_tenant, owner_client) for _ in range(10)]
        + [_sample(other_tenant, other_client, total=9999) for _ in range(50)]
    )
    result = BenchmarkBuilder(BenchmarkPolicy("v1")).build(
        scope=ReferenceScope.CLIENT_ONLY,
        samples=samples,
        feature_key=KEY,
        as_of=NOW,
        tenant_id=owner_tenant,
        client_id=owner_client,
    )
    assert result.available
    assert result.snapshot is not None
    assert result.snapshot.metrics.median_total_tokens == 100
    assert result.snapshot.sample_count == 10


def test_client_cold_start_does_not_silently_fallback_global() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    samples = tuple(_sample(tenant_id, client_id) for _ in range(3))
    result = BenchmarkBuilder(BenchmarkPolicy("v1")).build(
        scope=ReferenceScope.CLIENT_ONLY,
        samples=samples,
        feature_key=KEY,
        as_of=NOW,
        tenant_id=tenant_id,
        client_id=client_id,
    )
    assert not result.available
    assert result.snapshot is None
    assert result.reason is BenchmarkUnavailableReason.INSUFFICIENT_CLIENT_HISTORY
    assert result.fallback_available is ReferenceScope.GLOBAL_PUBLIC
    assert "no_implicit_cross_scope_fallback" in result.limitations


def test_global_requires_distinct_client_cohort_not_just_sample_volume() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    samples = tuple(_sample(tenant_id, client_id) for _ in range(100))
    result = BenchmarkBuilder(BenchmarkPolicy("v1")).build(
        scope=ReferenceScope.GLOBAL_PUBLIC,
        samples=samples,
        feature_key=KEY,
        as_of=NOW,
    )
    assert not result.available
    assert result.reason is BenchmarkUnavailableReason.INSUFFICIENT_GLOBAL_COHORT


def test_global_snapshot_strips_owner_and_buckets_public_counts() -> None:
    samples = []
    for _ in range(7):
        tenant_id, client_id = uuid7(), uuid7()
        samples.extend(_sample(tenant_id, client_id) for _ in range(7))
    result = BenchmarkBuilder(BenchmarkPolicy("v1")).build(
        scope=ReferenceScope.GLOBAL_PUBLIC,
        samples=tuple(samples),
        feature_key=KEY,
        as_of=NOW,
    )
    assert result.available
    snapshot = result.snapshot
    assert snapshot is not None
    assert snapshot.tenant_id is None
    assert snapshot.client_id is None
    assert snapshot.sample_count == 49
    assert snapshot.public_sample_size == 40
    assert snapshot.distinct_client_count == 7
    assert snapshot.public_cohort_size == 0
    metadata = snapshot.client_metadata()
    assert metadata["sample_size"] == 40
    assert metadata["cohort_size"] == 0


def test_global_rejects_owner_filter_to_prevent_cohort_narrowing() -> None:
    with pytest.raises(ValueError, match="does not accept tenant/client filters"):
        BenchmarkBuilder(BenchmarkPolicy("v1")).build(
            scope=ReferenceScope.GLOBAL_PUBLIC,
            samples=(),
            feature_key=KEY,
            as_of=NOW,
            tenant_id=uuid7(),
        )


def test_expired_samples_are_excluded_and_raw_content_is_unrepresentable() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    samples = tuple(
        _sample(
            tenant_id,
            client_id,
            occurred_at=NOW - timedelta(days=91),
        )
        for _ in range(20)
    )
    result = BenchmarkBuilder(BenchmarkPolicy("v1")).build(
        scope=ReferenceScope.CLIENT_ONLY,
        samples=samples,
        feature_key=KEY,
        as_of=NOW,
        tenant_id=tenant_id,
        client_id=client_id,
    )
    assert not result.available
    fields = asdict(_sample(tenant_id, client_id))
    assert not {"prompt", "input", "output", "content"} & set(fields)


def test_drift_is_versioned_comparison_on_same_cohort_shape() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    builder = BenchmarkBuilder(BenchmarkPolicy("v1", minimum_client_samples=2))
    previous = builder.build(
        scope=ReferenceScope.CLIENT_ONLY,
        samples=(
            _sample(tenant_id, client_id, total=100),
            _sample(tenant_id, client_id, total=100),
        ),
        feature_key=KEY,
        as_of=NOW,
        tenant_id=tenant_id,
        client_id=client_id,
    ).snapshot
    current = builder.build(
        scope=ReferenceScope.CLIENT_ONLY,
        samples=(
            _sample(tenant_id, client_id, total=150),
            _sample(tenant_id, client_id, total=150),
        ),
        feature_key=KEY,
        as_of=NOW,
        tenant_id=tenant_id,
        client_id=client_id,
    ).snapshot
    assert previous is not None and current is not None
    assert builder.drift_score(previous=previous, current=current) == pytest.approx(0.5)
