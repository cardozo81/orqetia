from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from orqetia.control_plane import (
    AuthorizedExecutionTarget,
    ExecutionPolicyAdminService,
    InMemoryExecutionPolicyRepository,
)

NOW = datetime(2026, 10, 5, 23, 40, tzinfo=UTC)


def _targets() -> tuple[AuthorizedExecutionTarget, ...]:
    return (
        AuthorizedExecutionTarget("openai", "gpt-x", "medium"),
        AuthorizedExecutionTarget("anthropic", "claude-x", "standard"),
    )


@pytest.mark.asyncio
async def test_publish_creates_immutable_versions_and_moves_assignment() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    repository = InMemoryExecutionPolicyRepository()
    service = ExecutionPolicyAdminService(repository)

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
    assert versions[0].authorized_targets == _targets()
    assert second.assignment.assignment_version == 2
    assert (
        await service.resolve_effective(
            tenant_id=tenant_id,
            client_id=client_id,
        )
    ).version == second.version


@pytest.mark.asyncio
async def test_assignment_version_conflict_fails_closed() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    repository = InMemoryExecutionPolicyRepository()
    service = ExecutionPolicyAdminService(repository)
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
    service = ExecutionPolicyAdminService(InMemoryExecutionPolicyRepository())
    with pytest.raises(LookupError, match="no effective"):
        await service.resolve_effective(
            tenant_id=uuid7(),
            client_id=uuid7(),
        )
