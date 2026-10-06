"""PostgreSQL Customer Portal activity adapter over the immutable audit ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .customer_activity import CustomerActivityEvent
from .immutable import AuditClass, build_immutable_audit_event
from .immutable_postgres import PostgresImmutableAuditStore

SessionFactory = async_sessionmaker[AsyncSession]

_ACTOR_TYPE = "CUSTOMER_HUMAN_MEMBERSHIP"


def _actor_id(identity_id: UUID, membership_id: UUID) -> str:
    return f"{identity_id}:{membership_id}"


def _actor_parts(value: str | None) -> tuple[UUID, UUID]:
    if value is None:
        raise ValueError("customer activity actor_id is missing")
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise ValueError("customer activity actor_id provenance is invalid")
    return UUID(parts[0]), UUID(parts[1])


class PostgresCustomerActivityStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._ledger = PostgresImmutableAuditStore(session_factory)

    async def record(self, event: CustomerActivityEvent) -> None:
        correlation_id = event.correlation_id or str(event.event_id)
        immutable = build_immutable_audit_event(
            event_id=event.event_id,
            audit_class=AuditClass.CLIENT_ACTIVITY,
            action=event.action,
            result=event.result,
            correlation_id=correlation_id,
            occurred_at=event.occurred_at,
            recorded_at=max(datetime.now(UTC), event.occurred_at),
            actor_type=_ACTOR_TYPE,
            actor_id=_actor_id(event.identity_id, event.membership_id),
            tenant_id=event.tenant_id,
            client_id=event.client_id,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
        )
        await self._ledger.record(immutable)

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        limit: int,
    ) -> tuple[CustomerActivityEvent, ...]:
        events = await self._ledger.list_owned(
            audit_class=AuditClass.CLIENT_ACTIVITY,
            tenant_id=tenant_id,
            client_id=client_id,
            limit=limit,
        )
        output: list[CustomerActivityEvent] = []
        for item in events:
            if item.actor_type != _ACTOR_TYPE:
                continue
            identity_id, membership_id = _actor_parts(item.actor_id)
            output.append(
                CustomerActivityEvent(
                    event_id=item.event_id,
                    tenant_id=tenant_id,
                    client_id=client_id,
                    identity_id=identity_id,
                    membership_id=membership_id,
                    action=item.action,
                    result=item.result,
                    occurred_at=item.occurred_at,
                    correlation_id=item.correlation_id,
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                )
            )
        return tuple(output)
