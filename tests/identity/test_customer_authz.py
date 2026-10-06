from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from orqetia.identity import (
    CustomerAuthorizationService,
    CustomerIdentityStatus,
    CustomerMembershipStatus,
    CustomerRole,
    HumanAuthenticationContext,
    InMemoryCustomerAuthzAuditSink,
    InMemoryCustomerIdentityRepository,
)
from orqetia.tenancy import (
    AdministrativeStatus,
    InMemoryTenancyAuditSink,
    InMemoryTenantClientRepository,
    TenancyAdminService,
)

NOW = datetime(2026, 10, 6, 7, tzinfo=UTC)
ISSUER = "https://idp.example.com"


async def _fixture():
    tenancy_repository = InMemoryTenantClientRepository()
    tenancy = TenancyAdminService(
        repository=tenancy_repository,
        audit=InMemoryTenancyAuditSink(),
    )
    tenant_a = await tenancy.create_tenant(display_name="A", occurred_at=NOW)
    client_a = await tenancy.create_client(
        tenant_id=tenant_a.tenant_id,
        display_name="A1",
        occurred_at=NOW,
    )
    tenant_b = await tenancy.create_tenant(display_name="B", occurred_at=NOW)
    client_b = await tenancy.create_client(
        tenant_id=tenant_b.tenant_id,
        display_name="B1",
        occurred_at=NOW,
    )
    repository = InMemoryCustomerIdentityRepository()
    audit = InMemoryCustomerAuthzAuditSink()
    service = CustomerAuthorizationService(
        repository=repository,
        owner_resolver=tenancy,
        audit=audit,
    )
    return (
        service,
        repository,
        audit,
        tenancy,
        tenant_a,
        client_a,
        tenant_b,
        client_b,
    )


def _context(*, mfa: bool = True, age_minutes: int = 0) -> HumanAuthenticationContext:
    return HumanAuthenticationContext(
        issuer=ISSUER,
        subject="customer-subject",
        authenticated_at=NOW - timedelta(minutes=age_minutes),
        mfa_satisfied=mfa,
        amr=("pwd", "webauthn") if mfa else ("pwd",),
        acr="urn:mfa" if mfa else None,
    )


@pytest.mark.asyncio
async def test_external_identity_is_default_deny_until_membership_is_provisioned() -> None:
    service, _repository, _audit, _tenancy, _ta, client_a, _tb, _cb = await _fixture()
    with pytest.raises(PermissionError, match="not provisioned"):
        await service.authenticate(
            context=_context(),
            tenant_id=client_a.tenant_id,
            client_id=client_a.client_id,
            occurred_at=NOW,
        )


@pytest.mark.asyncio
async def test_one_external_identity_can_have_multiple_explicit_memberships() -> None:
    (
        service,
        _repository,
        audit,
        _tenancy,
        tenant_a,
        client_a,
        tenant_b,
        client_b,
    ) = await _fixture()
    identity = await service.create_identity(
        issuer=ISSUER,
        subject="customer-subject",
        occurred_at=NOW,
    )
    first = await service.add_membership(
        identity_id=identity.identity_id,
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        roles=(CustomerRole.OWNER,),
        occurred_at=NOW,
    )
    second = await service.add_membership(
        identity_id=identity.identity_id,
        tenant_id=tenant_b.tenant_id,
        client_id=client_b.client_id,
        roles=(CustomerRole.ANALYST,),
        occurred_at=NOW,
    )

    available = await service.list_available_memberships(context=_context())
    assert {item.membership_id for item in available} == {
        first.membership_id,
        second.membership_id,
    }
    principal_a = await service.authenticate(
        context=_context(),
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        occurred_at=NOW,
    )
    principal_b = await service.authenticate(
        context=_context(),
        tenant_id=tenant_b.tenant_id,
        client_id=client_b.client_id,
        occurred_at=NOW,
    )
    assert principal_a.roles == (CustomerRole.OWNER,)
    assert principal_b.roles == (CustomerRole.ANALYST,)
    assert principal_a.client_id != principal_b.client_id
    assert [event.action for event in audit.events[:3]] == [
        "IDENTITY_CREATE",
        "MEMBERSHIP_CREATE",
        "MEMBERSHIP_CREATE",
    ]


@pytest.mark.asyncio
async def test_browser_selected_owner_cannot_escape_exact_membership() -> None:
    service, _repository, _audit, _tenancy, tenant_a, client_a, tenant_b, client_b = (
        await _fixture()
    )
    identity = await service.create_identity(
        issuer=ISSUER,
        subject="customer-subject",
        occurred_at=NOW,
    )
    await service.add_membership(
        identity_id=identity.identity_id,
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        roles=(CustomerRole.DEVELOPER,),
        occurred_at=NOW,
    )

    with pytest.raises(PermissionError, match="membership is not provisioned"):
        await service.authenticate(
            context=_context(),
            tenant_id=tenant_b.tenant_id,
            client_id=client_b.client_id,
            occurred_at=NOW,
        )
    with pytest.raises(PermissionError, match="membership is not provisioned"):
        await service.authenticate(
            context=_context(),
            tenant_id=tenant_b.tenant_id,
            client_id=client_a.client_id,
            occurred_at=NOW,
        )


@pytest.mark.asyncio
async def test_disabled_identity_membership_or_owner_blocks_existing_access() -> None:
    service, _repository, _audit, tenancy, tenant_a, client_a, _tb, _cb = await _fixture()
    identity = await service.create_identity(
        issuer=ISSUER,
        subject="customer-subject",
        occurred_at=NOW,
    )
    membership = await service.add_membership(
        identity_id=identity.identity_id,
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        roles=(CustomerRole.OWNER,),
        occurred_at=NOW,
    )

    await service.set_membership_status(
        membership_id=membership.membership_id,
        status=CustomerMembershipStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="membership is disabled"):
        await service.authenticate(
            context=_context(),
            tenant_id=tenant_a.tenant_id,
            client_id=client_a.client_id,
            occurred_at=NOW + timedelta(seconds=2),
        )

    await service.set_membership_status(
        membership_id=membership.membership_id,
        status=CustomerMembershipStatus.ACTIVE,
        occurred_at=NOW + timedelta(seconds=3),
    )
    await service.set_identity_status(
        identity_id=identity.identity_id,
        status=CustomerIdentityStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=4),
    )
    with pytest.raises(PermissionError, match="identity is disabled"):
        await service.authenticate(
            context=_context(),
            tenant_id=tenant_a.tenant_id,
            client_id=client_a.client_id,
            occurred_at=NOW + timedelta(seconds=5),
        )

    await service.set_identity_status(
        identity_id=identity.identity_id,
        status=CustomerIdentityStatus.ACTIVE,
        occurred_at=NOW + timedelta(seconds=6),
    )
    await tenancy.set_client_status(
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        status=AdministrativeStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=7),
    )
    with pytest.raises(PermissionError, match="disabled"):
        await service.authenticate(
            context=_context(),
            tenant_id=tenant_a.tenant_id,
            client_id=client_a.client_id,
            occurred_at=NOW + timedelta(seconds=8),
        )
    assert await service.list_available_memberships(context=_context()) == ()


@pytest.mark.asyncio
async def test_credential_write_requires_recent_mfa_but_read_does_not() -> None:
    service, _repository, _audit, _tenancy, tenant_a, client_a, _tb, _cb = await _fixture()
    identity = await service.create_identity(
        issuer=ISSUER,
        subject="customer-subject",
        occurred_at=NOW,
    )
    await service.add_membership(
        identity_id=identity.identity_id,
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        roles=(CustomerRole.DEVELOPER,),
        occurred_at=NOW,
    )

    no_mfa = await service.authenticate(
        context=_context(mfa=False),
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        occurred_at=NOW,
    )
    service.require_permission(
        principal=no_mfa,
        permission="credentials:read",
        occurred_at=NOW,
    )
    with pytest.raises(PermissionError, match="step-up"):
        service.require_permission(
            principal=no_mfa,
            permission="credentials:write",
            occurred_at=NOW,
        )

    stale = await service.authenticate(
        context=_context(mfa=True, age_minutes=6),
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        occurred_at=NOW,
    )
    with pytest.raises(PermissionError, match="step-up"):
        service.require_permission(
            principal=stale,
            permission="credentials:write",
            occurred_at=NOW,
        )

    fresh = await service.authenticate(
        context=_context(mfa=True),
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        occurred_at=NOW,
    )
    service.require_permission(
        principal=fresh,
        permission="credentials:write",
        occurred_at=NOW + timedelta(minutes=1),
    )


@pytest.mark.asyncio
async def test_identity_membership_uniqueness_and_optimistic_versions_fail_closed() -> None:
    service, repository, _audit, _tenancy, tenant_a, client_a, _tb, _cb = await _fixture()
    identity = await service.create_identity(
        issuer=ISSUER,
        subject="customer-subject",
        occurred_at=NOW,
    )
    with pytest.raises(ValueError, match="external identity already exists"):
        await service.create_identity(
            issuer=ISSUER,
            subject="customer-subject",
            occurred_at=NOW,
        )
    membership = await service.add_membership(
        identity_id=identity.identity_id,
        tenant_id=tenant_a.tenant_id,
        client_id=client_a.client_id,
        roles=(CustomerRole.VIEWER,),
        occurred_at=NOW,
    )
    with pytest.raises(ValueError, match="membership already exists"):
        await service.add_membership(
            identity_id=identity.identity_id,
            tenant_id=tenant_a.tenant_id,
            client_id=client_a.client_id,
            roles=(CustomerRole.OWNER,),
            occurred_at=NOW,
        )

    updated = await service.set_membership_roles(
        membership_id=membership.membership_id,
        roles=(CustomerRole.ANALYST,),
        occurred_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="version conflict"):
        await repository.replace_membership(
            replace(
                membership,
                roles=(CustomerRole.OWNER,),
                version=membership.version + 1,
                updated_at=NOW + timedelta(seconds=2),
            ),
            expected_version=membership.version,
        )
    assert updated.version == 2
