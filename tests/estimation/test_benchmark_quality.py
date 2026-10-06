from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

from orqetia.estimation import (
    BenchmarkCalibrationSample,
    BenchmarkConfidence,
    BenchmarkFeatureKey,
    BenchmarkMetrics,
    BenchmarkQualityGate,
    BenchmarkQualityPolicy,
    BenchmarkRebuildReason,
    BenchmarkSnapshot,
    BenchmarkUnavailableReason,
    ReferenceScope,
)

NOW = datetime(2026, 10, 6, 18, tzinfo=UTC)
KEY = BenchmarkFeatureKey(
    provider_id="provider-a",
    model_id="model-a",
    reasoning_profile="standard",
    input_size_bucket="1k-4k",
    output_class="structured",
    schema_class="object",
)


def _snapshot(
    *,
    scope: ReferenceScope = ReferenceScope.CLIENT_ONLY,
    total: str = "100",
    as_of: datetime = NOW,
    confidence: BenchmarkConfidence = BenchmarkConfidence.MEDIUM,
    methodology: str = "quality-v1",
) -> BenchmarkSnapshot:
    return BenchmarkSnapshot(
        snapshot_id=uuid7(),
        scope=scope,
        feature_key=KEY,
        methodology_version=methodology,
        benchmark_version=f"{methodology}:{uuid7()}",
        as_of=as_of,
        sample_count=40,
        public_sample_size=40,
        distinct_client_count=5,
        public_cohort_size=5,
        confidence=confidence,
        metrics=BenchmarkMetrics(
            median_input_tokens=Decimal("60"),
            median_output_tokens=Decimal("40"),
            median_cached_input_tokens=Decimal("0"),
            median_reasoning_tokens=Decimal("10"),
            median_total_tokens=Decimal(total),
            p90_output_tokens=60,
            p90_total_tokens=140,
        ),
        tenant_id=uuid7() if scope is ReferenceScope.CLIENT_ONLY else None,
        client_id=uuid7() if scope is ReferenceScope.CLIENT_ONLY else None,
    )


def _calibration(
    snapshot: BenchmarkSnapshot,
    *,
    estimated: int = 100,
    observed: int = 100,
    count: int = 20,
) -> tuple[BenchmarkCalibrationSample, ...]:
    return tuple(
        BenchmarkCalibrationSample(
            benchmark_version=snapshot.benchmark_version,
            estimated_total_tokens=estimated,
            observed_total_tokens=observed,
            occurred_at=NOW + timedelta(minutes=index + 1),
        )
        for index in range(count)
    )


def _gate(**overrides) -> BenchmarkQualityGate:
    values = {
        "methodology_version": "quality-v1",
        "minimum_calibration_samples": 20,
        "minimum_serving_confidence": BenchmarkConfidence.MEDIUM,
        "maximum_snapshot_age_days": 7,
        "maximum_median_absolute_error_ratio": Decimal("0.25"),
        "maximum_p90_absolute_error_ratio": Decimal("0.50"),
        "maximum_absolute_bias_ratio": Decimal("0.20"),
        "maximum_drift_ratio": Decimal("0.30"),
    }
    values.update(overrides)
    return BenchmarkQualityGate(BenchmarkQualityPolicy(**values))


def test_fresh_calibrated_snapshot_is_servable() -> None:
    snapshot = _snapshot()
    assessment = _gate().assess(
        snapshot=snapshot,
        assessed_at=NOW + timedelta(hours=1),
        calibration_samples=_calibration(snapshot, estimated=102, observed=100),
    )

    assert assessment.servable
    assert not assessment.rebuild_required
    assert assessment.reasons == ()
    assert assessment.accuracy is not None
    assert assessment.accuracy.median_absolute_error_ratio == Decimal("0.02")
    assert assessment.fallback_available is None


def test_stale_client_snapshot_fails_closed_and_only_advertises_global() -> None:
    snapshot = _snapshot(as_of=NOW - timedelta(days=8))
    guarded = _gate().guard(
        snapshot=snapshot,
        assessed_at=NOW,
        calibration_samples=(),
    )

    assert guarded.snapshot is None
    assert guarded.reason is BenchmarkUnavailableReason.STALE_BENCHMARK
    assert guarded.fallback_available is ReferenceScope.GLOBAL_PUBLIC
    assert "no_implicit_cross_scope_fallback" in guarded.limitations


def test_stale_global_snapshot_has_no_cross_scope_fallback() -> None:
    snapshot = _snapshot(
        scope=ReferenceScope.GLOBAL_PUBLIC,
        as_of=NOW - timedelta(days=8),
    )
    guarded = _gate().guard(snapshot=snapshot, assessed_at=NOW)

    assert guarded.snapshot is None
    assert guarded.reason is BenchmarkUnavailableReason.STALE_BENCHMARK
    assert guarded.fallback_available is None


def test_drift_threshold_requires_rebuild_with_version_provenance() -> None:
    previous = _snapshot(total="100", as_of=NOW - timedelta(days=1))
    current = _snapshot(total="150", as_of=NOW)
    assessment = _gate().assess(
        snapshot=current,
        previous=previous,
        assessed_at=NOW + timedelta(hours=1),
        calibration_samples=_calibration(current),
    )

    assert assessment.rebuild_required
    assert BenchmarkRebuildReason.DRIFT in assessment.reasons
    assert assessment.drift_score == Decimal("0.5")
    assert assessment.previous_benchmark_version == previous.benchmark_version


def test_accuracy_and_bias_thresholds_require_rebuild() -> None:
    snapshot = _snapshot()
    assessment = _gate().assess(
        snapshot=snapshot,
        assessed_at=NOW + timedelta(hours=1),
        calibration_samples=_calibration(
            snapshot,
            estimated=160,
            observed=100,
        ),
    )

    assert assessment.rebuild_required
    assert BenchmarkRebuildReason.ACCURACY in assessment.reasons
    assert BenchmarkRebuildReason.BIAS in assessment.reasons
    assert assessment.accuracy is not None
    assert assessment.accuracy.median_absolute_error_ratio == Decimal("0.6")
    assert assessment.accuracy.bias_ratio == Decimal("0.6")


def test_insufficient_calibration_is_visible_but_does_not_fake_failure() -> None:
    snapshot = _snapshot()
    assessment = _gate().assess(
        snapshot=snapshot,
        assessed_at=NOW + timedelta(hours=1),
        calibration_samples=_calibration(snapshot, count=3),
    )

    assert assessment.servable
    assert assessment.accuracy is None
    assert "insufficient_calibration_samples" in assessment.limitations


def test_calibration_from_other_benchmark_version_is_ignored() -> None:
    snapshot = _snapshot()
    foreign = BenchmarkCalibrationSample(
        benchmark_version="other-version",
        estimated_total_tokens=999,
        observed_total_tokens=1,
        occurred_at=NOW + timedelta(minutes=1),
    )
    assessment = _gate().assess(
        snapshot=snapshot,
        assessed_at=NOW + timedelta(hours=1),
        calibration_samples=(foreign,),
    )

    assert assessment.servable
    assert assessment.accuracy is None
    assert "insufficient_calibration_samples" in assessment.limitations


def test_methodology_and_confidence_are_serving_thresholds() -> None:
    snapshot = _snapshot(
        confidence=BenchmarkConfidence.LOW,
        methodology="old-method",
    )
    assessment = _gate().assess(
        snapshot=snapshot,
        assessed_at=NOW + timedelta(hours=1),
    )

    assert not assessment.servable
    assert BenchmarkRebuildReason.METHODOLOGY in assessment.reasons
    assert BenchmarkRebuildReason.LOW_CONFIDENCE in assessment.reasons
