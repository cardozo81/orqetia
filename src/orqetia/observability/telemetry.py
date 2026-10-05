"""Phase-2 observability contracts and safe structured telemetry primitives."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, TextIO
from uuid import UUID

from orqetia.execution import ProviderAttempt
from orqetia.shared.messaging import WorkLease

REDACTED = "[REDACTED]"
UNSUPPORTED = "[UNSUPPORTED_VALUE]"

_SENSITIVE_KEYS = frozenset(
    {
        "amount",
        "access_token",
        "api_key",
        "authorization",
        "balance",
        "client_charge",
        "content",
        "cookie",
        "credential",
        "credentials",
        "cost_amount",
        "credit",
        "currency",
        "password",
        "payload",
        "prompt",
        "provider_account",
        "provider_cost",
        "provider_cost_amount",
        "provider_credential",
        "provider_credentials",
        "raw_input",
        "raw_output",
        "raw_prompt",
        "refresh_token",
        "request_body",
        "response_body",
        "secret",
        "set_cookie",
    }
)
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_credential",
    "_credentials",
    "_password",
    "_secret",
    "_access_token",
    "_refresh_token",
)


class EventEmitter(Protocol):
    """Minimal structured event sink used across runtime composition boundaries."""

    def emit(self, event: str, fields: Mapping[str, object]) -> None:
        """Emit one safe structured event."""


class NullEventEmitter:
    """Explicit no-op emitter for tests or composition points that do not log."""

    def emit(self, event: str, fields: Mapping[str, object]) -> None:
        del event, fields


class JsonEventEmitter:
    """JSON-lines emitter that redacts sensitive or payload-bearing fields."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def emit(self, event: str, fields: Mapping[str, object]) -> None:
        normalized_event = event.strip()
        if not normalized_event:
            raise ValueError("event is required")
        record: dict[str, object] = {
            "event": normalized_event,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        record.update(sanitize_telemetry_mapping(fields))
        print(
            json.dumps(record, ensure_ascii=False, sort_keys=True),
            file=self._stream or sys.stdout,
            flush=True,
        )


def sanitize_telemetry_mapping(fields: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): sanitize_telemetry_value(value, key=str(key))
        for key, value in fields.items()
    }


def sanitize_telemetry_value(value: object, *, key: str | None = None) -> object:
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return sanitize_telemetry_mapping(
            {str(nested_key): nested_value for nested_key, nested_value in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_telemetry_value(item) for item in value]
    return UNSUPPORTED


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_KEYS:
        return True
    return any(normalized.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def work_telemetry_fields(lease: WorkLease) -> dict[str, object]:
    """Return safe queue/ownership/correlation fields without inline work payload."""

    return {
        "work_id": lease.work_id,
        "queue_name": lease.queue_name,
        "operation_type": lease.operation_type,
        "operation_version": lease.operation_version,
        "tenant_id": lease.tenant_id,
        "client_id": lease.client_id,
        "resource_type": lease.resource_type,
        "resource_id": lease.resource_id,
        "correlation_id": lease.correlation_id,
        "causation_id": lease.causation_id,
        "trace_id": lease.trace_id,
        "work_attempt_count": lease.attempt_count,
    }


def provider_attempt_telemetry_fields(
    lease: WorkLease,
    attempt: ProviderAttempt,
) -> dict[str, object]:
    fields = work_telemetry_fields(lease)
    fields.update(
        {
            "attempt_id": attempt.attempt_id,
            "session_id": attempt.session_id,
            "task_id": attempt.task_id,
            "cycle_index": attempt.cycle,
            "attempt_index": attempt.attempt_index,
            "provider_id": attempt.target.provider_id,
            "model_id": attempt.target.model_id,
            "reasoning_profile": attempt.target.reasoning_profile,
            "attempt_status": attempt.status,
        }
    )
    return fields


_LOW_CARDINALITY_LABELS = frozenset(
    {
        "attempt_status",
        "disposition",
        "execution_mode",
        "health",
        "operation_type",
        "provider_outcome",
        "quarantine",
        "queue_name",
        "work_state",
    }
)


@dataclass(frozen=True)
class MetricContract:
    name: str
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.startswith("orqetia_"):
            raise ValueError("metric name must use orqetia_ prefix")
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("metric labels must be unique")
        unsupported = set(self.labels) - _LOW_CARDINALITY_LABELS
        if unsupported:
            raise ValueError(
                "metric labels must be bounded low-cardinality dimensions: "
                + ", ".join(sorted(unsupported))
            )


METRIC_CONTRACTS = (
    MetricContract(
        "orqetia_work_events_total",
        ("queue_name", "operation_type", "disposition"),
    ),
    MetricContract(
        "orqetia_provider_attempts_total",
        ("provider_outcome", "attempt_status"),
    ),
    MetricContract(
        "orqetia_provider_attempt_latency_ms",
        ("provider_outcome",),
    ),
)


@dataclass(frozen=True)
class SpanContract:
    name: str
    attributes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.startswith("orqetia."):
            raise ValueError("span name must use orqetia. prefix")
        if len(set(self.attributes)) != len(self.attributes):
            raise ValueError("span attributes must be unique")
        if any(_is_sensitive_key(attribute) for attribute in self.attributes):
            raise ValueError("span contract cannot include sensitive attributes")


SPAN_CONTRACTS = (
    SpanContract(
        "orqetia.work",
        (
            "work_id",
            "correlation_id",
            "tenant_id",
            "client_id",
            "queue_name",
            "operation_type",
        ),
    ),
    SpanContract(
        "orqetia.provider_attempt",
        (
            "attempt_id",
            "session_id",
            "task_id",
            "correlation_id",
            "provider_id",
            "model_id",
            "reasoning_profile",
            "provider_outcome",
        ),
    ),
)
