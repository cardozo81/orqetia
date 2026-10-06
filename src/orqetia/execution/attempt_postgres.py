"""PostgreSQL adapter for provider-attempt crash and replay boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.providers import ProviderAttemptResult

from .attempt_tables import provider_attempts
from .attempts import (
    DispatchAction,
    DispatchClaim,
    ProviderAttempt,
    ProviderAttemptStatus,
)
from .sessions import (
    ExecutionTargetSnapshot,
    OwnershipScope,
    SessionStatus,
)
from .tables import execution_sessions
from .task_tables import tasks
from .tasks import ExecutionMode, TaskStatus

SessionFactory = async_sessionmaker[AsyncSession]


def _target_json(target: ExecutionTargetSnapshot) -> dict[str, str]:
    return {
        "provider_id": target.provider_id,
        "model_id": target.model_id,
        "reasoning_profile": target.reasoning_profile,
    }


def _target_from_json(value: object) -> ExecutionTargetSnapshot:
    if not isinstance(value, dict):
        raise ValueError("persisted target must be an object")
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


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"persisted {field} must be an array")
    return tuple(str(item) for item in value)


def _attempt_from_row(row: RowMapping) -> ProviderAttempt:
    return ProviderAttempt(
        attempt_id=cast(UUID, row["attempt_id"]),
        task_id=cast(UUID | None, row["task_id"]),
        session_id=cast(UUID | None, row["session_id"]),
        ownership=OwnershipScope(
            tenant_id=cast(UUID, row["tenant_id"]),
            client_id=cast(UUID, row["client_id"]),
        ),
        operation=cast(str, row["operation"]),
        provider_account_id=cast(UUID | None, row["provider_account_id"]),
        provider_credential_id=cast(UUID | None, row["provider_credential_id"]),
        target=ExecutionTargetSnapshot(
            provider_id=cast(str, row["provider_id"]),
            model_id=cast(str, row["model_id"]),
            reasoning_profile=cast(str, row["reasoning_profile"]),
        ),
        cycle=cast(int, row["cycle"]),
        attempt_index=cast(int, row["attempt_index"]),
        request_reference=cast(str, row["request_reference"]),
        request_fingerprint=cast(str, row["request_fingerprint"]),
        status=ProviderAttemptStatus(cast(str, row["status"])),
        dispatch_work_id=cast(UUID | None, row["dispatch_work_id"]),
        provider_outcome=cast(str | None, row["provider_outcome"]),
        accepted_requirements=_string_tuple(
            row["accepted_requirements"],
            "accepted_requirements",
        ),
        missing_requirements=_string_tuple(
            row["missing_requirements"],
            "missing_requirements",
        ),
        response_reference=cast(str | None, row["response_reference"]),
        error_class=cast(str | None, row["error_class"]),
        retry_after_seconds=cast(int | None, row["retry_after_seconds"]),
        latency_ms=cast(int | None, row["latency_ms"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        dispatch_started_at=cast(datetime | None, row["dispatch_started_at"]),
        terminal_at=cast(datetime | None, row["terminal_at"]),
        version=cast(int, row["version"]),
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")


class PostgresProviderAttemptStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def create(self, attempt: ProviderAttempt) -> None:
        if attempt.status is not ProviderAttemptStatus.PREPARED:
            raise ValueError("new provider attempt must start PREPARED")
        if attempt.version != 1:
            raise ValueError("new provider attempt must start at version 1")

        values = {
            "attempt_id": attempt.attempt_id,
            "task_id": attempt.task_id,
            "session_id": attempt.session_id,
            "tenant_id": attempt.ownership.tenant_id,
            "client_id": attempt.ownership.client_id,
            "operation": attempt.operation,
            "provider_id": attempt.target.provider_id,
            "provider_account_id": attempt.provider_account_id,
            "provider_credential_id": attempt.provider_credential_id,
            "model_id": attempt.target.model_id,
            "reasoning_profile": attempt.target.reasoning_profile,
            "cycle": attempt.cycle,
            "attempt_index": attempt.attempt_index,
            "request_reference": attempt.request_reference,
            "request_fingerprint": attempt.request_fingerprint,
            "status": attempt.status.value,
            "accepted_requirements": list(attempt.accepted_requirements),
            "missing_requirements": list(attempt.missing_requirements),
            "created_at": attempt.created_at,
            "updated_at": attempt.updated_at,
            "version": attempt.version,
        }

        async with self._sessions.begin() as database:
            if attempt.session_id is not None:
                session_row = (
                    await database.execute(
                        sa.select(
                            execution_sessions.c.status,
                            execution_sessions.c.authorized_targets,
                        ).where(
                            execution_sessions.c.session_id == attempt.session_id,
                            execution_sessions.c.tenant_id == attempt.ownership.tenant_id,
                            execution_sessions.c.client_id == attempt.ownership.client_id,
                        )
                    )
                ).mappings().one_or_none()
                if session_row is None:
                    raise LookupError("owned execution session not found")
                if SessionStatus(cast(str, session_row["status"])).terminal:
                    raise ValueError("cannot create attempt in terminal session")
                if attempt.target not in set(
                    _target_tuple(session_row["authorized_targets"])
                ):
                    raise PermissionError("attempt target is not authorized by session")

            if attempt.task_id is not None:
                task_row = (
                    await database.execute(
                        sa.select(
                            tasks.c.session_id,
                            tasks.c.status,
                            tasks.c.requested_execution_mode,
                            tasks.c.effective_target,
                        ).where(
                            tasks.c.task_id == attempt.task_id,
                            tasks.c.tenant_id == attempt.ownership.tenant_id,
                            tasks.c.client_id == attempt.ownership.client_id,
                        )
                    )
                ).mappings().one_or_none()
                if task_row is None:
                    raise LookupError("owned execution task not found")
                if cast(UUID, task_row["session_id"]) != attempt.session_id:
                    raise ValueError("attempt session must match task session")
                if TaskStatus(cast(str, task_row["status"])) is not TaskStatus.RUNNING:
                    raise ValueError("provider attempt requires RUNNING task")

                mode = ExecutionMode(cast(str, task_row["requested_execution_mode"]))
                if mode is ExecutionMode.EXPLICIT_TARGET:
                    effective = _target_from_json(task_row["effective_target"])
                    if effective != attempt.target:
                        raise PermissionError(
                            "explicit task attempt must use frozen effective target"
                        )

            await database.execute(sa.insert(provider_attempts).values(**values))

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
    ) -> ProviderAttempt | None:
        statement = sa.select(provider_attempts).where(
            provider_attempts.c.attempt_id == attempt_id,
            provider_attempts.c.tenant_id == scope.tenant_id,
            provider_attempts.c.client_id == scope.client_id,
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _attempt_from_row(row)

    async def list_for_task(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
    ) -> tuple[ProviderAttempt, ...]:
        statement = (
            sa.select(provider_attempts)
            .where(
                provider_attempts.c.task_id == task_id,
                provider_attempts.c.tenant_id == scope.tenant_id,
                provider_attempts.c.client_id == scope.client_id,
            )
            .order_by(
                provider_attempts.c.cycle,
                provider_attempts.c.attempt_index,
                provider_attempts.c.created_at,
                provider_attempts.c.attempt_id,
            )
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_attempt_from_row(row) for row in rows)

    async def claim_dispatch(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
        work_id: UUID,
        occurred_at: datetime,
    ) -> DispatchClaim:
        _require_aware(occurred_at)
        lookup = (
            sa.select(provider_attempts)
            .where(
                provider_attempts.c.attempt_id == attempt_id,
                provider_attempts.c.tenant_id == scope.tenant_id,
                provider_attempts.c.client_id == scope.client_id,
            )
            .with_for_update()
        )

        async with self._sessions.begin() as database:
            row = (await database.execute(lookup)).mappings().one_or_none()
            if row is None:
                raise LookupError("owned provider attempt not found")
            attempt = _attempt_from_row(row)

            if attempt.status is ProviderAttemptStatus.COMPLETED:
                return DispatchClaim(DispatchAction.SKIP_COMPLETED, attempt)
            if attempt.status in {
                ProviderAttemptStatus.AMBIGUOUS,
                ProviderAttemptStatus.CANCELLED,
            }:
                return DispatchClaim(DispatchAction.SKIP_TERMINAL, attempt)

            if attempt.status is ProviderAttemptStatus.DISPATCHING:
                if attempt.dispatch_work_id != work_id:
                    return DispatchClaim(DispatchAction.DUPLICATE_WORK, attempt)
                updated = await self._terminalize_ambiguous(
                    database=database,
                    attempt=attempt,
                    work_id=work_id,
                    occurred_at=occurred_at,
                    error_class="DISPATCH_RESULT_UNKNOWN_AFTER_RECLAIM",
                )
                return DispatchClaim(DispatchAction.MARKED_AMBIGUOUS, updated)

            if attempt.task_id is not None:
                task_status = (
                    await database.execute(
                        sa.select(tasks.c.status).where(
                            tasks.c.task_id == attempt.task_id,
                            tasks.c.tenant_id == scope.tenant_id,
                            tasks.c.client_id == scope.client_id,
                        )
                    )
                ).scalar_one_or_none()
                if task_status is None:
                    raise LookupError("owned task for provider attempt not found")
                if TaskStatus(cast(str, task_status)) is not TaskStatus.RUNNING:
                    statement = (
                        sa.update(provider_attempts)
                        .where(
                            provider_attempts.c.attempt_id == attempt_id,
                            provider_attempts.c.version == attempt.version,
                            provider_attempts.c.status
                            == ProviderAttemptStatus.PREPARED.value,
                        )
                        .values(
                            status=ProviderAttemptStatus.CANCELLED.value,
                            updated_at=occurred_at,
                            terminal_at=occurred_at,
                            error_class="TASK_NOT_RUNNABLE",
                            version=provider_attempts.c.version + 1,
                        )
                        .returning(*provider_attempts.c)
                    )
                    updated = (
                        await database.execute(statement)
                    ).mappings().one()
                    return DispatchClaim(
                        DispatchAction.CANCELLED_BY_TASK,
                        _attempt_from_row(updated),
                    )

            statement = (
                sa.update(provider_attempts)
                .where(
                    provider_attempts.c.attempt_id == attempt_id,
                    provider_attempts.c.version == attempt.version,
                    provider_attempts.c.status == ProviderAttemptStatus.PREPARED.value,
                )
                .values(
                    status=ProviderAttemptStatus.DISPATCHING.value,
                    dispatch_work_id=work_id,
                    dispatch_started_at=occurred_at,
                    updated_at=occurred_at,
                    version=provider_attempts.c.version + 1,
                )
                .returning(*provider_attempts.c)
            )
            updated_row = (await database.execute(statement)).mappings().one()
            return DispatchClaim(
                DispatchAction.DISPATCH,
                _attempt_from_row(updated_row),
            )

    async def complete_dispatch(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
        work_id: UUID,
        result: ProviderAttemptResult,
        occurred_at: datetime,
    ) -> bool:
        _require_aware(occurred_at)
        if result.attempt_id != attempt_id:
            raise ValueError("provider result attempt_id does not match journal")

        statement = (
            sa.update(provider_attempts)
            .where(
                provider_attempts.c.attempt_id == attempt_id,
                provider_attempts.c.tenant_id == scope.tenant_id,
                provider_attempts.c.client_id == scope.client_id,
                provider_attempts.c.status == ProviderAttemptStatus.DISPATCHING.value,
                provider_attempts.c.dispatch_work_id == work_id,
            )
            .values(
                status=ProviderAttemptStatus.COMPLETED.value,
                provider_outcome=result.outcome.value,
                accepted_requirements=list(result.accepted_requirements),
                missing_requirements=list(result.missing_requirements),
                response_reference=result.response_reference,
                error_class=result.error_class,
                retry_after_seconds=result.retry_after_seconds,
                latency_ms=result.simulated_latency_ms,
                updated_at=occurred_at,
                terminal_at=occurred_at,
                version=provider_attempts.c.version + 1,
            )
            .returning(provider_attempts.c.attempt_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        return changed is not None

    async def mark_ambiguous(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
        work_id: UUID,
        occurred_at: datetime,
        error_class: str,
    ) -> bool:
        _require_aware(occurred_at)
        if not error_class.strip() or len(error_class) > 200:
            raise ValueError("error_class must contain 1..200 characters")

        statement = (
            sa.update(provider_attempts)
            .where(
                provider_attempts.c.attempt_id == attempt_id,
                provider_attempts.c.tenant_id == scope.tenant_id,
                provider_attempts.c.client_id == scope.client_id,
                provider_attempts.c.status == ProviderAttemptStatus.DISPATCHING.value,
                provider_attempts.c.dispatch_work_id == work_id,
            )
            .values(
                status=ProviderAttemptStatus.AMBIGUOUS.value,
                error_class=error_class,
                updated_at=occurred_at,
                terminal_at=occurred_at,
                version=provider_attempts.c.version + 1,
            )
            .returning(provider_attempts.c.attempt_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        return changed is not None

    @staticmethod
    async def _terminalize_ambiguous(
        *,
        database: AsyncSession,
        attempt: ProviderAttempt,
        work_id: UUID,
        occurred_at: datetime,
        error_class: str,
    ) -> ProviderAttempt:
        statement = (
            sa.update(provider_attempts)
            .where(
                provider_attempts.c.attempt_id == attempt.attempt_id,
                provider_attempts.c.version == attempt.version,
                provider_attempts.c.status == ProviderAttemptStatus.DISPATCHING.value,
                provider_attempts.c.dispatch_work_id == work_id,
            )
            .values(
                status=ProviderAttemptStatus.AMBIGUOUS.value,
                error_class=error_class,
                updated_at=occurred_at,
                terminal_at=occurred_at,
                version=provider_attempts.c.version + 1,
            )
            .returning(*provider_attempts.c)
        )
        row = (await database.execute(statement)).mappings().one()
        return _attempt_from_row(row)
