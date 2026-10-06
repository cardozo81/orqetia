from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from uuid import uuid7

import pytest

from orqetia.estimation import (
    BenchmarkBuilder,
    BenchmarkFeatureKey,
    BenchmarkPolicy,
    BenchmarkSample,
    BenchmarkUnavailableReason,
    GLOBAL_PUBLIC_ALLOWED_FEATURE_DIMENSIONS,
    GLOBAL_PUBLIC_MINIMUM_CLIENTS,
    GLOBAL_PUBLIC_MINIMUM_COHORT_BUCKET,
    GLOBAL_PUBLIC_MINIMUM_SAMPLE_BUCKET,
    GLOBAL_PUBLIC_MINIMUM_SAMPLES,
    GLOBAL_PUBLIC_PRIVACY_CONTRACT_VERSION,
    GLOBAL_PUBLIC_PROHIBITED_SAMPLE_FIELDS,
    GLOBAL_PUBLIC_PURPOSE,
    ReferenceScope,
)

NOW = datetime(2026, 10, 6, 21, tzinfo=UTC)
KEY = BenchmarkFeatureKey(
    provider_id="provider-a",
    model_id="model-a",
    reasoning_profile="standard",
    input_size_bucket="1k-4k",
    output_class="structured",
    schema_class="object",
)


def _sample(
    tenant_id,
    client_id,
    *,
    eligible: bool = True,
) -> BenchmarkSample:
    return BenchmarkSample(
        tenant_id=tenant_id,
        client_id=client_id,
        feature_key=KEY,
        input_tokens=60,
        output_tokens=30,
        cached_input_tokens=10,
        reasoning_tokens=5,
        total_tokens=90,
        occurred_at=NOW,
        global_eligible=eligible,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("minimum_global_samples", GLOBAL_PUBLIC_MINIMUM_SAMPLES - 1),
        ("minimum_global_clients", GLOBAL_PUBLIC_MINIMUM_CLIENTS - 1),
        ("public_count_bucket", GLOBAL_PUBLIC_MINIMUM_SAMPLE_BUCKET - 1),
        ("public_cohort_bucket", GLOBAL_PUBLIC_MINIMUM_COHORT_BUCKET - 1),
    ),
)
def test_global_public_privacy_floors_cannot_be_weakened(
    field: str,
    value: int,
) -> None:
    kwargs = {"methodology_version": "privacy-v1", field: value}
    with pytest.raises(
        ValueError,
        match=GLOBAL_PUBLIC_PRIVACY_CONTRACT_VERSION,
    ):
        BenchmarkPolicy(**kwargs)


def test_stronger_global_public_policy_is_allowed() -> None:
    policy = BenchmarkPolicy(
        methodology_version="privacy-v1",
        minimum_global_samples=60,
        minimum_global_clients=10,
        public_count_bucket=20,
        public_cohort_bucket=10,
    )
    assert policy.minimum_global_samples == 60
    assert policy.minimum_global_clients == 10


def test_global_public_contract_has_fixed_purpose_and_dimensions() -> None:
    assert GLOBAL_PUBLIC_PRIVACY_CONTRACT_VERSION == "global-public-v1"
    assert GLOBAL_PUBLIC_PURPOSE == "technical_token_estimation"
    assert {item.name for item in fields(BenchmarkFeatureKey)} == set(
        GLOBAL_PUBLIC_ALLOWED_FEATURE_DIMENSIONS
    )

    sample_fields = {item.name for item in fields(BenchmarkSample)}
    assert sample_fields.isdisjoint(GLOBAL_PUBLIC_PROHIBITED_SAMPLE_FIELDS)
    assert {
        "tenant_id",
        "client_id",
        "feature_key",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "total_tokens",
        "occurred_at",
        "global_eligible",
    } == sample_fields


def test_global_public_snapshot_strips_owner_and_exposes_bucketed_counts() -> None:
    samples: list[BenchmarkSample] = []
    for _ in range(7):
        tenant_id, client_id = uuid7(), uuid7()
        samples.extend(_sample(tenant_id, client_id) for _ in range(7))

    result = BenchmarkBuilder(BenchmarkPolicy("privacy-v1")).build(
        scope=ReferenceScope.GLOBAL_PUBLIC,
        samples=tuple(samples),
        feature_key=KEY,
        as_of=NOW,
    )
    assert result.snapshot is not None
    snapshot = result.snapshot
    assert snapshot.tenant_id is None
    assert snapshot.client_id is None
    assert snapshot.sample_count == 49
    assert snapshot.distinct_client_count == 7

    metadata = snapshot.client_metadata()
    assert metadata["sample_size"] == 40
    assert metadata["cohort_size"] == 5
    assert "tenant_id" not in metadata
    assert "client_id" not in metadata
    assert "distinct_client_count" not in metadata


def test_opt_out_rebuild_fails_closed_when_cohort_floor_is_lost() -> None:
    clients = [(uuid7(), uuid7()) for _ in range(5)]
    initial = tuple(
        _sample(tenant_id, client_id)
        for tenant_id, client_id in clients
        for _ in range(10)
    )
    builder = BenchmarkBuilder(BenchmarkPolicy("privacy-v1"))
    before = builder.build(
        scope=ReferenceScope.GLOBAL_PUBLIC,
        samples=initial,
        feature_key=KEY,
        as_of=NOW,
    )
    assert before.snapshot is not None

    opted_out_client = clients[-1]
    rebuilt_input = tuple(
        _sample(
            tenant_id,
            client_id,
            eligible=(tenant_id, client_id) != opted_out_client,
        )
        for tenant_id, client_id in clients
        for _ in range(10)
    )
    after = builder.build(
        scope=ReferenceScope.GLOBAL_PUBLIC,
        samples=rebuilt_input,
        feature_key=KEY,
        as_of=NOW,
    )

    assert after.snapshot is None
    assert after.reason is BenchmarkUnavailableReason.INSUFFICIENT_GLOBAL_COHORT
    assert after.fallback_available is None


def test_global_public_rejects_owner_filter_even_with_valid_cohort() -> None:
    with pytest.raises(
        ValueError,
        match="does not accept tenant/client filters",
    ):
        BenchmarkBuilder(BenchmarkPolicy("privacy-v1")).build(
            scope=ReferenceScope.GLOBAL_PUBLIC,
            samples=(),
            feature_key=KEY,
            as_of=NOW,
            tenant_id=uuid7(),
            client_id=uuid7(),
        )
