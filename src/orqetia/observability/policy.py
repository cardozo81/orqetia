"""Versioned operational observability policy and cost/cardinality controls."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .telemetry import METRIC_CONTRACTS, MetricContract

OBSERVABILITY_POLICY_VERSION = "observability-v1"


class TelemetrySignal(StrEnum):
    LOGS = "logs"
    METRICS = "metrics"
    TRACES = "traces"


class TelemetryEventClass(StrEnum):
    OPERATIONAL = "operational"
    SECURITY = "security"


@dataclass(frozen=True)
class RetentionPolicy:
    log_days: int
    metric_days: int
    trace_days: int
    security_log_days: int

    def __post_init__(self) -> None:
        for field, value in (
            ("log_days", self.log_days),
            ("metric_days", self.metric_days),
            ("trace_days", self.trace_days),
            ("security_log_days", self.security_log_days),
        ):
            if value < 1:
                raise ValueError(f"{field} must be positive")
        if self.security_log_days < self.log_days:
            raise ValueError(
                "security_log_days cannot be shorter than operational log retention"
            )

    def days_for(
        self,
        signal: TelemetrySignal,
        *,
        event_class: TelemetryEventClass = TelemetryEventClass.OPERATIONAL,
    ) -> int:
        if signal is TelemetrySignal.LOGS:
            return (
                self.security_log_days
                if event_class is TelemetryEventClass.SECURITY
                else self.log_days
            )
        if signal is TelemetrySignal.METRICS:
            return self.metric_days
        return self.trace_days


@dataclass(frozen=True)
class VolumeBudget:
    soft_events_per_minute: Mapping[TelemetrySignal, int]
    hard_events_per_minute: Mapping[TelemetrySignal, int]
    saturation_alert_ratio: float = 0.80

    def __post_init__(self) -> None:
        if not 0 < self.saturation_alert_ratio < 1:
            raise ValueError("saturation_alert_ratio must be between 0 and 1")
        for signal in TelemetrySignal:
            soft = self.soft_events_per_minute.get(signal)
            hard = self.hard_events_per_minute.get(signal)
            if soft is None or hard is None:
                raise ValueError(f"volume budget missing {signal.value}")
            if soft < 1 or hard < soft:
                raise ValueError(
                    f"invalid volume budget for {signal.value}: hard must be >= soft"
                )

    def saturation_threshold(self, signal: TelemetrySignal) -> int:
        return max(
            1,
            int(
                self.hard_events_per_minute[signal]
                * self.saturation_alert_ratio
            ),
        )


@dataclass(frozen=True)
class CardinalityBudget:
    max_labels_per_metric: int
    distinct_values_per_label: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.max_labels_per_metric < 1:
            raise ValueError("max_labels_per_metric must be positive")
        if not self.distinct_values_per_label:
            raise ValueError("distinct_values_per_label cannot be empty")
        if any(value < 1 for value in self.distinct_values_per_label.values()):
            raise ValueError("label cardinality budgets must be positive")

    def validate_contract(self, contract: MetricContract) -> None:
        if len(contract.labels) > self.max_labels_per_metric:
            raise ValueError(
                f"{contract.name} exceeds max metric label count "
                f"{self.max_labels_per_metric}"
            )
        missing = set(contract.labels) - set(self.distinct_values_per_label)
        if missing:
            raise ValueError(
                f"{contract.name} has labels without cardinality budgets: "
                + ", ".join(sorted(missing))
            )


@dataclass(frozen=True)
class OperationalObservabilityPolicy:
    version: str
    environment: str
    retention: RetentionPolicy
    volume: VolumeBudget
    cardinality: CardinalityBudget
    default_trace_sample_rate: float
    correlation_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version != OBSERVABILITY_POLICY_VERSION:
            raise ValueError("unsupported observability policy version")
        if self.environment not in {"local", "test", "staging", "production"}:
            raise ValueError("unsupported observability environment")
        if not 0 <= self.default_trace_sample_rate <= 1:
            raise ValueError("default_trace_sample_rate must be between 0 and 1")
        required_correlation = {"correlation_id", "trace_id"}
        if not required_correlation.issubset(self.correlation_fields):
            raise ValueError("correlation policy must preserve correlation_id and trace_id")
        for contract in METRIC_CONTRACTS:
            self.cardinality.validate_contract(contract)


_CARDINALITY = CardinalityBudget(
    max_labels_per_metric=4,
    distinct_values_per_label=MappingProxyType(
        {
            "attempt_status": 16,
            "disposition": 16,
            "execution_mode": 4,
            "health": 8,
            "operation_type": 64,
            "provider_outcome": 32,
            "quarantine": 4,
            "queue_name": 8,
            "work_state": 16,
        }
    ),
)

_CORRELATION_FIELDS = (
    "correlation_id",
    "causation_id",
    "trace_id",
    "session_id",
    "task_id",
    "attempt_id",
    "work_id",
)


def _volume(
    *,
    logs: tuple[int, int],
    metrics: tuple[int, int],
    traces: tuple[int, int],
) -> VolumeBudget:
    return VolumeBudget(
        soft_events_per_minute=MappingProxyType(
            {
                TelemetrySignal.LOGS: logs[0],
                TelemetrySignal.METRICS: metrics[0],
                TelemetrySignal.TRACES: traces[0],
            }
        ),
        hard_events_per_minute=MappingProxyType(
            {
                TelemetrySignal.LOGS: logs[1],
                TelemetrySignal.METRICS: metrics[1],
                TelemetrySignal.TRACES: traces[1],
            }
        ),
    )


_POLICIES = MappingProxyType(
    {
        "local": OperationalObservabilityPolicy(
            version=OBSERVABILITY_POLICY_VERSION,
            environment="local",
            retention=RetentionPolicy(
                log_days=3,
                metric_days=7,
                trace_days=3,
                security_log_days=30,
            ),
            volume=_volume(
                logs=(5_000, 10_000),
                metrics=(10_000, 20_000),
                traces=(2_000, 5_000),
            ),
            cardinality=_CARDINALITY,
            default_trace_sample_rate=1.0,
            correlation_fields=_CORRELATION_FIELDS,
        ),
        "test": OperationalObservabilityPolicy(
            version=OBSERVABILITY_POLICY_VERSION,
            environment="test",
            retention=RetentionPolicy(
                log_days=1,
                metric_days=3,
                trace_days=1,
                security_log_days=7,
            ),
            volume=_volume(
                logs=(5_000, 10_000),
                metrics=(10_000, 20_000),
                traces=(2_000, 5_000),
            ),
            cardinality=_CARDINALITY,
            default_trace_sample_rate=1.0,
            correlation_fields=_CORRELATION_FIELDS,
        ),
        "staging": OperationalObservabilityPolicy(
            version=OBSERVABILITY_POLICY_VERSION,
            environment="staging",
            retention=RetentionPolicy(
                log_days=14,
                metric_days=30,
                trace_days=7,
                security_log_days=90,
            ),
            volume=_volume(
                logs=(25_000, 50_000),
                metrics=(50_000, 100_000),
                traces=(10_000, 25_000),
            ),
            cardinality=_CARDINALITY,
            default_trace_sample_rate=0.25,
            correlation_fields=_CORRELATION_FIELDS,
        ),
        "production": OperationalObservabilityPolicy(
            version=OBSERVABILITY_POLICY_VERSION,
            environment="production",
            retention=RetentionPolicy(
                log_days=30,
                metric_days=90,
                trace_days=14,
                security_log_days=180,
            ),
            volume=_volume(
                logs=(100_000, 200_000),
                metrics=(250_000, 500_000),
                traces=(25_000, 75_000),
            ),
            cardinality=_CARDINALITY,
            default_trace_sample_rate=0.10,
            correlation_fields=_CORRELATION_FIELDS,
        ),
    }
)


def default_observability_policy(environment: str) -> OperationalObservabilityPolicy:
    """Return the immutable v1 policy for one runtime environment."""

    normalized = environment.strip().lower()
    try:
        return _POLICIES[normalized]
    except KeyError as error:
        raise ValueError("unsupported observability environment") from error


def should_sample_trace(trace_id: str, sample_rate: float) -> bool:
    """Deterministically sample by trace ID so an entire trace stays correlated."""

    normalized = trace_id.strip()
    if not normalized:
        raise ValueError("trace_id is required")
    if not 0 <= sample_rate <= 1:
        raise ValueError("sample_rate must be between 0 and 1")
    if sample_rate <= 0:
        return False
    if sample_rate >= 1:
        return True

    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big", signed=False)
    threshold = int(sample_rate * (1 << 64))
    return bucket < threshold
