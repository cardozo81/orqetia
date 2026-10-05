"""PostgreSQL persistence adapter for durable execution sessions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .sessions import (
    ExecutionSession,
    ExecutionTargetSnapshot,
    OwnershipScope,
    QuarantineState,
    SessionPolicySnapshot,
    SessionStatus,
    TargetHealth,
    TargetRuntimeState,
)
from .tables import execution_sessions, session_target_runtime

SessionFactory = async_sessionmaker[AsyncSession]


def _target_json(target: ExecutionTargetSnapshot) -> dict[str, str]:
    return {
        "provider_id": target.provider_id,
        "model_id": target.model_id,
        "reasoning_profile": target.reasoning_profile,
    }


def _target_from_json(value: object) -> ExecutionTargetSnapshot:
    if not isinstance(value, dict):
        raise ValueError("persisted authorized target must be an object")
    data = cast(dict[str, object], value)
    return ExecutionTargetSnapshot(
        provider_id=str(data["provider_id"]),
        model_id=str(data["model_id"]),
        reasoning_profile=str(data["reasoning_profile"]),
    )


def _target_tuple(value: object) -> tuple[ExecutionTargetSnapshot, ...]:
    if not isinstance(value, list):
        raise ValueError("persisted authorized_targets must be an array")
    return tuple(_target_from_json(item) for item in value)


def _uuid_tuple(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, list):
        raise ValueError("persisted reference list must be an array")
    return tuple(UUID(str(item)) for item in value)


def _runtime_values(
    *,
    scope: OwnershipScope,
    session_id: UUID,
    state: TargetRuntimeState,
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "tenant_id": scope.tenant_id,
        "client_id": scope.client_id,
        "provider_id": state.target.provider_id,
        "model_id": state.target.model_id,
        "reasoning_profile": state.target.reasoning_profile,
        "health_state": state.health.value,
        "quarantine_state": state.quarantine.value,
        "quarantine_until": state.quarantine_until,
        "quarantine_reason_code": state.quarantine_reason_code,
        "updated_at": state.updated_at,
    }


def _runtime_from_row(row: RowMapping) -> TargetRuntimeState:
    return TargetRuntimeState(
        target=ExecutionTargetSnapshot(
            provider_id=cast(str, row["provider_id"]),
            model_id=cast(str, row["model_id"]),
            reasoning_profile=cast(str, row["reasoning_profile"]),
        ),
        health=TargetHealth(cast(str, row["health_state"])),
        quarantine=QuarantineState(cast(str, row["quarantine_state"])),
        quarantine_until=cast(datetime | None, row["quarantine_until"]),
        quarantine_reason_code=cast(str | None, row["quarantine_reason_code"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


def _session_from_rows(
    row: RowMapping,
    runtime_rows: Sequence[RowMapping],
) -> ExecutionSession:
    return ExecutionSession(
        session_id=cast(UUID, row["session_id"]),
        ownership=OwnershipScope(
            tenant_id=cast(UUID, row["tenant_id"]),
            client_id=cast(UUID, row["client_id"]),
        ),
        status=SessionStatus(cast(str, row["status"])),
        policy=SessionPolicySnapshot(
            requested_policy_version_id=cast(UUID | None, row["requested_policy_version_id"]),
            effective_policy_version_id=cast(UUID, row["effective_policy_version_id"]),
            authorized_targets=_target_tuple(row["authorized_targets"]),
        ),
        external_reference=cast(str | None, row["external_reference"]),
        usage_reference_ids=_uuid_tuple(row["usage_reference_ids"]),
        internal_cost_reference_ids=_uuid_tuple(row["internal_cost_reference_ids"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        expires_at=cast(datetime | None, row["expires_at"]),
        terminal_at=cast(datetime | None, row["terminal_at"]),
        target_runtime=tuple(_runtime_from_row(item) for item in runtime_rows),
        version=cast(int, row["version"]),
    )


class PostgresExecutionSessionStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def create(self, session: ExecutionSession) -> None:
        values = {
            "session_id": session.session_id,
            "tenant_id": session.ownership.tenant_id,
            "client_id": session.ownership.client_id,
            "status": session.status.value,
            "requested_policy_version_id": session.policy.requested_policy_version_id,
            "effective_policy_version_id": session.policy.effective_policy_version_id,
            "authorized_targets": [_target_json(item) for item in session.policy.authorized_targets],
            "external_reference": session.external_reference,
            "usage_reference_ids": [str(item) for item in session.usage_reference_ids],
            "internal_cost_reference_ids": [
                str(item) for item in session.internal_cost_reference_ids
            ],
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "expires_at": session.expires_at,
            "terminal_at": session.terminal_at,
            "version": session.version,
        }

        async with self._sessions.begin() as database:
            await database.execute(sa.insert(execution_sessions).values(**values))
            if session.target_runtime:
                await database.execute(
                    sa.insert(session_target_runtime).values(
                        [
                            _runtime_values(
                                scope=session.ownership,
                                session_id=session.session_id,
                                state=state,
                            )
                            for state in session.target_runtime
                        ]
                    )
                )

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        session_id: UUID,
    ) -> ExecutionSession | None:
        statement = sa.select(execution_sessions).where(
            execution_sessions.c.session_id == session_id,
            execution_sessions.c.tenant_id == scope.tenant_id,
            execution_sessions.c.client_id == scope.client_id,
        )
        runtime_statement = (
            sa.select(session_target_runtime)
            .where(
                session_target_runtime.c.session_id == session_id,
                session_target_runtime.c.tenant_id == scope.tenant_id,
                session_target_runtime.c.client_id == scope.client_id,
            )
            .order_by(
                session_target_runtime.c.provider_id,
                session_target_runtime.c.model_id,
                session_target_runtime.c.reasoning_profile,
            )
        )

        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
            if row is None:
                return None
            runtime_rows = (await database.execute(runtime_statement)).mappings().all()
        return _session_from_rows(row, runtime_rows)

    async def put_target_runtime_state(
        self,
        *,
        scope: OwnershipScope,
        session_id: UUID,
        state: TargetRuntimeState,
    ) -> bool:
        ownership_statement = sa.select(
            execution_sessions.c.status,
            execution_sessions.c.authorized_targets,
        ).where(
            execution_sessions.c.session_id == session_id,
            execution_sessions.c.tenant_id == scope.tenant_id,
            execution_sessions.c.client_id == scope.client_id,
        )

        async with self._sessions.begin() as database:
            owner_row = (await database.execute(ownership_statement)).mappings().one_or_none()
            if owner_row is None:
                raise LookupError("owned execution session not found")
            if SessionStatus(cast(str, owner_row["status"])).terminal:
                return False

            authorized = set(_target_tuple(owner_row["authorized_targets"]))
            if state.target not in authorized:
                raise PermissionError("target is not authorized by the session policy snapshot")

            insert_statement = pg_insert(session_target_runtime).values(
                **_runtime_values(scope=scope, session_id=session_id, state=state)
            )
            upsert_statement = insert_statement.on_conflict_do_update(
                index_elements=[
                    session_target_runtime.c.session_id,
                    session_target_runtime.c.provider_id,
                    session_target_runtime.c.model_id,
                    session_target_runtime.c.reasoning_profile,
                ],
                set_={
                    "health_state": insert_statement.excluded.health_state,
                    "quarantine_state": insert_statement.excluded.quarantine_state,
                    "quarantine_until": insert_statement.excluded.quarantine_until,
                    "quarantine_reason_code": insert_statement.excluded.quarantine_reason_code,
                    "updated_at": insert_statement.excluded.updated_at,
                },
                where=sa.or_(
                    session_target_runtime.c.quarantine_state
                    != QuarantineState.TERMINAL.value,
                    insert_statement.excluded.quarantine_state
                    == QuarantineState.TERMINAL.value,
                ),
            ).returning(session_target_runtime.c.session_id)

            changed = (await database.execute(upsert_statement)).scalar_one_or_none()
            if changed is None:
                return False

            await database.execute(
                sa.update(execution_sessions)
                .where(
                    execution_sessions.c.session_id == session_id,
                    execution_sessions.c.tenant_id == scope.tenant_id,
                    execution_sessions.c.client_id == scope.client_id,
                )
                .values(
                    updated_at=sa.func.greatest(execution_sessions.c.updated_at, state.updated_at),
                    version=execution_sessions.c.version + 1,
                )
            )
            return True
