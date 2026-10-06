"""PostgreSQL repository for Identity & Tenancy administrative records."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .admin import AdministrativeStatus, ServiceClientRecord, TenantRecord
from .tables import service_clients, tenants

SessionFactory = async_sessionmaker[AsyncSession]


def _tenant_from_row(row: RowMapping) -> TenantRecord:
    return TenantRecord(
        tenant_id=cast(UUID, row["tenant_id"]),
        display_name=cast(str, row["display_name"]),
        status=AdministrativeStatus(cast(str, row["status"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=cast(int, row["version"]),
    )


def _client_from_row(row: RowMapping) -> ServiceClientRecord:
    return ServiceClientRecord(
        client_id=cast(UUID, row["client_id"]),
        tenant_id=cast(UUID, row["tenant_id"]),
        display_name=cast(str, row["display_name"]),
        status=AdministrativeStatus(cast(str, row["status"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=cast(int, row["version"]),
    )


class PostgresTenantClientRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def create_tenant(self, tenant: TenantRecord) -> TenantRecord:
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(tenants).values(
                    tenant_id=tenant.tenant_id,
                    display_name=tenant.display_name,
                    status=tenant.status.value,
                    created_at=tenant.created_at,
                    updated_at=tenant.updated_at,
                    version=tenant.version,
                )
            )
        return tenant

    async def get_tenant(self, tenant_id: UUID) -> TenantRecord | None:
        statement = sa.select(tenants).where(tenants.c.tenant_id == tenant_id)
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _tenant_from_row(row)

    async def replace_tenant(
        self,
        tenant: TenantRecord,
        *,
        expected_version: int,
    ) -> TenantRecord:
        statement = (
            sa.update(tenants)
            .where(
                tenants.c.tenant_id == tenant.tenant_id,
                tenants.c.version == expected_version,
            )
            .values(
                display_name=tenant.display_name,
                status=tenant.status.value,
                updated_at=tenant.updated_at,
                version=tenant.version,
            )
            .returning(tenants.c.tenant_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise ValueError("tenant version conflict or missing row")
        return tenant

    async def list_tenants(self) -> tuple[TenantRecord, ...]:
        statement = sa.select(tenants).order_by(tenants.c.tenant_id)
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_tenant_from_row(row) for row in rows)

    async def create_client(
        self,
        client: ServiceClientRecord,
    ) -> ServiceClientRecord:
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(service_clients).values(
                    client_id=client.client_id,
                    tenant_id=client.tenant_id,
                    display_name=client.display_name,
                    status=client.status.value,
                    created_at=client.created_at,
                    updated_at=client.updated_at,
                    version=client.version,
                )
            )
        return client

    async def get_client(self, client_id: UUID) -> ServiceClientRecord | None:
        statement = sa.select(service_clients).where(
            service_clients.c.client_id == client_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _client_from_row(row)

    async def replace_client(
        self,
        client: ServiceClientRecord,
        *,
        expected_version: int,
    ) -> ServiceClientRecord:
        statement = (
            sa.update(service_clients)
            .where(
                service_clients.c.client_id == client.client_id,
                service_clients.c.tenant_id == client.tenant_id,
                service_clients.c.version == expected_version,
            )
            .values(
                display_name=client.display_name,
                status=client.status.value,
                updated_at=client.updated_at,
                version=client.version,
            )
            .returning(service_clients.c.client_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise ValueError("client version conflict or ownership mismatch")
        return client

    async def list_clients(
        self,
        *,
        tenant_id: UUID,
    ) -> tuple[ServiceClientRecord, ...]:
        statement = (
            sa.select(service_clients)
            .where(service_clients.c.tenant_id == tenant_id)
            .order_by(service_clients.c.client_id)
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_client_from_row(row) for row in rows)
