"""Client-owned Portal activity audit contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid7


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")


@dataclass(frozen=True)
class CustomerActivityEvent:
    event_id: UUID
    tenant_id: UUID
    client_id: UUID
    identity_id: UUID
    membership_id: UUID
    action: str
    result: str
    occurred_at: datetime
    correlation_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None

    def __post_init__(self) -> None:
        if not self.action.strip() or len(self.action) > 100:
            raise ValueError("activity action must contain 1..100 characters")
        if self.result not in {"SUCCESS", "DENIED"}:
            raise ValueError("activity result must be SUCCESS or DENIED")
        if self.correlation_id is not None and (
            not self.correlation_id.strip() or len(self.correlation_id) > 200
        ):
            raise ValueError("correlation_id must contain 1..200 characters")
        for field, value, maximum in (
            ("resource_type", self.resource_type, 100),
            ("resource_id", self.resource_id, 500),
        ):
            if value is not None and (
                not value.strip() or len(value) > maximum
            ):
                raise ValueError(
                    f"{field} must contain 1..{maximum} characters"
                )
        _aware(self.occurred_at)


class CustomerActivityStore(Protocol):
    async def record(self, event: CustomerActivityEvent) -> None: ...

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        limit: int,
    ) -> tuple[CustomerActivityEvent, ...]: ...


class InMemoryCustomerActivityStore:
    def __init__(self) -> None:
        self._items: list[CustomerActivityEvent] = []

    async def record(self, event: CustomerActivityEvent) -> None:
        self._items.append(event)

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        limit: int,
    ) -> tuple[CustomerActivityEvent, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("activity limit must be between 1 and 200")
        items = [
            item
            for item in self._items
            if item.tenant_id == tenant_id and item.client_id == client_id
        ]
        items.sort(
            key=lambda item: (item.occurred_at, str(item.event_id)),
            reverse=True,
        )
        return tuple(items[:limit])


def activity_event(
    *,
    tenant_id: UUID,
    client_id: UUID,
    identity_id: UUID,
    membership_id: UUID,
    action: str,
    occurred_at: datetime,
    correlation_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> CustomerActivityEvent:
    return CustomerActivityEvent(
        event_id=uuid7(),
        tenant_id=tenant_id,
        client_id=client_id,
        identity_id=identity_id,
        membership_id=membership_id,
        action=action,
        result="SUCCESS",
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
