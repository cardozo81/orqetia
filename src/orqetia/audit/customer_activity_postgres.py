"""PostgreSQL Customer Portal activity audit store."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .customer_activity import CustomerActivityEvent
from .customer_activity_tables import customer_activity_events

SessionFactory = async_sessionmaker[AsyncSession]


def _from_row(row: RowMapping) -> CustomerActivityEvent:
    return CustomerActivityEvent(
        event_id=cast(UUID, row["event_id"]),
        tenant_id=cast(UUID, row["tenant_id"]),
        client_id=cast(UUID, row["client_id"]),
        identity_id=cast(UUID, row["identity_id"]),
        membership_id=cast(UUID, row["membership_id"]),
        action=cast(str, row["action"]),
        result=cast(str, row["result"]),
        resource_type=cast(str | None, row["resource_type"]),
        resource_id=cast(str | None, row["resource_id"]),
        occurred_at=row["occurred_at"],
    )


class PostgresCustomerActivityStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def record(self, event: CustomerActivityEvent) -> None:
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(customer_activity_events).values(
                    event_id=event.event_id,
                    tenant_id=event.tenant_id,
                    client_id=event.client_id,
                    identity_id=event.identity_id,
                    membership_id=event.membership_id,
                    action=event.action,
                    result=event.result,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    occurred_at=event.occurred_at,
                )
            )

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        limit: int,
    ) -> tuple[CustomerActivityEvent, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("activity limit must be between 1 and 200")
        statement = (
            sa.select(customer_activity_events)
            .where(
                customer_activity_events.c.tenant_id == tenant_id,
                customer_activity_events.c.client_id == client_id,
            )
            .order_by(
                customer_activity_events.c.occurred_at.desc(),
                customer_activity_events.c.event_id.desc(),
            )
            .limit(limit)
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_from_row(row) for row in rows)
