from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from orqetia.tenancy import (
    AdministrativeStatus,
    InMemoryTenantClientRepository,
    InMemoryTenancyAuditSink,
    TenancyAdminService,
)

NOW = datetime(2026, 10, 5, 23, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_create_disable_and_resolve_owner_fail_closed() -> None:
    repository = InMemoryTenantClientRepository()
    audit = InMemoryTenancyAuditSink()
    service = TenancyAdminService(repository=repository, audit=audit)

    tenant = await service.create_tenant(
        display_name="Acme",
        occurred_at=NOW,
    )
    client = await service.create_client(
        tenant_id=tenant.tenant_id,
        display_name="Acme Production",
        occurred_at=NOW,
    )
    owner = await service.resolve_active_owner(
        tenant_id=tenant.tenant_id,
        client_id=client.client_id,
    )
    assert owner.tenant_id == tenant.tenant_id
    assert owner.client_id == client.client_id

    await service.set_tenant_status(
        tenant_id=tenant.tenant_id,
        status=AdministrativeStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="disabled"):
        await service.resolve_active_owner(
            tenant_id=tenant.tenant_id,
            client_id=client.client_id,
        )
    with pytest.raises(ValueError, match="disabled tenant"):
        await service.create_client(
            tenant_id=tenant.tenant_id,
            display_name="Blocked",
            occurred_at=NOW + timedelta(seconds=2),
        )

    assert [event.action for event in audit.events] == [
        "TENANT_CREATE",
        "CLIENT_CREATE",
        "TENANT_DISABLED",
    ]


@pytest.mark.asyncio
async def test_client_cannot_move_between_tenants_and_version_is_optimistic() -> None:
    repository = InMemoryTenantClientRepository()
    service = TenancyAdminService(
        repository=repository,
        audit=InMemoryTenancyAuditSink(),
    )
    first = await service.create_tenant(display_name="One", occurred_at=NOW)
    second = await service.create_tenant(display_name="Two", occurred_at=NOW)
    client = await service.create_client(
        tenant_id=first.tenant_id,
        display_name="Client",
        occurred_at=NOW,
    )

    moved = replace(
        client,
        tenant_id=second.tenant_id,
        version=client.version + 1,
        updated_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="cannot move"):
        await repository.replace_client(moved, expected_version=client.version)

    disabled = await service.set_client_status(
        tenant_id=first.tenant_id,
        client_id=client.client_id,
        status=AdministrativeStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=2),
    )
    stale = replace(
        disabled,
        display_name="stale",
        version=disabled.version + 1,
        updated_at=NOW + timedelta(seconds=3),
    )
    with pytest.raises(ValueError, match="version conflict"):
        await repository.replace_client(stale, expected_version=1)


@pytest.mark.asyncio
async def test_client_activation_requires_active_parent_tenant() -> None:
    repository = InMemoryTenantClientRepository()
    service = TenancyAdminService(
        repository=repository,
        audit=InMemoryTenancyAuditSink(),
    )
    tenant = await service.create_tenant(display_name="Tenant", occurred_at=NOW)
    client = await service.create_client(
        tenant_id=tenant.tenant_id,
        display_name="Client",
        occurred_at=NOW,
    )
    await service.set_client_status(
        tenant_id=tenant.tenant_id,
        client_id=client.client_id,
        status=AdministrativeStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    await service.set_tenant_status(
        tenant_id=tenant.tenant_id,
        status=AdministrativeStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(ValueError, match="disabled tenant"):
        await service.set_client_status(
            tenant_id=tenant.tenant_id,
            client_id=client.client_id,
            status=AdministrativeStatus.ACTIVE,
            occurred_at=NOW + timedelta(seconds=3),
        )


@pytest.mark.asyncio
async def test_owner_mismatch_never_resolves() -> None:
    repository = InMemoryTenantClientRepository()
    service = TenancyAdminService(
        repository=repository,
        audit=InMemoryTenancyAuditSink(),
    )
    tenant = await service.create_tenant(display_name="Tenant", occurred_at=NOW)
    client = await service.create_client(
        tenant_id=tenant.tenant_id,
        display_name="Client",
        occurred_at=NOW,
    )
    with pytest.raises(PermissionError, match="ownership mismatch"):
        await service.resolve_active_owner(
            tenant_id=uuid7(),
            client_id=client.client_id,
        )
