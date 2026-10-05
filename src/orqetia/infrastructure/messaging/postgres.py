"""PostgreSQL implementation of ORQETIA messaging ports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from orqetia.shared.messaging import (
    DataClassification,
    DeliveryState,
    EventDeliveryLease,
    EventEnvelope,
    EventTransportPort,
    QueueName,
    WakeupPort,
    WorkItem,
    WorkLease,
    WorkQueuePort,
    WorkState,
)

from .tables import event_deliveries, work_items

SessionFactory = async_sessionmaker[AsyncSession]


def _lease_deadline(seconds: int) -> datetime:
    if not 1 <= seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise ValueError("claim limit must be between 1 and 100")
    return value


def _work_lease(row: sa.RowMapping) -> WorkLease:
    return WorkLease(
        work_id=row["work_id"],
        queue_name=QueueName(row["queue_name"]),
        operation_type=row["operation_type"],
        operation_version=row["operation_version"],
        payload=row["payload"],
        data_classification=DataClassification(row["data_classification"]),
        tenant_id=row["tenant_id"],
        client_id=row["client_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        trace_id=row["trace_id"],
        logical_operation_id=row["logical_operation_id"],
        lease_owner=row["lease_owner"],
        lease_until=row["lease_until"],
        attempt_count=row["attempt_count"],
    )


def _event_envelope(row: sa.RowMapping) -> EventEnvelope:
    return EventEnvelope(
        event_id=row["event_id"],
        event_type=row["event_type"],
        event_version=row["event_version"],
        producer=row["producer"],
        occurred_at=row["occurred_at"],
        tenant_id=row["tenant_id"],
        client_id=row["client_id"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        aggregate_version=row["aggregate_version"],
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        trace_id=row["trace_id"],
        data_classification=DataClassification(row["data_classification"]),
        payload=row["payload"],
    )


class PostgresWorkQueue(WorkQueuePort):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def enqueue(self, item: WorkItem) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                sa.insert(work_items).values(
                    work_id=item.work_id,
                    queue_name=item.queue_name.value,
                    operation_type=item.operation_type,
                    operation_version=item.operation_version,
                    tenant_id=item.tenant_id,
                    client_id=item.client_id,
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    data_classification=item.data_classification.value,
                    payload=dict(item.payload),
                    priority=item.priority,
                    state=WorkState.READY.value,
                    available_at=item.available_at,
                    max_infrastructure_attempts=item.max_infrastructure_attempts,
                    correlation_id=item.correlation_id,
                    causation_id=item.causation_id,
                    trace_id=item.trace_id,
                    logical_operation_id=item.logical_operation_id,
                )
            )

    async def claim(
        self,
        *,
        queue_name: QueueName,
        lease_owner: str,
        lease_seconds: int,
        limit: int,
    ) -> Sequence[WorkLease]:
        if not lease_owner.strip():
            raise ValueError("lease_owner is required")

        deadline = _lease_deadline(lease_seconds)
        claim_limit = _limit(limit)

        claimable = (
            sa.select(work_items.c.work_id)
            .where(
                work_items.c.queue_name == queue_name.value,
                work_items.c.available_at <= sa.func.now(),
                sa.or_(
                    work_items.c.state == WorkState.READY.value,
                    sa.and_(
                        work_items.c.state == WorkState.LEASED.value,
                        work_items.c.lease_until <= sa.func.now(),
                    ),
                ),
            )
            .order_by(
                work_items.c.priority.desc(),
                work_items.c.available_at.asc(),
                work_items.c.work_id.asc(),
            )
            .limit(claim_limit)
            .with_for_update(skip_locked=True)
            .cte("claimable_work")
        )

        statement = (
            sa.update(work_items)
            .where(work_items.c.work_id.in_(sa.select(claimable.c.work_id)))
            .values(
                state=WorkState.LEASED.value,
                lease_owner=lease_owner,
                lease_until=deadline,
                attempt_count=work_items.c.attempt_count + 1,
                started_at=sa.func.coalesce(work_items.c.started_at, sa.func.now()),
            )
            .returning(*work_items.c)
        )

        async with self._sessions.begin() as session:
            rows = (await session.execute(statement)).mappings().all()
        return tuple(_work_lease(row) for row in rows)

    async def complete(self, lease: WorkLease) -> bool:
        statement = (
            sa.update(work_items)
            .where(
                work_items.c.work_id == lease.work_id,
                work_items.c.state == WorkState.LEASED.value,
                work_items.c.lease_owner == lease.lease_owner,
                work_items.c.lease_until == lease.lease_until,
            )
            .values(
                state=WorkState.DONE.value,
                completed_at=sa.func.now(),
                lease_owner=None,
                lease_until=None,
            )
        )
        async with self._sessions.begin() as session:
            result = await session.execute(statement)
        return result.rowcount == 1

    async def requeue_infrastructure_failure(
        self,
        lease: WorkLease,
        *,
        available_at: datetime,
        error_class: str,
    ) -> bool:
        if not error_class.strip():
            raise ValueError("error_class is required")

        statement = (
            sa.update(work_items)
            .where(
                work_items.c.work_id == lease.work_id,
                work_items.c.state == WorkState.LEASED.value,
                work_items.c.lease_owner == lease.lease_owner,
                work_items.c.lease_until == lease.lease_until,
                work_items.c.attempt_count < work_items.c.max_infrastructure_attempts,
            )
            .values(
                state=WorkState.READY.value,
                available_at=available_at,
                lease_owner=None,
                lease_until=None,
                last_error_class=error_class,
            )
        )
        async with self._sessions.begin() as session:
            result = await session.execute(statement)
        return result.rowcount == 1


class PostgresEventTransport(EventTransportPort):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def register_deliveries(
        self,
        envelope: EventEnvelope,
        *,
        consumers: Sequence[str],
    ) -> int:
        unique_consumers = tuple(dict.fromkeys(name.strip() for name in consumers if name.strip()))
        if not unique_consumers:
            return 0

        values = [
            {
                "delivery_id": uuid7(),
                "event_id": envelope.event_id,
                "consumer_name": consumer,
                "event_type": envelope.event_type,
                "event_version": envelope.event_version,
                "producer": envelope.producer,
                "occurred_at": envelope.occurred_at,
                "tenant_id": envelope.tenant_id,
                "client_id": envelope.client_id,
                "aggregate_type": envelope.aggregate_type,
                "aggregate_id": envelope.aggregate_id,
                "aggregate_version": envelope.aggregate_version,
                "correlation_id": envelope.correlation_id,
                "causation_id": envelope.causation_id,
                "trace_id": envelope.trace_id,
                "data_classification": envelope.data_classification.value,
                "payload": dict(envelope.payload),
                "state": DeliveryState.READY.value,
                "available_at": datetime.now(UTC),
            }
            for consumer in unique_consumers
        ]

        statement = (
            pg_insert(event_deliveries)
            .values(values)
            .on_conflict_do_nothing(index_elements=["event_id", "consumer_name"])
        )

        async with self._sessions.begin() as session:
            result = await session.execute(statement)
        return result.rowcount or 0

    async def claim(
        self,
        *,
        consumer_name: str,
        lease_owner: str,
        lease_seconds: int,
        limit: int,
    ) -> Sequence[EventDeliveryLease]:
        if not consumer_name.strip() or not lease_owner.strip():
            raise ValueError("consumer_name and lease_owner are required")

        deadline = _lease_deadline(lease_seconds)
        claim_limit = _limit(limit)

        claimable = (
            sa.select(event_deliveries.c.delivery_id)
            .where(
                event_deliveries.c.consumer_name == consumer_name,
                event_deliveries.c.available_at <= sa.func.now(),
                sa.or_(
                    event_deliveries.c.state == DeliveryState.READY.value,
                    sa.and_(
                        event_deliveries.c.state == DeliveryState.LEASED.value,
                        event_deliveries.c.lease_until <= sa.func.now(),
                    ),
                ),
            )
            .order_by(event_deliveries.c.available_at, event_deliveries.c.delivery_id)
            .limit(claim_limit)
            .with_for_update(skip_locked=True)
            .cte("claimable_deliveries")
        )

        statement = (
            sa.update(event_deliveries)
            .where(event_deliveries.c.delivery_id.in_(sa.select(claimable.c.delivery_id)))
            .values(
                state=DeliveryState.LEASED.value,
                lease_owner=lease_owner,
                lease_until=deadline,
                attempt_count=event_deliveries.c.attempt_count + 1,
                delivered_at=sa.func.coalesce(event_deliveries.c.delivered_at, sa.func.now()),
            )
            .returning(*event_deliveries.c)
        )

        async with self._sessions.begin() as session:
            rows = (await session.execute(statement)).mappings().all()

        return tuple(
            EventDeliveryLease(
                delivery_id=row["delivery_id"],
                consumer_name=row["consumer_name"],
                envelope=_event_envelope(row),
                lease_owner=row["lease_owner"],
                lease_until=row["lease_until"],
                attempt_count=row["attempt_count"],
            )
            for row in rows
        )

    async def acknowledge(self, lease: EventDeliveryLease) -> bool:
        statement = (
            sa.update(event_deliveries)
            .where(
                event_deliveries.c.delivery_id == lease.delivery_id,
                event_deliveries.c.state == DeliveryState.LEASED.value,
                event_deliveries.c.lease_owner == lease.lease_owner,
                event_deliveries.c.lease_until == lease.lease_until,
            )
            .values(
                state=DeliveryState.ACKED.value,
                acked_at=sa.func.now(),
                lease_owner=None,
                lease_until=None,
            )
        )
        async with self._sessions.begin() as session:
            result = await session.execute(statement)
        return result.rowcount == 1


class PostgresWakeup(WakeupPort):
    CHANNEL = "orqetia_work_available"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def notify(self, queue_name: QueueName) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text("SELECT pg_notify(:channel, :payload)"),
                {"channel": self.CHANNEL, "payload": queue_name.value},
            )
