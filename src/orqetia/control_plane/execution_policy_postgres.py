"""PostgreSQL persistence for immutable execution-policy versions."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .execution_policies import (
    AuthorizedExecutionTarget,
    ClientPolicyAssignment,
    EffectiveExecutionPolicy,
    ExecutionPolicyVersion,
)
from .execution_policy_tables import (
    client_policy_assignments,
    execution_policy_versions,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _targets_json(
    targets: tuple[AuthorizedExecutionTarget, ...],
) -> list[dict[str, str]]:
    return [
        {
            "provider_id": target.provider_id,
            "model_id": target.model_id,
            "reasoning_profile": target.reasoning_profile,
        }
        for target in targets
    ]


def _targets_from_json(value: object) -> tuple[AuthorizedExecutionTarget, ...]:
    if not isinstance(value, list):
        raise ValueError("persisted authorized_targets must be an array")
    output: list[AuthorizedExecutionTarget] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("persisted target must be an object")
        output.append(
            AuthorizedExecutionTarget(
                provider_id=str(item["provider_id"]),
                model_id=str(item["model_id"]),
                reasoning_profile=str(item["reasoning_profile"]),
            )
        )
    return tuple(output)


def _version_from_row(row: RowMapping) -> ExecutionPolicyVersion:
    return ExecutionPolicyVersion(
        policy_version_id=cast(UUID, row["policy_version_id"]),
        tenant_id=cast(UUID, row["tenant_id"]),
        client_id=cast(UUID, row["client_id"]),
        version_number=cast(int, row["version_number"]),
        max_cycles=cast(int, row["max_cycles"]),
        max_attempts=cast(int, row["max_attempts"]),
        cycle_delay_seconds=cast(int, row["cycle_delay_seconds"]),
        retry_after_cap_seconds=cast(int, row["retry_after_cap_seconds"]),
        authorized_targets=_targets_from_json(row["authorized_targets"]),
        created_at=row["created_at"],
    )


def _assignment_from_row(row: RowMapping) -> ClientPolicyAssignment:
    return ClientPolicyAssignment(
        tenant_id=cast(UUID, row["tenant_id"]),
        client_id=cast(UUID, row["client_id"]),
        policy_version_id=cast(UUID, row["policy_version_id"]),
        assignment_version=cast(int, row["assignment_version"]),
        assigned_at=row["assigned_at"],
    )


class PostgresExecutionPolicyRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def list_versions(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[ExecutionPolicyVersion, ...]:
        statement = (
            sa.select(execution_policy_versions)
            .where(
                execution_policy_versions.c.tenant_id == tenant_id,
                execution_policy_versions.c.client_id == client_id,
            )
            .order_by(execution_policy_versions.c.version_number)
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_version_from_row(row) for row in rows)

    async def get_effective(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> EffectiveExecutionPolicy | None:
        statement = (
            sa.select(
                client_policy_assignments,
                *execution_policy_versions.c,
            )
            .join(
                execution_policy_versions,
                client_policy_assignments.c.policy_version_id
                == execution_policy_versions.c.policy_version_id,
            )
            .where(
                client_policy_assignments.c.tenant_id == tenant_id,
                client_policy_assignments.c.client_id == client_id,
            )
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        return EffectiveExecutionPolicy(
            version=_version_from_row(row),
            assignment=_assignment_from_row(row),
        )

    async def publish_and_activate(
        self,
        *,
        version: ExecutionPolicyVersion,
        assignment: ClientPolicyAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveExecutionPolicy:
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(execution_policy_versions).values(
                    policy_version_id=version.policy_version_id,
                    tenant_id=version.tenant_id,
                    client_id=version.client_id,
                    version_number=version.version_number,
                    max_cycles=version.max_cycles,
                    max_attempts=version.max_attempts,
                    cycle_delay_seconds=version.cycle_delay_seconds,
                    retry_after_cap_seconds=version.retry_after_cap_seconds,
                    authorized_targets=_targets_json(version.authorized_targets),
                    created_at=version.created_at,
                )
            )
            if expected_assignment_version is None:
                statement = (
                    insert(client_policy_assignments)
                    .values(
                        tenant_id=assignment.tenant_id,
                        client_id=assignment.client_id,
                        policy_version_id=assignment.policy_version_id,
                        assignment_version=assignment.assignment_version,
                        assigned_at=assignment.assigned_at,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            client_policy_assignments.c.tenant_id,
                            client_policy_assignments.c.client_id,
                        ]
                    )
                    .returning(client_policy_assignments.c.assignment_version)
                )
            else:
                statement = (
                    sa.update(client_policy_assignments)
                    .where(
                        client_policy_assignments.c.tenant_id
                        == assignment.tenant_id,
                        client_policy_assignments.c.client_id
                        == assignment.client_id,
                        client_policy_assignments.c.assignment_version
                        == expected_assignment_version,
                    )
                    .values(
                        policy_version_id=assignment.policy_version_id,
                        assignment_version=assignment.assignment_version,
                        assigned_at=assignment.assigned_at,
                    )
                    .returning(client_policy_assignments.c.assignment_version)
                )
            changed = (await database.execute(statement)).scalar_one_or_none()
            if changed is None:
                raise ValueError("policy assignment version conflict")
        return EffectiveExecutionPolicy(version=version, assignment=assignment)
