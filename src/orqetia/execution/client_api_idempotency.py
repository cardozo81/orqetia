"""Idempotency journal for client-facing session/task/cancel operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .sessions import OwnershipScope


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def idempotency_key_hash(value: str) -> str:
    if not value.strip() or len(value.encode("utf-8")) > 200:
        raise ValueError("idempotency key must contain 1..200 UTF-8 bytes")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClientApiIdempotencyReservation:
    scope: OwnershipScope
    operation: str
    key_hash: str
    request_fingerprint: str
    resource_id: UUID
    created_at: datetime
    replayed: bool = False

    def __post_init__(self) -> None:
        if not self.operation.strip() or len(self.operation) > 100:
            raise ValueError("idempotency operation must contain 1..100 characters")
        for field, value in (
            ("key_hash", self.key_hash),
            ("request_fingerprint", self.request_fingerprint),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{field} must be lowercase SHA-256 hex")
        _aware(self.created_at, "created_at")


class ClientApiIdempotencyConflict(ValueError):
    """A key was reused for a semantically different request."""


class ClientApiIdempotencyJournal(Protocol):
    async def reserve(
        self,
        *,
        scope: OwnershipScope,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        resource_id: UUID,
        occurred_at: datetime,
    ) -> ClientApiIdempotencyReservation: ...


class InMemoryClientApiIdempotencyJournal:
    def __init__(self) -> None:
        self._items: dict[
            tuple[UUID, UUID, str, str],
            ClientApiIdempotencyReservation,
        ] = {}

    async def reserve(
        self,
        *,
        scope: OwnershipScope,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        resource_id: UUID,
        occurred_at: datetime,
    ) -> ClientApiIdempotencyReservation:
        key_hash = idempotency_key_hash(idempotency_key)
        identity = (
            scope.tenant_id,
            scope.client_id,
            operation,
            key_hash,
        )
        current = self._items.get(identity)
        if current is not None:
            if current.request_fingerprint != request_fingerprint:
                raise ClientApiIdempotencyConflict(
                    "idempotency key reused with different request"
                )
            return ClientApiIdempotencyReservation(
                scope=current.scope,
                operation=current.operation,
                key_hash=current.key_hash,
                request_fingerprint=current.request_fingerprint,
                resource_id=current.resource_id,
                created_at=current.created_at,
                replayed=True,
            )

        reservation = ClientApiIdempotencyReservation(
            scope=scope,
            operation=operation,
            key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            resource_id=resource_id,
            created_at=occurred_at,
        )
        self._items[identity] = reservation
        return reservation
