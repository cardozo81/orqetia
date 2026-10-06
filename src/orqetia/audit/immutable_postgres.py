"""PostgreSQL adapter for the immutable audit ledger."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .immutable import (
    AuditClass,
    ImmutableAuditEvent,
    verify_event_integrity,
)
from .immutable_tables import audit_events

SessionFactory = async_sessionmaker[AsyncSession]


def _from_row(row: RowMapping) -> ImmutableAuditEvent:
    return ImmutableAuditEvent(
        event_id=cast(UUID, row["event_id"]),
        audit_class=AuditClass(cast(str, row["audit_class"])),
        action=cast(str, row["action"]),
        result=cast(str, row["result"]),
        correlation_id=cast(str, row["correlation_id"]),
        actor_type=cast(str | None, row["actor_type"]),
        actor_id=cast(str | None, row["actor_id"]),
        tenant_id=cast(UUID | None, row["tenant_id"]),
        client_id=cast(UUID | None, row["client_id"]),
        resource_type=cast(str | None, row["resource_type"]),
        resource_id=cast(str | None, row["resource_id"]),
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        retention_policy_id=cast(str, row["retention_policy_id"]),
        retention_policy_version=cast(int, row["retention_policy_version"]),
        retain_until=row["retain_until"],
        event_hash=cast(str, row["event_hash"]),
    )


class PostgresImmutableAuditStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def record(self, event: ImmutableAuditEvent) -> None:
        if not verify_event_integrity(event):
            raise ValueError("audit event integrity hash mismatch")
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(audit_events).values(
                    event_id=event.event_id,
                    audit_class=event.audit_class.value,
                    action=event.action,
                    result=event.result,
                    correlation_id=event.correlation_id,
                    actor_type=event.actor_type,
                    actor_id=event.actor_id,
                    tenant_id=event.tenant_id,
                    client_id=event.client_id,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    occurred_at=event.occurred_at,
                    recorded_at=event.recorded_at,
                    retention_policy_id=event.retention_policy_id,
                    retention_policy_version=event.retention_policy_version,
                    retain_until=event.retain_until,
                    event_hash=event.event_hash,
                )
            )

    async def list_owned(
        self,
        *,
        audit_class: AuditClass,
        tenant_id: UUID,
        client_id: UUID,
        limit: int,
    ) -> tuple[ImmutableAuditEvent, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("audit limit must be between 1 and 200")
        statement = (
            sa.select(audit_events)
            .where(
                audit_events.c.audit_class == audit_class.value,
                audit_events.c.tenant_id == tenant_id,
                audit_events.c.client_id == client_id,
            )
            .order_by(
                audit_events.c.occurred_at.desc(),
                audit_events.c.event_id.desc(),
            )
            .limit(limit)
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_from_row(row) for row in rows)

    async def find_integrity_failures(
        self,
        *,
        limit: int,
    ) -> tuple[UUID, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("integrity verification limit must be between 1 and 1000")
        statement = (
            sa.select(audit_events)
            .order_by(
                audit_events.c.occurred_at.desc(),
                audit_events.c.event_id.desc(),
            )
            .limit(limit)
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(
            event.event_id
            for event in (_from_row(row) for row in rows)
            if not verify_event_integrity(event)
        )
