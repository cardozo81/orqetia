"""Privacy-preserving statistical benchmark contracts for token estimates."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import median
from uuid import UUID, uuid7


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ReferenceScope(StrEnum):
    CLIENT_ONLY = "CLIENT_ONLY"
    GLOBAL_PUBLIC = "GLOBAL_PUBLIC"


class BenchmarkConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class BenchmarkUnavailableReason(StrEnum):
    INSUFFICIENT_CLIENT_HISTORY = "INSUFFICIENT_CLIENT_HISTORY"
    INSUFFICIENT_GLOBAL_SAMPLES = "INSUFFICIENT_GLOBAL_SAMPLES"
    INSUFFICIENT_GLOBAL_COHORT = "INSUFFICIENT_GLOBAL_COHORT"
    STALE_BENCHMARK = "STALE_BENCHMARK"
    DRIFT_THRESHOLD_EXCEEDED = "DRIFT_THRESHOLD_EXCEEDED"
    CALIBRATION_THRESHOLD_EXCEEDED = "CALIBRATION_THRESHOLD_EXCEEDED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    METHODOLOGY_VERSION_MISMATCH = "METHODOLOGY_VERSION_MISMATCH"


@dataclass(frozen=True)
class BenchmarkFeatureKey:
    """Typed, non-content dimensions allowed to partition benchmark cohorts."""

    provider_id: str
    model_id: str
    reasoning_profile: str
    input_size_bucket: str
    output_class: str
    schema_class: str

    def __post_init__(self) -> None:
        for field, value, maximum in (
            ("provider_id", self.provider_id, 100),
            ("model_id", self.model_id, 200),
            ("reasoning_profile", self.reasoning_profile, 100),
            ("input_size_bucket", self.input_size_bucket, 100),
            ("output_class", self.output_class, 100),
            ("schema_class", self.schema_class, 100),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must contain 1..{maximum} characters")


@dataclass(frozen=True)
class BenchmarkSample:
    """Synthetic/rollup input only. Raw prompt/output content has no representation."""

    tenant_id: UUID
    client_id: UUID
    feature_key: BenchmarkFeatureKey
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    total_tokens: int
    occurred_at: datetime
    global_eligible: bool = False

    def __post_init__(self) -> None:
        for field, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cached_input_tokens", self.cached_input_tokens),
            ("reasoning_tokens", self.reasoning_tokens),
            ("total_tokens", self.total_tokens),
        ):
            if value < 0:
                raise ValueError(f"{field} cannot be negative")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        _require_aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True)
class BenchmarkPolicy:
    methodology_version: str
    minimum_client_samples: int = 10
    minimum_global_samples: int = 30
    minimum_global_clients: int = 5
    maximum_sample_age_days: int = 90
    public_count_bucket: int = 10
    public_cohort_bucket: int = 5

    def __post_init__(self) -> None:
        if not self.methodology_version.strip() or len(self.methodology_version) > 100:
            raise ValueError("methodology_version must contain 1..100 characters")
        for field, value in (
            ("minimum_client_samples", self.minimum_client_samples),
            ("minimum_global_samples", self.minimum_global_samples),
            ("minimum_global_clients", self.minimum_global_clients),
            ("maximum_sample_age_days", self.maximum_sample_age_days),
            ("public_count_bucket", self.public_count_bucket),
            ("public_cohort_bucket", self.public_cohort_bucket),
        ):
            if value < 1:
                raise ValueError(f"{field} must be positive")


@dataclass(frozen=True)
class BenchmarkMetrics:
    median_input_tokens: Decimal
    median_output_tokens: Decimal
    median_cached_input_tokens: Decimal
    median_reasoning_tokens: Decimal
    median_total_tokens: Decimal
    p90_output_tokens: int
    p90_total_tokens: int

    def __post_init__(self) -> None:
        for value in (
            self.median_input_tokens,
            self.median_output_tokens,
            self.median_cached_input_tokens,
            self.median_reasoning_tokens,
            self.median_total_tokens,
            Decimal(self.p90_output_tokens),
            Decimal(self.p90_total_tokens),
        ):
            if value < 0:
                raise ValueError("benchmark metrics cannot be negative")


@dataclass(frozen=True)
class BenchmarkSnapshot:
    snapshot_id: UUID
    scope: ReferenceScope
    feature_key: BenchmarkFeatureKey
    methodology_version: str
    benchmark_version: str
    as_of: datetime
    sample_count: int
    public_sample_size: int
    distinct_client_count: int
    public_cohort_size: int
    confidence: BenchmarkConfidence
    metrics: BenchmarkMetrics
    tenant_id: UUID | None = None
    client_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_aware(self.as_of, "as_of")
        if self.sample_count < 1:
            raise ValueError("sample_count must be positive")
        if self.distinct_client_count < 1:
            raise ValueError("distinct_client_count must be positive")
        if self.public_sample_size < 0 or self.public_cohort_size < 0:
            raise ValueError("public cohort counts cannot be negative")
        if self.scope is ReferenceScope.CLIENT_ONLY:
            if self.tenant_id is None or self.client_id is None:
                raise ValueError("CLIENT_ONLY snapshot requires owner")
        elif self.tenant_id is not None or self.client_id is not None:
            raise ValueError("GLOBAL_PUBLIC snapshot must not carry tenant/client identity")

    def client_metadata(self) -> dict[str, object]:
        """Safe estimate metadata; internal exact global counts stay private."""

        return {
            "reference_scope": self.scope.value,
            "methodology_version": self.methodology_version,
            "benchmark_version": self.benchmark_version,
            "as_of": self.as_of,
            "sample_size": (
                self.sample_count
                if self.scope is ReferenceScope.CLIENT_ONLY
                else self.public_sample_size
            ),
            "cohort_size": (
                1
                if self.scope is ReferenceScope.CLIENT_ONLY
                else self.public_cohort_size
            ),
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True)
class BenchmarkBuildResult:
    requested_scope: ReferenceScope
    snapshot: BenchmarkSnapshot | None
    reason: BenchmarkUnavailableReason | None = None
    fallback_available: ReferenceScope | None = None
    limitations: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.snapshot is not None


class BenchmarkRebuildReason(StrEnum):
    STALE = "STALE"
    DRIFT = "DRIFT"
    ACCURACY = "ACCURACY"
    BIAS = "BIAS"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    METHODOLOGY = "METHODOLOGY"


_CONFIDENCE_RANK = {
    BenchmarkConfidence.LOW: 0,
    BenchmarkConfidence.MEDIUM: 1,
    BenchmarkConfidence.HIGH: 2,
}


@dataclass(frozen=True)
class BenchmarkCalibrationSample:
    benchmark_version: str
    estimated_total_tokens: int
    observed_total_tokens: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.benchmark_version.strip() or len(self.benchmark_version) > 200:
            raise ValueError("benchmark_version must contain 1..200 characters")
        if self.estimated_total_tokens < 0 or self.observed_total_tokens < 0:
            raise ValueError("calibration token counts cannot be negative")
        _require_aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True)
class BenchmarkAccuracyMetrics:
    sample_count: int
    median_absolute_error_ratio: Decimal
    p90_absolute_error_ratio: Decimal
    bias_ratio: Decimal

    def __post_init__(self) -> None:
        if self.sample_count < 1:
            raise ValueError("accuracy sample_count must be positive")
        if self.median_absolute_error_ratio < 0:
            raise ValueError("median_absolute_error_ratio cannot be negative")
        if self.p90_absolute_error_ratio < 0:
            raise ValueError("p90_absolute_error_ratio cannot be negative")


@dataclass(frozen=True)
class BenchmarkQualityPolicy:
    methodology_version: str
    minimum_calibration_samples: int = 20
    minimum_serving_confidence: BenchmarkConfidence = BenchmarkConfidence.MEDIUM
    maximum_snapshot_age_days: int = 7
    maximum_median_absolute_error_ratio: Decimal = Decimal("0.25")
    maximum_p90_absolute_error_ratio: Decimal = Decimal("0.50")
    maximum_absolute_bias_ratio: Decimal = Decimal("0.20")
    maximum_drift_ratio: Decimal = Decimal("0.30")

    def __post_init__(self) -> None:
        if not self.methodology_version.strip() or len(self.methodology_version) > 100:
            raise ValueError("methodology_version must contain 1..100 characters")
        if self.minimum_calibration_samples < 1:
            raise ValueError("minimum_calibration_samples must be positive")
        if self.maximum_snapshot_age_days < 1:
            raise ValueError("maximum_snapshot_age_days must be positive")
        for field, value in (
            (
                "maximum_median_absolute_error_ratio",
                self.maximum_median_absolute_error_ratio,
            ),
            (
                "maximum_p90_absolute_error_ratio",
                self.maximum_p90_absolute_error_ratio,
            ),
            ("maximum_absolute_bias_ratio", self.maximum_absolute_bias_ratio),
            ("maximum_drift_ratio", self.maximum_drift_ratio),
        ):
            if value < 0:
                raise ValueError(f"{field} cannot be negative")


@dataclass(frozen=True)
class BenchmarkQualityAssessment:
    snapshot_id: UUID
    benchmark_version: str
    methodology_version: str
    assessed_at: datetime
    servable: bool
    rebuild_required: bool
    reasons: tuple[BenchmarkRebuildReason, ...]
    limitations: tuple[str, ...]
    accuracy: BenchmarkAccuracyMetrics | None
    drift_score: Decimal | None
    previous_benchmark_version: str | None
    fallback_available: ReferenceScope | None

    def __post_init__(self) -> None:
        _require_aware(self.assessed_at, "assessed_at")
        if self.servable == self.rebuild_required:
            raise ValueError("servable and rebuild_required must be opposites")


class BenchmarkQualityGate:
    def __init__(self, policy: BenchmarkQualityPolicy) -> None:
        self._policy = policy

    def assess(
        self,
        *,
        snapshot: BenchmarkSnapshot,
        assessed_at: datetime,
        calibration_samples: tuple[BenchmarkCalibrationSample, ...] = (),
        previous: BenchmarkSnapshot | None = None,
    ) -> BenchmarkQualityAssessment:
        _require_aware(assessed_at, "assessed_at")
        if assessed_at < snapshot.as_of:
            raise ValueError("assessed_at cannot precede snapshot as_of")

        reasons: list[BenchmarkRebuildReason] = []
        limitations: list[str] = []

        if snapshot.methodology_version != self._policy.methodology_version:
            reasons.append(BenchmarkRebuildReason.METHODOLOGY)
            limitations.append("methodology_version_mismatch")

        maximum_age = timedelta(days=self._policy.maximum_snapshot_age_days)
        if assessed_at - snapshot.as_of > maximum_age:
            reasons.append(BenchmarkRebuildReason.STALE)
            limitations.append("benchmark_snapshot_stale")

        if (
            _CONFIDENCE_RANK[snapshot.confidence]
            < _CONFIDENCE_RANK[self._policy.minimum_serving_confidence]
        ):
            reasons.append(BenchmarkRebuildReason.LOW_CONFIDENCE)
            limitations.append("benchmark_confidence_below_serving_threshold")

        drift_score: Decimal | None = None
        previous_version: str | None = None
        if previous is not None:
            if previous.scope is not snapshot.scope:
                raise ValueError("drift comparison requires same scope")
            if previous.feature_key != snapshot.feature_key:
                raise ValueError("drift comparison requires same feature key")
            if previous.as_of > snapshot.as_of:
                raise ValueError("previous snapshot cannot be newer than current")
            previous_version = previous.benchmark_version
            baseline = previous.metrics.median_total_tokens
            delta = abs(snapshot.metrics.median_total_tokens - baseline)
            drift_score = delta / max(baseline, Decimal("1"))
            if drift_score > self._policy.maximum_drift_ratio:
                reasons.append(BenchmarkRebuildReason.DRIFT)
                limitations.append("benchmark_drift_threshold_exceeded")

        owned_calibration = tuple(
            item
            for item in calibration_samples
            if item.benchmark_version == snapshot.benchmark_version
            and snapshot.as_of <= item.occurred_at <= assessed_at
        )
        accuracy: BenchmarkAccuracyMetrics | None = None
        if len(owned_calibration) < self._policy.minimum_calibration_samples:
            limitations.append("insufficient_calibration_samples")
        else:
            accuracy = _accuracy_metrics(owned_calibration)
            if (
                accuracy.median_absolute_error_ratio
                > self._policy.maximum_median_absolute_error_ratio
                or accuracy.p90_absolute_error_ratio
                > self._policy.maximum_p90_absolute_error_ratio
            ):
                reasons.append(BenchmarkRebuildReason.ACCURACY)
                limitations.append("benchmark_accuracy_threshold_exceeded")
            if (
                abs(accuracy.bias_ratio)
                > self._policy.maximum_absolute_bias_ratio
            ):
                reasons.append(BenchmarkRebuildReason.BIAS)
                limitations.append("benchmark_bias_threshold_exceeded")

        unique_reasons = tuple(dict.fromkeys(reasons))
        rebuild_required = bool(unique_reasons)
        fallback = (
            ReferenceScope.GLOBAL_PUBLIC
            if rebuild_required and snapshot.scope is ReferenceScope.CLIENT_ONLY
            else None
        )
        if fallback is not None:
            limitations.append("no_implicit_cross_scope_fallback")

        return BenchmarkQualityAssessment(
            snapshot_id=snapshot.snapshot_id,
            benchmark_version=snapshot.benchmark_version,
            methodology_version=snapshot.methodology_version,
            assessed_at=assessed_at,
            servable=not rebuild_required,
            rebuild_required=rebuild_required,
            reasons=unique_reasons,
            limitations=tuple(dict.fromkeys(limitations)),
            accuracy=accuracy,
            drift_score=drift_score,
            previous_benchmark_version=previous_version,
            fallback_available=fallback,
        )

    def guard(
        self,
        *,
        snapshot: BenchmarkSnapshot,
        assessed_at: datetime,
        calibration_samples: tuple[BenchmarkCalibrationSample, ...] = (),
        previous: BenchmarkSnapshot | None = None,
    ) -> BenchmarkBuildResult:
        assessment = self.assess(
            snapshot=snapshot,
            assessed_at=assessed_at,
            calibration_samples=calibration_samples,
            previous=previous,
        )
        if assessment.servable:
            return BenchmarkBuildResult(
                requested_scope=snapshot.scope,
                snapshot=snapshot,
                limitations=assessment.limitations,
            )

        reason = _unavailable_reason(assessment.reasons)
        return BenchmarkBuildResult(
            requested_scope=snapshot.scope,
            snapshot=None,
            reason=reason,
            fallback_available=assessment.fallback_available,
            limitations=assessment.limitations,
        )


class BenchmarkBuilder:
    def __init__(self, policy: BenchmarkPolicy) -> None:
        self._policy = policy

    def build(
        self,
        *,
        scope: ReferenceScope,
        samples: tuple[BenchmarkSample, ...],
        feature_key: BenchmarkFeatureKey,
        as_of: datetime,
        tenant_id: UUID | None = None,
        client_id: UUID | None = None,
    ) -> BenchmarkBuildResult:
        _require_aware(as_of, "as_of")
        cutoff = as_of - timedelta(days=self._policy.maximum_sample_age_days)
        eligible = tuple(
            sample
            for sample in samples
            if sample.feature_key == feature_key
            and cutoff <= sample.occurred_at <= as_of
        )

        if scope is ReferenceScope.CLIENT_ONLY:
            if tenant_id is None or client_id is None:
                raise ValueError("CLIENT_ONLY build requires tenant_id and client_id")
            owned = tuple(
                sample
                for sample in eligible
                if sample.tenant_id == tenant_id and sample.client_id == client_id
            )
            if len(owned) < self._policy.minimum_client_samples:
                return BenchmarkBuildResult(
                    requested_scope=scope,
                    snapshot=None,
                    reason=BenchmarkUnavailableReason.INSUFFICIENT_CLIENT_HISTORY,
                    fallback_available=ReferenceScope.GLOBAL_PUBLIC,
                    limitations=("no_implicit_cross_scope_fallback",),
                )
            return BenchmarkBuildResult(
                requested_scope=scope,
                snapshot=self._snapshot(
                    scope=scope,
                    samples=owned,
                    feature_key=feature_key,
                    as_of=as_of,
                    tenant_id=tenant_id,
                    client_id=client_id,
                ),
            )

        if tenant_id is not None or client_id is not None:
            raise ValueError("GLOBAL_PUBLIC build does not accept tenant/client filters")
        global_eligible = tuple(sample for sample in eligible if sample.global_eligible)
        if len(global_eligible) < self._policy.minimum_global_samples:
            return BenchmarkBuildResult(
                requested_scope=scope,
                snapshot=None,
                reason=BenchmarkUnavailableReason.INSUFFICIENT_GLOBAL_SAMPLES,
                limitations=("minimum_global_sample_threshold_not_met",),
            )
        distinct_clients = {
            (sample.tenant_id, sample.client_id) for sample in global_eligible
        }
        if len(distinct_clients) < self._policy.minimum_global_clients:
            return BenchmarkBuildResult(
                requested_scope=scope,
                snapshot=None,
                reason=BenchmarkUnavailableReason.INSUFFICIENT_GLOBAL_COHORT,
                limitations=("minimum_distinct_client_cohort_not_met",),
            )
        return BenchmarkBuildResult(
            requested_scope=scope,
            snapshot=self._snapshot(
                scope=scope,
                samples=global_eligible,
                feature_key=feature_key,
                as_of=as_of,
            ),
        )

    def drift_score(
        self,
        *,
        previous: BenchmarkSnapshot,
        current: BenchmarkSnapshot,
    ) -> Decimal:
        if previous.feature_key != current.feature_key or previous.scope != current.scope:
            raise ValueError("drift comparison requires same scope and feature key")
        baseline = previous.metrics.median_total_tokens
        delta = abs(current.metrics.median_total_tokens - baseline)
        return delta / max(baseline, Decimal("1"))

    def _snapshot(
        self,
        *,
        scope: ReferenceScope,
        samples: tuple[BenchmarkSample, ...],
        feature_key: BenchmarkFeatureKey,
        as_of: datetime,
        tenant_id: UUID | None = None,
        client_id: UUID | None = None,
    ) -> BenchmarkSnapshot:
        distinct_clients = {(sample.tenant_id, sample.client_id) for sample in samples}
        sample_count = len(samples)
        client_count = len(distinct_clients)
        threshold = (
            self._policy.minimum_client_samples
            if scope is ReferenceScope.CLIENT_ONLY
            else self._policy.minimum_global_samples
        )
        confidence = self._confidence(
            sample_count=sample_count,
            client_count=client_count,
            threshold=threshold,
            scope=scope,
        )
        public_sample = (
            sample_count
            if scope is ReferenceScope.CLIENT_ONLY
            else self._bucket_count(
                sample_count,
                self._policy.public_count_bucket,
            )
        )
        public_cohort = (
            1
            if scope is ReferenceScope.CLIENT_ONLY
            else self._bucket_count(
                client_count,
                self._policy.public_cohort_bucket,
            )
        )
        snapshot_id = uuid7()
        return BenchmarkSnapshot(
            snapshot_id=snapshot_id,
            scope=scope,
            feature_key=feature_key,
            methodology_version=self._policy.methodology_version,
            benchmark_version=f"{self._policy.methodology_version}:{snapshot_id}",
            as_of=as_of,
            sample_count=sample_count,
            public_sample_size=public_sample,
            distinct_client_count=client_count,
            public_cohort_size=public_cohort,
            confidence=confidence,
            metrics=self._metrics(samples),
            tenant_id=tenant_id,
            client_id=client_id,
        )

    def _confidence(
        self,
        *,
        sample_count: int,
        client_count: int,
        threshold: int,
        scope: ReferenceScope,
    ) -> BenchmarkConfidence:
        if sample_count >= threshold * 4 and (
            scope is ReferenceScope.CLIENT_ONLY
            or client_count >= self._policy.minimum_global_clients * 2
        ):
            return BenchmarkConfidence.HIGH
        if sample_count >= threshold * 2:
            return BenchmarkConfidence.MEDIUM
        return BenchmarkConfidence.LOW

    @staticmethod
    def _bucket_count(value: int, bucket: int) -> int:
        return value - value % bucket

    @staticmethod
    def _metrics(samples: tuple[BenchmarkSample, ...]) -> BenchmarkMetrics:
        return BenchmarkMetrics(
            median_input_tokens=_median(sample.input_tokens for sample in samples),
            median_output_tokens=_median(sample.output_tokens for sample in samples),
            median_cached_input_tokens=_median(
                sample.cached_input_tokens for sample in samples
            ),
            median_reasoning_tokens=_median(
                sample.reasoning_tokens for sample in samples
            ),
            median_total_tokens=_median(sample.total_tokens for sample in samples),
            p90_output_tokens=_percentile90(
                tuple(sample.output_tokens for sample in samples)
            ),
            p90_total_tokens=_percentile90(
                tuple(sample.total_tokens for sample in samples)
            ),
        )


def _median(values: Iterable[int]) -> Decimal:
    sequence = tuple(values)
    return Decimal(str(median(sequence)))


def _percentile90(values: tuple[int, ...]) -> int:
    ordered = sorted(values)
    index = max(0, (9 * len(ordered) + 9) // 10 - 1)
    return ordered[min(index, len(ordered) - 1)]



def _accuracy_metrics(
    samples: tuple[BenchmarkCalibrationSample, ...],
) -> BenchmarkAccuracyMetrics:
    ratios = tuple(
        Decimal(abs(item.estimated_total_tokens - item.observed_total_tokens))
        / Decimal(max(item.observed_total_tokens, 1))
        for item in samples
    )
    estimated_total = sum(item.estimated_total_tokens for item in samples)
    observed_total = sum(item.observed_total_tokens for item in samples)
    bias = Decimal(estimated_total - observed_total) / Decimal(
        max(observed_total, 1)
    )
    return BenchmarkAccuracyMetrics(
        sample_count=len(samples),
        median_absolute_error_ratio=Decimal(median(ratios)),
        p90_absolute_error_ratio=_decimal_percentile90(ratios),
        bias_ratio=bias,
    )


def _decimal_percentile90(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    index = max(0, (9 * len(ordered) + 9) // 10 - 1)
    return ordered[min(index, len(ordered) - 1)]


def _unavailable_reason(
    reasons: tuple[BenchmarkRebuildReason, ...],
) -> BenchmarkUnavailableReason:
    if BenchmarkRebuildReason.STALE in reasons:
        return BenchmarkUnavailableReason.STALE_BENCHMARK
    if BenchmarkRebuildReason.METHODOLOGY in reasons:
        return BenchmarkUnavailableReason.METHODOLOGY_VERSION_MISMATCH
    if BenchmarkRebuildReason.DRIFT in reasons:
        return BenchmarkUnavailableReason.DRIFT_THRESHOLD_EXCEEDED
    if (
        BenchmarkRebuildReason.ACCURACY in reasons
        or BenchmarkRebuildReason.BIAS in reasons
    ):
        return BenchmarkUnavailableReason.CALIBRATION_THRESHOLD_EXCEEDED
    return BenchmarkUnavailableReason.LOW_CONFIDENCE
