"""Broker-neutral messaging contracts shared across ORQETIA composition boundaries."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

MAX_INLINE_PAYLOAD_BYTES = 64 * 1024


class DataClassification(StrEnum):
    SECRET = "SECRET"
    RESTRICTED = "RESTRICTED"
    CONFIDENTIAL = "CONFIDENTIAL"
    CLIENT_PRIVATE = "CLIENT_PRIVATE"
    INTERNAL = "INTERNAL"
    PUBLIC = "PUBLIC"


class WorkState(StrEnum):
    READY = "READY"
    LEASED = "LEASED"
    DONE = "DONE"
    DEAD = "DEAD"
    CANCELLED = "CANCELLED"


class DeliveryState(StrEnum):
    READY = "READY"
    LEASED = "LEASED"
    ACKED = "ACKED"
    DEAD = "DEAD"


class QueueName(StrEnum):
    EXECUTION = "execution"
    SCHEDULER = "scheduler"
    ACCOUNTING = "accounting"
    PROJECTION = "projection"
    MAINTENANCE = "maintenance"


JsonObject = Mapping[str, Any]


def validate_inline_payload(
    payload: JsonObject,
    *,
    data_classification: DataClassification,
    tenant_id: UUID | None,
    client_id: UUID | None,
) -> None:
    if data_classification is DataClassification.SECRET:
        raise ValueError("SECRET payload is prohibited in messaging")

    if data_classification is DataClassification.CLIENT_PRIVATE:
        if tenant_id is None or client_id is None:
            raise ValueError("CLIENT_PRIVATE payload requires tenant_id and client_id")

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("messaging payload must be JSON serializable") from exc

    if len(encoded) > MAX_INLINE_PAYLOAD_BYTES:
        raise ValueError("inline messaging payload exceeds 64 KiB")


@dataclass(frozen=True)
class WorkItem:
    work_id: UUID
    queue_name: QueueName
    operation_type: str
    operation_version: int
    data_classification: DataClassification
    payload: JsonObject
    available_at: datetime
    priority: int = 0
    max_infrastructure_attempts: int = 5
    tenant_id: UUID | None = None
    client_id: UUID | None = None
    resource_type: str | None = None
    resource_id: UUID | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    trace_id: str | None = None
    logical_operation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.operation_type.strip():
            raise ValueError("operation_type is required")
        if self.operation_version < 1:
            raise ValueError("operation_version must be >= 1")
        if not -100 <= self.priority <= 100:
            raise ValueError("priority must be between -100 and 100")
        if not 1 <= self.max_infrastructure_attempts <= 100:
            raise ValueError("max_infrastructure_attempts must be between 1 and 100")
        validate_inline_payload(
            self.payload,
            data_classification=self.data_classification,
            tenant_id=self.tenant_id,
            client_id=self.client_id,
        )


@dataclass(frozen=True)
class WorkLease:
    work_id: UUID
    queue_name: QueueName
    operation_type: str
    operation_version: int
    payload: JsonObject
    data_classification: DataClassification
    tenant_id: UUID | None
    client_id: UUID | None
    resource_type: str | None
    resource_id: UUID | None
    correlation_id: UUID | None
    causation_id: UUID | None
    trace_id: str | None
    logical_operation_id: str | None
    lease_owner: str
    lease_until: datetime
    attempt_count: int


@dataclass(frozen=True)
class EventEnvelope:
    event_id: UUID
    event_type: str
    event_version: int
    producer: str
    occurred_at: datetime
    aggregate_type: str
    aggregate_id: UUID
    data_classification: DataClassification
    payload: JsonObject
    tenant_id: UUID | None = None
    client_id: UUID | None = None
    aggregate_version: int | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_type.strip() or "." not in self.event_type:
            raise ValueError("event_type must be a dotted event contract name")
        if self.event_version < 1:
            raise ValueError("event_version must be >= 1")
        if not self.producer.strip():
            raise ValueError("producer is required")
        if not self.aggregate_type.strip():
            raise ValueError("aggregate_type is required")
        validate_inline_payload(
            self.payload,
            data_classification=self.data_classification,
            tenant_id=self.tenant_id,
            client_id=self.client_id,
        )


@dataclass(frozen=True)
class EventDeliveryLease:
    delivery_id: UUID
    consumer_name: str
    envelope: EventEnvelope
    lease_owner: str
    lease_until: datetime
    attempt_count: int


class WorkQueuePort(Protocol):
    async def enqueue(self, item: WorkItem) -> None: ...

    async def claim(
        self,
        *,
        queue_name: QueueName,
        lease_owner: str,
        lease_seconds: int,
        limit: int,
    ) -> Sequence[WorkLease]: ...

    async def renew_lease(self, lease: WorkLease, *, lease_seconds: int) -> bool: ...

    async def complete(self, lease: WorkLease) -> bool: ...

    async def requeue_infrastructure_failure(
        self,
        lease: WorkLease,
        *,
        available_at: datetime,
        error_class: str,
    ) -> bool: ...


class EventTransportPort(Protocol):
    async def register_deliveries(
        self,
        envelope: EventEnvelope,
        *,
        consumers: Sequence[str],
    ) -> int: ...

    async def claim(
        self,
        *,
        consumer_name: str,
        lease_owner: str,
        lease_seconds: int,
        limit: int,
    ) -> Sequence[EventDeliveryLease]: ...

    async def acknowledge(self, lease: EventDeliveryLease) -> bool: ...


class WakeupPort(Protocol):
    async def notify(self, queue_name: QueueName) -> None: ...
