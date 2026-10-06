"""Provider-free token/usage estimation application service."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .benchmarks import BenchmarkBuildResult, BenchmarkSnapshot, ReferenceScope


class EstimateExecutionMode(StrEnum):
    AUTO = "AUTO"
    EXPLICIT_TARGET = "EXPLICIT_TARGET"


@dataclass(frozen=True)
class EstimateTarget:
    provider_id: str
    model_id: str
    reasoning_profile: str

    def __post_init__(self) -> None:
        for field, value, maximum in (
            ("provider_id", self.provider_id, 100),
            ("model_id", self.model_id, 200),
            ("reasoning_profile", self.reasoning_profile, 100),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must contain 1..{maximum} characters")


@dataclass(frozen=True)
class EstimateSubject:
    tenant_id: str
    client_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.client_id.strip():
            raise ValueError("estimate subject requires tenant_id and client_id")


@dataclass(frozen=True)
class EstimateSpec:
    operation: str
    input_payload: Mapping[str, object]
    reference_scope: ReferenceScope
    execution_mode: EstimateExecutionMode = EstimateExecutionMode.AUTO
    target: EstimateTarget | None = None

    def __post_init__(self) -> None:
        if not self.operation.strip() or len(self.operation) > 100:
            raise ValueError("operation must contain 1..100 characters")
        if self.execution_mode is EstimateExecutionMode.AUTO and self.target is not None:
            raise ValueError("AUTO estimate must not pin a target")
        if self.execution_mode is EstimateExecutionMode.EXPLICIT_TARGET and self.target is None:
            raise ValueError("EXPLICIT_TARGET estimate requires target")


@dataclass(frozen=True)
class EstimatedTechnicalUsage:
    input_tokens: int
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None

    def __post_init__(self) -> None:
        for field, value in (
            ("input_tokens", self.input_tokens),
            ("cached_input_tokens", self.cached_input_tokens),
            ("output_tokens", self.output_tokens),
            ("reasoning_tokens", self.reasoning_tokens),
            ("total_tokens", self.total_tokens),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{field} cannot be negative")


@dataclass(frozen=True)
class EstimateResult:
    usage: EstimatedTechnicalUsage
    requested_execution_mode: EstimateExecutionMode
    effective_execution_mode: EstimateExecutionMode
    effective_target: EstimateTarget
    reference_scope: ReferenceScope
    estimation_method: str
    methodology_version: str
    benchmark_version: str
    as_of: object
    sample_size: int
    cohort_size: int
    confidence: str
    fallback_available: ReferenceScope | None
    limitations: tuple[str, ...]

    def client_payload(self) -> dict[str, object]:
        return {
            "estimated_input_tokens": self.usage.input_tokens,
            "estimated_cached_input_tokens": self.usage.cached_input_tokens,
            "estimated_output_tokens": self.usage.output_tokens,
            "estimated_reasoning_tokens": self.usage.reasoning_tokens,
            "estimated_total_tokens": self.usage.total_tokens,
            "requested_execution_mode": self.requested_execution_mode.value,
            "effective_execution_mode": self.effective_execution_mode.value,
            "effective_target": {
                "provider_id": self.effective_target.provider_id,
                "model_id": self.effective_target.model_id,
                "reasoning_profile": self.effective_target.reasoning_profile,
            },
            "reference_scope": self.reference_scope.value,
            "estimation_method": self.estimation_method,
            "methodology_version": self.methodology_version,
            "benchmark_version": self.benchmark_version,
            "as_of": self.as_of,
            "sample_size": self.sample_size,
            "cohort_size": self.cohort_size,
            "confidence": self.confidence,
            "fallback_available": (
                None if self.fallback_available is None else self.fallback_available.value
            ),
            "limitations": list(self.limitations),
        }


class EstimateForbidden(Exception):
    """The requested explicit target is outside the authorized envelope."""


class EstimateUnavailable(Exception):
    def __init__(
        self,
        reason: str,
        *,
        fallback_available: ReferenceScope | None = None,
        limitations: tuple[str, ...] = (),
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.fallback_available = fallback_available
        self.limitations = limitations


class EstimateTargetResolver(Protocol):
    async def ranked_auto_targets(
        self,
        *,
        subject: EstimateSubject,
        operation: str,
    ) -> tuple[EstimateTarget, ...]:
        """Return authorized/capable AUTO targets in internal policy order."""

    async def is_authorized(
        self,
        *,
        subject: EstimateSubject,
        operation: str,
        target: EstimateTarget,
    ) -> bool:
        """Check the same target envelope used by execution authorization."""


class EstimateBenchmarkLookup(Protocol):
    async def lookup(
        self,
        *,
        subject: EstimateSubject,
        target: EstimateTarget,
        scope: ReferenceScope,
    ) -> BenchmarkBuildResult:
        """Resolve the latest governed benchmark without raw cross-tenant data."""


class InputTokenEstimator(Protocol):
    def estimate(self, payload: Mapping[str, object]) -> int:
        """Estimate input tokens locally, without a provider call."""


class JsonByteInputTokenEstimator:
    """Deterministic zero-network approximation used until model tokenizers are wired."""

    def estimate(self, payload: Mapping[str, object]) -> int:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if not encoded:
            return 0
        return math.ceil(len(encoded) / 4)


class EstimateService(Protocol):
    async def estimate(
        self,
        *,
        subject: EstimateSubject,
        spec: EstimateSpec,
    ) -> EstimateResult: ...


class EstimateEngine:
    """Provider-free estimate engine preserving AUTO/EXPLICIT_TARGET semantics."""

    def __init__(
        self,
        *,
        targets: EstimateTargetResolver,
        benchmarks: EstimateBenchmarkLookup,
        input_tokens: InputTokenEstimator | None = None,
    ) -> None:
        self._targets = targets
        self._benchmarks = benchmarks
        self._input_tokens = input_tokens or JsonByteInputTokenEstimator()

    async def estimate(
        self,
        *,
        subject: EstimateSubject,
        spec: EstimateSpec,
    ) -> EstimateResult:
        if spec.execution_mode is EstimateExecutionMode.EXPLICIT_TARGET:
            assert spec.target is not None
            if not await self._targets.is_authorized(
                subject=subject,
                operation=spec.operation,
                target=spec.target,
            ):
                raise EstimateForbidden("explicit estimate target is not authorized")
            lookup = await self._benchmarks.lookup(
                subject=subject,
                target=spec.target,
                scope=spec.reference_scope,
            )
            return self._result_or_raise(spec=spec, target=spec.target, lookup=lookup)

        targets = await self._targets.ranked_auto_targets(
            subject=subject,
            operation=spec.operation,
        )
        if not targets:
            raise EstimateUnavailable("NO_ELIGIBLE_TARGET")

        misses: list[BenchmarkBuildResult] = []
        for target in targets:
            lookup = await self._benchmarks.lookup(
                subject=subject,
                target=target,
                scope=spec.reference_scope,
            )
            if lookup.snapshot is not None:
                return self._result_or_raise(spec=spec, target=target, lookup=lookup)
            misses.append(lookup)

        fallback = next(
            (item.fallback_available for item in misses if item.fallback_available is not None),
            None,
        )
        limitations = tuple(
            dict.fromkeys(
                limitation
                for item in misses
                for limitation in item.limitations
            )
        )
        reason = next(
            (item.reason.value for item in misses if item.reason is not None),
            "BENCHMARK_UNAVAILABLE",
        )
        raise EstimateUnavailable(
            reason,
            fallback_available=fallback,
            limitations=limitations,
        )

    def _result_or_raise(
        self,
        *,
        spec: EstimateSpec,
        target: EstimateTarget,
        lookup: BenchmarkBuildResult,
    ) -> EstimateResult:
        snapshot = lookup.snapshot
        if snapshot is None:
            raise EstimateUnavailable(
                "BENCHMARK_UNAVAILABLE" if lookup.reason is None else lookup.reason.value,
                fallback_available=lookup.fallback_available,
                limitations=lookup.limitations,
            )

        input_tokens = self._input_tokens.estimate(spec.input_payload)
        return _result_from_snapshot(
            spec=spec,
            target=target,
            snapshot=snapshot,
            input_tokens=input_tokens,
            fallback_available=lookup.fallback_available,
            limitations=lookup.limitations,
        )


def _result_from_snapshot(
    *,
    spec: EstimateSpec,
    target: EstimateTarget,
    snapshot: BenchmarkSnapshot,
    input_tokens: int,
    fallback_available: ReferenceScope | None,
    limitations: tuple[str, ...],
) -> EstimateResult:
    output_tokens = max(0, round(snapshot.metrics.median_output_tokens))
    cached_tokens = max(0, round(snapshot.metrics.median_cached_input_tokens))
    reasoning_tokens = max(0, round(snapshot.metrics.median_reasoning_tokens))
    cached_tokens = min(cached_tokens, input_tokens)
    total_tokens = input_tokens + output_tokens
    metadata = snapshot.client_metadata()
    return EstimateResult(
        usage=EstimatedTechnicalUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
        ),
        requested_execution_mode=spec.execution_mode,
        effective_execution_mode=spec.execution_mode,
        effective_target=target,
        reference_scope=spec.reference_scope,
        estimation_method="LOCAL_JSON_BYTES_PLUS_BENCHMARK_MEDIAN",
        methodology_version=snapshot.methodology_version,
        benchmark_version=snapshot.benchmark_version,
        as_of=snapshot.as_of,
        sample_size=int(metadata["sample_size"]),
        cohort_size=int(metadata["cohort_size"]),
        confidence=snapshot.confidence.value,
        fallback_available=fallback_available,
        limitations=limitations,
    )
