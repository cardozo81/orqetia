"""PostgreSQL idempotency journal for client API operations."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .client_api_idempotency import (
    ClientApiIdempotencyConflict,
    ClientApiIdempotencyReservation,
    idempotency_key_hash,
)
from .client_api_tables import client_api_idempotency
from .sessions import OwnershipScope

SessionFactory = async_sessionmaker[AsyncSession]


class PostgresClientApiIdempotencyJournal:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

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
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        key_hash = idempotency_key_hash(idempotency_key)
        values = {
            "tenant_id": scope.tenant_id,
            "client_id": scope.client_id,
            "operation": operation,
            "key_hash": key_hash,
            "request_fingerprint": request_fingerprint,
            "resource_id": resource_id,
            "created_at": occurred_at,
        }
        statement = (
            insert(client_api_idempotency)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    client_api_idempotency.c.tenant_id,
                    client_api_idempotency.c.client_id,
                    client_api_idempotency.c.operation,
                    client_api_idempotency.c.key_hash,
                ]
            )
            .returning(client_api_idempotency.c.resource_id)
        )
        lookup = sa.select(client_api_idempotency).where(
            client_api_idempotency.c.tenant_id == scope.tenant_id,
            client_api_idempotency.c.client_id == scope.client_id,
            client_api_idempotency.c.operation == operation,
            client_api_idempotency.c.key_hash == key_hash,
        )
        async with self._sessions.begin() as database:
            inserted_resource = (await database.execute(statement)).scalar_one_or_none()
            if inserted_resource is not None:
                return ClientApiIdempotencyReservation(
                    scope=scope,
                    operation=operation,
                    key_hash=key_hash,
                    request_fingerprint=request_fingerprint,
                    resource_id=cast(UUID, inserted_resource),
                    created_at=occurred_at,
                )
            row = (await database.execute(lookup)).mappings().one()

        if cast(str, row["request_fingerprint"]) != request_fingerprint:
            raise ClientApiIdempotencyConflict(
                "idempotency key reused with different request"
            )
        return ClientApiIdempotencyReservation(
            scope=scope,
            operation=operation,
            key_hash=key_hash,
            request_fingerprint=cast(str, row["request_fingerprint"]),
            resource_id=cast(UUID, row["resource_id"]),
            created_at=row["created_at"],
            replayed=True,
        )
