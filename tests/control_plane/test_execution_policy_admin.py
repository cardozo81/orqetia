from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from orqetia.control_plane import (
    AuthorizedExecutionTarget,
    ExecutionPolicyAdminService,
    InMemoryExecutionPolicyRepository,
)
from orqetia.tenancy import (
    InMemoryTenantClientRepository,
    InMemoryTenancyAuditSink,
    TenancyAdminService,
)

NOW = datetime(2026, 10, 5, 23, 40, tzinfo=UTC)


async def _owner() -> tuple[TenancyAdminService, object, object]:
    service = TenancyAdminService(
        repository=InMemoryTenantClientRepository(),
        audit=InMemoryTenancyAuditSink(),
    )
    tenant = await service.create_tenant(display_name="Tenant", occurred_at=NOW)
    client = await service.create_client(
        tenant_id=tenant.tenant_id,
        display_name="Client",
        occurred_at=NOW,
    )
    return service, tenant, client


def _targets() -> tuple[AuthorizedExecutionTarget, ...]:
    return (
        AuthorizedExecutionTarget("openai", "gpt-x", "medium"),
        AuthorizedExecutionTarget("anthropic", "claude-x", "standard"),
    )


@pytest.mark.asyncio
async def test_publish_creates_immutable_versions_and_moves_assignment() -> None:
    owner, tenant, client = await _owner()
    tenant_id, client_id = tenant.tenant_id, client.client_id
    repository = InMemoryExecutionPolicyRepository()
    service = ExecutionPolicyAdminService(repository, owner_resolver=owner)

    first = await service.publish_and_activate(
        tenant_id=tenant_id,
        client_id=client_id,
        max_cycles=2,
        max_attempts=4,
        cycle_delay_seconds=1,
        retry_after_cap_seconds=120,
        authorized_targets=_targets(),
        occurred_at=NOW,
    )
    second = await service.publish_and_activate(
        tenant_id=tenant_id,
        client_id=client_id,
        max_cycles=3,
        max_attempts=6,
        cycle_delay_seconds=2,
        retry_after_cap_seconds=180,
        authorized_targets=(_targets()[0],),
        occurred_at=NOW + timedelta(seconds=1),
    )

    versions = await repository.list_versions(
        tenant_id=tenant_id,
        client_id=client_id,
    )
    assert [item.version_number for item in versions] == [1, 2]
    assert versions[0] == first.version
    assert versions[0].authorized_targets == tuple(sorted(_targets()))
    assert second.assignment.assignment_version == 2
    assert (
        await service.resolve_effective(
            tenant_id=tenant_id,
            client_id=client_id,
        )
    ).version == second.version


@pytest.mark.asyncio
async def test_assignment_version_conflict_fails_closed() -> None:
    owner, tenant, client = await _owner()
    tenant_id, client_id = tenant.tenant_id, client.client_id
    repository = InMemoryExecutionPolicyRepository()
    service = ExecutionPolicyAdminService(repository, owner_resolver=owner)
    effective = await service.publish_and_activate(
        tenant_id=tenant_id,
        client_id=client_id,
        max_cycles=2,
        max_attempts=4,
        cycle_delay_seconds=0,
        retry_after_cap_seconds=60,
        authorized_targets=(_targets()[0],),
        occurred_at=NOW,
    )

    from orqetia.control_plane.execution_policies import (
        ClientPolicyAssignment,
        ExecutionPolicyVersion,
    )

    conflicting_version = ExecutionPolicyVersion(
        policy_version_id=uuid7(),
        tenant_id=tenant_id,
        client_id=client_id,
        version_number=2,
        max_cycles=2,
        max_attempts=4,
        cycle_delay_seconds=0,
        retry_after_cap_seconds=60,
        authorized_targets=(_targets()[0],),
        created_at=NOW + timedelta(seconds=1),
    )
    assignment = ClientPolicyAssignment(
        tenant_id=tenant_id,
        client_id=client_id,
        policy_version_id=conflicting_version.policy_version_id,
        assignment_version=2,
        assigned_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="version conflict"):
        await repository.publish_and_activate(
            version=conflicting_version,
            assignment=assignment,
            expected_assignment_version=effective.assignment.assignment_version + 99,
        )


def test_policy_rejects_duplicate_targets_and_invalid_limits() -> None:
    from orqetia.control_plane.execution_policies import ExecutionPolicyVersion

    target = _targets()[0]
    with pytest.raises(ValueError, match="duplicates"):
        ExecutionPolicyVersion(
            policy_version_id=uuid7(),
            tenant_id=uuid7(),
            client_id=uuid7(),
            version_number=1,
            max_cycles=1,
            max_attempts=1,
            cycle_delay_seconds=0,
            retry_after_cap_seconds=60,
            authorized_targets=(target, target),
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="between 0 and 300"):
        ExecutionPolicyVersion(
            policy_version_id=uuid7(),
            tenant_id=uuid7(),
            client_id=uuid7(),
            version_number=1,
            max_cycles=1,
            max_attempts=1,
            cycle_delay_seconds=0,
            retry_after_cap_seconds=301,
            authorized_targets=(target,),
            created_at=NOW,
        )


@pytest.mark.asyncio
async def test_owner_has_no_implicit_policy_fallback() -> None:
    owner, tenant, client = await _owner()
    service = ExecutionPolicyAdminService(
        InMemoryExecutionPolicyRepository(),
        owner_resolver=owner,
    )
    with pytest.raises(LookupError, match="no effective"):
        await service.resolve_effective(
            tenant_id=tenant.tenant_id,
            client_id=client.client_id,
        )



@pytest.mark.asyncio
async def test_policy_owner_must_be_active_and_match_tenant() -> None:
    owner, tenant, client = await _owner()
    service = ExecutionPolicyAdminService(
        InMemoryExecutionPolicyRepository(),
        owner_resolver=owner,
    )
    with pytest.raises(PermissionError, match="ownership mismatch"):
        await service.publish_and_activate(
            tenant_id=uuid7(),
            client_id=client.client_id,
            max_cycles=2,
            max_attempts=4,
            cycle_delay_seconds=0,
            retry_after_cap_seconds=60,
            authorized_targets=(_targets()[0],),
            occurred_at=NOW,
        )


@pytest.mark.asyncio
async def test_effective_policy_materializes_canonical_execution_contracts() -> None:
    owner, tenant, client = await _owner()
    service = ExecutionPolicyAdminService(
        InMemoryExecutionPolicyRepository(),
        owner_resolver=owner,
    )
    effective = await service.publish_and_activate(
        tenant_id=tenant.tenant_id,
        client_id=client.client_id,
        max_cycles=4,
        max_attempts=7,
        cycle_delay_seconds=3,
        retry_after_cap_seconds=90,
        authorized_targets=_targets(),
        occurred_at=NOW,
    )
    policy = effective.version.orchestration_policy()
    snapshot = effective.version.session_policy_snapshot()
    assert policy.max_cycles == 4
    assert policy.max_attempts == 7
    assert snapshot.effective_policy_version_id == effective.version.policy_version_id
    assert {
        (target.provider_id, target.model_id, target.reasoning_profile)
        for target in snapshot.authorized_targets
    } == {
        ("openai", "gpt-x", "medium"),
        ("anthropic", "claude-x", "standard"),
    }
