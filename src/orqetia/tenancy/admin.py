"""Administrative tenant/client lifecycle owned by Identity & Tenancy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _label(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError(f"{field} must contain 1..200 characters")
    return normalized


class AdministrativeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class TenantRecord:
    tenant_id: UUID
    display_name: str
    status: AdministrativeStatus
    created_at: datetime
    updated_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_name", _label(self.display_name, "display_name"))
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.version < 1:
            raise ValueError("tenant version must be positive")


@dataclass(frozen=True)
class ServiceClientRecord:
    client_id: UUID
    tenant_id: UUID
    display_name: str
    status: AdministrativeStatus
    created_at: datetime
    updated_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_name", _label(self.display_name, "display_name"))
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.version < 1:
            raise ValueError("client version must be positive")


@dataclass(frozen=True)
class ActiveOwner:
    tenant_id: UUID
    client_id: UUID


@dataclass(frozen=True)
class TenancyAuditEvent:
    action: str
    resource_type: str
    resource_id: UUID
    occurred_at: datetime
    tenant_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.action.strip() or len(self.action) > 100:
            raise ValueError("audit action must contain 1..100 characters")
        if self.resource_type not in {"TENANT", "CLIENT"}:
            raise ValueError("unknown tenancy resource_type")
        _aware(self.occurred_at, "occurred_at")


class TenancyAuditSink(Protocol):
    async def record(self, event: TenancyAuditEvent) -> None: ...


class TenantClientRepository(Protocol):
    async def create_tenant(self, tenant: TenantRecord) -> TenantRecord: ...
    async def get_tenant(self, tenant_id: UUID) -> TenantRecord | None: ...
    async def replace_tenant(
        self,
        tenant: TenantRecord,
        *,
        expected_version: int,
    ) -> TenantRecord: ...
    async def list_tenants(self) -> tuple[TenantRecord, ...]: ...
    async def create_client(self, client: ServiceClientRecord) -> ServiceClientRecord: ...
    async def get_client(self, client_id: UUID) -> ServiceClientRecord | None: ...
    async def replace_client(
        self,
        client: ServiceClientRecord,
        *,
        expected_version: int,
    ) -> ServiceClientRecord: ...
    async def list_clients(
        self,
        *,
        tenant_id: UUID,
    ) -> tuple[ServiceClientRecord, ...]: ...


class InMemoryTenancyAuditSink:
    def __init__(self) -> None:
        self.events: list[TenancyAuditEvent] = []

    async def record(self, event: TenancyAuditEvent) -> None:
        self.events.append(event)


class InMemoryTenantClientRepository:
    def __init__(self) -> None:
        self._tenants: dict[UUID, TenantRecord] = {}
        self._clients: dict[UUID, ServiceClientRecord] = {}

    async def create_tenant(self, tenant: TenantRecord) -> TenantRecord:
        if tenant.tenant_id in self._tenants:
            raise ValueError("tenant already exists")
        self._tenants[tenant.tenant_id] = tenant
        return tenant

    async def get_tenant(self, tenant_id: UUID) -> TenantRecord | None:
        return self._tenants.get(tenant_id)

    async def replace_tenant(
        self,
        tenant: TenantRecord,
        *,
        expected_version: int,
    ) -> TenantRecord:
        current = self._tenants.get(tenant.tenant_id)
        if current is None:
            raise LookupError("tenant not found")
        if current.version != expected_version:
            raise ValueError("tenant version conflict")
        self._tenants[tenant.tenant_id] = tenant
        return tenant

    async def list_tenants(self) -> tuple[TenantRecord, ...]:
        return tuple(
            sorted(self._tenants.values(), key=lambda item: str(item.tenant_id))
        )

    async def create_client(self, client: ServiceClientRecord) -> ServiceClientRecord:
        if client.client_id in self._clients:
            raise ValueError("client already exists")
        self._clients[client.client_id] = client
        return client

    async def get_client(self, client_id: UUID) -> ServiceClientRecord | None:
        return self._clients.get(client_id)

    async def replace_client(
        self,
        client: ServiceClientRecord,
        *,
        expected_version: int,
    ) -> ServiceClientRecord:
        current = self._clients.get(client.client_id)
        if current is None:
            raise LookupError("client not found")
        if current.version != expected_version:
            raise ValueError("client version conflict")
        if current.tenant_id != client.tenant_id:
            raise ValueError("client cannot move between tenants")
        self._clients[client.client_id] = client
        return client

    async def list_clients(
        self,
        *,
        tenant_id: UUID,
    ) -> tuple[ServiceClientRecord, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._clients.values()
                    if item.tenant_id == tenant_id
                ),
                key=lambda item: str(item.client_id),
            )
        )


class TenancyAdminService:
    def __init__(
        self,
        *,
        repository: TenantClientRepository,
        audit: TenancyAuditSink,
    ) -> None:
        self._repository = repository
        self._audit = audit

    async def create_tenant(
        self,
        *,
        display_name: str,
        occurred_at: datetime,
    ) -> TenantRecord:
        _aware(occurred_at, "occurred_at")
        tenant = TenantRecord(
            tenant_id=uuid7(),
            display_name=display_name,
            status=AdministrativeStatus.ACTIVE,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        saved = await self._repository.create_tenant(tenant)
        await self._audit.record(
            TenancyAuditEvent(
                action="TENANT_CREATE",
                resource_type="TENANT",
                resource_id=saved.tenant_id,
                occurred_at=occurred_at,
            )
        )
        return saved

    async def set_tenant_status(
        self,
        *,
        tenant_id: UUID,
        status: AdministrativeStatus,
        occurred_at: datetime,
    ) -> TenantRecord:
        _aware(occurred_at, "occurred_at")
        current = await self._repository.get_tenant(tenant_id)
        if current is None:
            raise LookupError("tenant not found")
        if current.status is status:
            return current
        updated = replace(
            current,
            status=status,
            updated_at=occurred_at,
            version=current.version + 1,
        )
        saved = await self._repository.replace_tenant(
            updated,
            expected_version=current.version,
        )
        await self._audit.record(
            TenancyAuditEvent(
                action=f"TENANT_{status.value}",
                resource_type="TENANT",
                resource_id=saved.tenant_id,
                occurred_at=occurred_at,
            )
        )
        return saved

    async def create_client(
        self,
        *,
        tenant_id: UUID,
        display_name: str,
        occurred_at: datetime,
    ) -> ServiceClientRecord:
        _aware(occurred_at, "occurred_at")
        tenant = await self._repository.get_tenant(tenant_id)
        if tenant is None:
            raise LookupError("tenant not found")
        if tenant.status is not AdministrativeStatus.ACTIVE:
            raise ValueError("cannot create client in disabled tenant")
        client = ServiceClientRecord(
            client_id=uuid7(),
            tenant_id=tenant_id,
            display_name=display_name,
            status=AdministrativeStatus.ACTIVE,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        saved = await self._repository.create_client(client)
        await self._audit.record(
            TenancyAuditEvent(
                action="CLIENT_CREATE",
                resource_type="CLIENT",
                resource_id=saved.client_id,
                tenant_id=saved.tenant_id,
                occurred_at=occurred_at,
            )
        )
        return saved

    async def set_client_status(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        status: AdministrativeStatus,
        occurred_at: datetime,
    ) -> ServiceClientRecord:
        _aware(occurred_at, "occurred_at")
        current = await self._repository.get_client(client_id)
        if current is None or current.tenant_id != tenant_id:
            raise LookupError("owned client not found")
        if status is AdministrativeStatus.ACTIVE:
            tenant = await self._repository.get_tenant(tenant_id)
            if tenant is None or tenant.status is not AdministrativeStatus.ACTIVE:
                raise ValueError("cannot activate client under disabled tenant")
        if current.status is status:
            return current
        updated = replace(
            current,
            status=status,
            updated_at=occurred_at,
            version=current.version + 1,
        )
        saved = await self._repository.replace_client(
            updated,
            expected_version=current.version,
        )
        await self._audit.record(
            TenancyAuditEvent(
                action=f"CLIENT_{status.value}",
                resource_type="CLIENT",
                resource_id=saved.client_id,
                tenant_id=saved.tenant_id,
                occurred_at=occurred_at,
            )
        )
        return saved

    async def require_active_tenant(
        self,
        *,
        tenant_id: UUID,
    ) -> TenantRecord:
        tenant = await self._repository.get_tenant(tenant_id)
        if tenant is None:
            raise PermissionError("tenant ownership unavailable")
        if tenant.status is not AdministrativeStatus.ACTIVE:
            raise PermissionError("tenant is disabled")
        return tenant

    async def resolve_active_owner(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> ActiveOwner:
        client = await self._repository.get_client(client_id)
        if client is None or client.tenant_id != tenant_id:
            raise PermissionError("client ownership mismatch")
        tenant = await self.require_active_tenant(tenant_id=tenant_id)
        if client.status is not AdministrativeStatus.ACTIVE:
            raise PermissionError("tenant/client is disabled")
        return ActiveOwner(tenant_id=tenant_id, client_id=client_id)
