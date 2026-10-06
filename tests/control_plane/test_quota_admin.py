from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from orqetia.control_plane import (
    InMemoryQuotaPolicyRepository,
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicyAdminService,
    QuotaPolicyKey,
    QuotaScope,
)
from orqetia.tenancy import (
    AdministrativeStatus,
    InMemoryTenantClientRepository,
    InMemoryTenancyAuditSink,
    TenancyAdminService,
)

NOW = datetime(2026, 10, 6, 0, 10, tzinfo=UTC)


async def _fixture():
    tenancy = TenancyAdminService(
        repository=InMemoryTenantClientRepository(),
        audit=InMemoryTenancyAuditSink(),
    )
    tenant = await tenancy.create_tenant(display_name="Tenant", occurred_at=NOW)
    client = await tenancy.create_client(
        tenant_id=tenant.tenant_id,
        display_name="Client",
        occurred_at=NOW,
    )
    service = QuotaPolicyAdminService(
        repository=InMemoryQuotaPolicyRepository(),
        owner_resolver=tenancy,
    )
    return tenancy, tenant, client, service


@pytest.mark.asyncio
async def test_publish_and_resolve_client_quota_versions() -> None:
    _tenancy, tenant, client, service = await _fixture()
    first = await service.publish(
        scope=QuotaScope.CLIENT,
        tenant_id=tenant.tenant_id,
        client_id=client.client_id,
        metric=QuotaMetric.TOKENS,
        limit=Decimal("1000"),
        burst=Decimal("100"),
        period_seconds=3600,
        enforcement=QuotaEnforcementMode.HARD,
        effective_from=NOW,
    )
    second = await service.publish(
        scope=QuotaScope.CLIENT,
        tenant_id=tenant.tenant_id,
        client_id=client.client_id,
        metric=QuotaMetric.TOKENS,
        limit=Decimal("2000"),
        burst=Decimal("200"),
        period_seconds=3600,
        enforcement=QuotaEnforcementMode.HARD,
        effective_from=NOW + timedelta(hours=1),
        policy_id=first.policy_id,
    )
    assert first.version == 1
    assert second.version == 2

    before = await service.resolve_effective(
        key=QuotaPolicyKey(
            scope=QuotaScope.CLIENT,
            tenant_id=tenant.tenant_id,
            client_id=client.client_id,
            metric=QuotaMetric.TOKENS,
        ),
        occurred_at=NOW + timedelta(minutes=30),
    )
    after = await service.resolve_effective(
        key=QuotaPolicyKey(
            scope=QuotaScope.CLIENT,
            tenant_id=tenant.tenant_id,
            client_id=client.client_id,
            metric=QuotaMetric.TOKENS,
        ),
        occurred_at=NOW + timedelta(hours=2),
    )
    assert before.limit == Decimal("1000")
    assert after.limit == Decimal("2000")


@pytest.mark.asyncio
async def test_provider_quota_never_grants_or_changes_owner() -> None:
    _tenancy, tenant, client, service = await _fixture()
    first = await service.publish(
        scope=QuotaScope.CLIENT,
        tenant_id=tenant.tenant_id,
        client_id=client.client_id,
        metric=QuotaMetric.PROVIDER_REQUESTS,
        provider_id="openai",
        limit=Decimal("10"),
        period_seconds=60,
        enforcement=QuotaEnforcementMode.HARD,
        effective_from=NOW,
    )
    with pytest.raises(ValueError, match="semantic key is immutable"):
        await service.publish(
            scope=QuotaScope.CLIENT,
            tenant_id=tenant.tenant_id,
            client_id=client.client_id,
            metric=QuotaMetric.PROVIDER_REQUESTS,
            provider_id="anthropic",
            limit=Decimal("10"),
            period_seconds=60,
            enforcement=QuotaEnforcementMode.HARD,
            effective_from=NOW + timedelta(minutes=1),
            policy_id=first.policy_id,
        )


@pytest.mark.asyncio
async def test_disabled_or_cross_tenant_owner_fails_closed() -> None:
    tenancy, tenant, client, service = await _fixture()
    other = await tenancy.create_tenant(display_name="Other", occurred_at=NOW)
    with pytest.raises(PermissionError, match="ownership mismatch"):
        await service.publish(
            scope=QuotaScope.CLIENT,
            tenant_id=other.tenant_id,
            client_id=client.client_id,
            metric=QuotaMetric.REQUESTS,
            limit=Decimal("5"),
            period_seconds=60,
            enforcement=QuotaEnforcementMode.HARD,
            effective_from=NOW,
        )

    await tenancy.set_client_status(
        tenant_id=tenant.tenant_id,
        client_id=client.client_id,
        status=AdministrativeStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="disabled"):
        await service.publish(
            scope=QuotaScope.CLIENT,
            tenant_id=tenant.tenant_id,
            client_id=client.client_id,
            metric=QuotaMetric.REQUESTS,
            limit=Decimal("5"),
            period_seconds=60,
            enforcement=QuotaEnforcementMode.HARD,
            effective_from=NOW + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_tenant_quota_requires_active_tenant() -> None:
    tenancy, tenant, _client, service = await _fixture()
    policy = await service.publish(
        scope=QuotaScope.TENANT,
        tenant_id=tenant.tenant_id,
        client_id=None,
        metric=QuotaMetric.REQUESTS,
        limit=Decimal("100"),
        period_seconds=60,
        enforcement=QuotaEnforcementMode.SOFT,
        effective_from=NOW,
    )
    assert policy.scope is QuotaScope.TENANT

    await tenancy.set_tenant_status(
        tenant_id=tenant.tenant_id,
        status=AdministrativeStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="disabled"):
        await service.resolve_effective(
            key=QuotaPolicyKey(
                scope=QuotaScope.TENANT,
                tenant_id=tenant.tenant_id,
                client_id=None,
                metric=QuotaMetric.REQUESTS,
            ),
            occurred_at=NOW + timedelta(seconds=2),
        )


def test_existing_quota_domain_validation_remains_canonical() -> None:
    from orqetia.control_plane import QuotaPolicySnapshot
    from uuid import uuid7

    with pytest.raises(ValueError, match="NATIVE_UNITS quota requires"):
        QuotaPolicySnapshot(
            policy_id=uuid7(),
            version=1,
            scope=QuotaScope.TENANT,
            tenant_id=uuid7(),
            metric=QuotaMetric.NATIVE_UNITS,
            limit=Decimal("1"),
            enforcement=QuotaEnforcementMode.HARD,
            effective_from=NOW,
            period_seconds=60,
        )
