"""PostgreSQL adapter for the durable task state machine."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import cast
from uuid import UUID, uuid7

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .sessions import (
    ExecutionTargetSnapshot,
    OwnershipScope,
    SessionStatus,
)
from .tables import execution_sessions
from .task_tables import task_cycle_decisions, task_transitions, tasks
from .tasks import (
    ExecutionMode,
    ExecutionTask,
    RequestedTargetSnapshot,
    TaskCycleDecision,
    TaskPayloadReferences,
    TaskReasonEnvelope,
    TaskStatus,
    validate_requirement_partition,
    validate_transition,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _target_json(target: ExecutionTargetSnapshot) -> dict[str, str]:
    return {
        "provider_id": target.provider_id,
        "model_id": target.model_id,
        "reasoning_profile": target.reasoning_profile,
    }


def _requested_target_json(target: RequestedTargetSnapshot) -> dict[str, str | None]:
    return {
        "provider_id": target.provider_id,
        "model_id": target.model_id,
        "reasoning_profile": target.reasoning_profile,
    }


def _requested_target(value: object) -> RequestedTargetSnapshot | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("persisted requested_target must be an object")
    data = cast(dict[str, object], value)
    model = data.get("model_id")
    profile = data.get("reasoning_profile")
    return RequestedTargetSnapshot(
        provider_id=str(data["provider_id"]),
        model_id=None if model is None else str(model),
        reasoning_profile=None if profile is None else str(profile),
    )


def _effective_target(value: object) -> ExecutionTargetSnapshot | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("persisted effective_target must be an object")
    data = cast(dict[str, object], value)
    return ExecutionTargetSnapshot(
        provider_id=str(data["provider_id"]),
        model_id=str(data["model_id"]),
        reasoning_profile=str(data["reasoning_profile"]),
    )


def _target_from_json(value: object) -> ExecutionTargetSnapshot:
    target = _effective_target(value)
    if target is None:
        raise ValueError("persisted target must be an object")
    return target


def _str_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"persisted {field} must be an array")
    return tuple(str(item) for item in value)


def _uuid_tuple(value: object, field: str) -> tuple[UUID, ...]:
    if not isinstance(value, list):
        raise ValueError(f"persisted {field} must be an array")
    return tuple(UUID(str(item)) for item in value)


def _reason_from_row(row: RowMapping) -> TaskReasonEnvelope | None:
    reason_code = cast(str | None, row["reason_code"])
    error_class = cast(str | None, row["error_class"])
    error_reference_id = cast(UUID | None, row["error_reference_id"])
    if reason_code is None and error_class is None and error_reference_id is None:
        return None
    return TaskReasonEnvelope(
        reason_code=reason_code,
        error_class=error_class,
        error_reference_id=error_reference_id,
    )


def _task_from_row(row: RowMapping) -> ExecutionTask:
    return ExecutionTask(
        task_id=cast(UUID, row["task_id"]),
        session_id=cast(UUID, row["session_id"]),
        ownership=OwnershipScope(
            tenant_id=cast(UUID, row["tenant_id"]),
            client_id=cast(UUID, row["client_id"]),
        ),
        operation=cast(str, row["operation"]),
        status=TaskStatus(cast(str, row["status"])),
        effective_policy_version_id=cast(UUID, row["effective_policy_version_id"]),
        requested_execution_mode=ExecutionMode(
            cast(str, row["requested_execution_mode"])
        ),
        requested_target=_requested_target(row["requested_target"]),
        effective_target=_effective_target(row["effective_target"]),
        requirements=_str_tuple(row["requirements"], "requirements"),
        accepted_requirements=_str_tuple(
            row["accepted_requirements"],
            "accepted_requirements",
        ),
        missing_requirements=_str_tuple(
            row["missing_requirements"],
            "missing_requirements",
        ),
        payloads=TaskPayloadReferences(
            input_reference=cast(str, row["input_reference"]),
            input_fingerprint=cast(str, row["input_fingerprint"]),
            context_reference=cast(str | None, row["context_reference"]),
            schema_reference=cast(str | None, row["schema_reference"]),
        ),
        external_reference=cast(str | None, row["external_reference"]),
        attempt_reference_ids=_uuid_tuple(
            row["attempt_reference_ids"],
            "attempt_reference_ids",
        ),
        usage_reference_ids=_uuid_tuple(
            row["usage_reference_ids"],
            "usage_reference_ids",
        ),
        internal_cost_reference_ids=_uuid_tuple(
            row["internal_cost_reference_ids"],
            "internal_cost_reference_ids",
        ),
        provenance_reference_ids=_uuid_tuple(
            row["provenance_reference_ids"],
            "provenance_reference_ids",
        ),
        current_cycle=cast(int, row["current_cycle"]),
        result_reference=cast(str | None, row["result_reference"]),
        reason=_reason_from_row(row),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        queued_at=cast(datetime | None, row["queued_at"]),
        started_at=cast(datetime | None, row["started_at"]),
        terminal_at=cast(datetime | None, row["terminal_at"]),
        version=cast(int, row["version"]),
    )


def _cycle_decision_from_row(row: RowMapping) -> TaskCycleDecision:
    raw_candidates = row["candidate_order"]
    if not isinstance(raw_candidates, list):
        raise ValueError("persisted candidate_order must be an array")
    return TaskCycleDecision(
        cycle_index=cast(int, row["cycle_index"]),
        candidate_order=tuple(_target_from_json(item) for item in raw_candidates),
        accepted_snapshot=_str_tuple(row["accepted_snapshot"], "accepted_snapshot"),
        missing_snapshot=_str_tuple(row["missing_snapshot"], "missing_snapshot"),
        escalation_reason_code=cast(str | None, row["escalation_reason_code"]),
        delay_seconds=cast(int, row["delay_seconds"]),
        result_reference_snapshot=cast(str | None, row["result_reference_snapshot"]),
        recorded_at=cast(datetime, row["recorded_at"]),
    )


def _reason_values(reason: TaskReasonEnvelope | None) -> dict[str, object]:
    return {
        "reason_code": None if reason is None else reason.reason_code,
        "error_class": None if reason is None else reason.error_class,
        "error_reference_id": None if reason is None else reason.error_reference_id,
    }


class PostgresExecutionTaskStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def create(self, task: ExecutionTask) -> None:
        if task.status is not TaskStatus.CREATED:
            raise ValueError("new task must start in CREATED")
        if task.version != 1 or task.current_cycle != 0:
            raise ValueError("new task must start at version 1 and cycle 0")
        if task.accepted_requirements or task.missing_requirements != task.requirements:
            raise ValueError("new task must start with all requirements missing")
        if task.result_reference is not None or task.terminal_at is not None:
            raise ValueError("new task cannot already contain a terminal result")

        session_statement = sa.select(
            execution_sessions.c.status,
            execution_sessions.c.effective_policy_version_id,
        ).where(
            execution_sessions.c.session_id == task.session_id,
            execution_sessions.c.tenant_id == task.ownership.tenant_id,
            execution_sessions.c.client_id == task.ownership.client_id,
        )

        values = {
            "task_id": task.task_id,
            "session_id": task.session_id,
            "tenant_id": task.ownership.tenant_id,
            "client_id": task.ownership.client_id,
            "operation": task.operation,
            "status": task.status.value,
            "effective_policy_version_id": task.effective_policy_version_id,
            "requested_execution_mode": task.requested_execution_mode.value,
            "requested_target": (
                None
                if task.requested_target is None
                else _requested_target_json(task.requested_target)
            ),
            "effective_target": (
                None
                if task.effective_target is None
                else _target_json(task.effective_target)
            ),
            "requirements": list(task.requirements),
            "accepted_requirements": list(task.accepted_requirements),
            "missing_requirements": list(task.missing_requirements),
            "input_reference": task.payloads.input_reference,
            "input_fingerprint": task.payloads.input_fingerprint,
            "context_reference": task.payloads.context_reference,
            "schema_reference": task.payloads.schema_reference,
            "external_reference": task.external_reference,
            "attempt_reference_ids": [str(item) for item in task.attempt_reference_ids],
            "usage_reference_ids": [str(item) for item in task.usage_reference_ids],
            "internal_cost_reference_ids": [
                str(item) for item in task.internal_cost_reference_ids
            ],
            "provenance_reference_ids": [
                str(item) for item in task.provenance_reference_ids
            ],
            "current_cycle": task.current_cycle,
            "result_reference": task.result_reference,
            **_reason_values(task.reason),
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "queued_at": task.queued_at,
            "started_at": task.started_at,
            "terminal_at": task.terminal_at,
            "version": task.version,
        }

        async with self._sessions.begin() as database:
            parent = (await database.execute(session_statement)).mappings().one_or_none()
            if parent is None:
                raise LookupError("owned execution session not found")
            if SessionStatus(cast(str, parent["status"])).terminal:
                raise ValueError("cannot create task in a terminal execution session")
            if parent["effective_policy_version_id"] != task.effective_policy_version_id:
                raise ValueError("task policy version must match its session snapshot")

            await database.execute(sa.insert(tasks).values(**values))
            await database.execute(
                sa.insert(task_transitions).values(
                    transition_id=uuid7(),
                    task_id=task.task_id,
                    tenant_id=task.ownership.tenant_id,
                    client_id=task.ownership.client_id,
                    from_status=None,
                    to_status=TaskStatus.CREATED.value,
                    task_version=1,
                    occurred_at=task.created_at,
                )
            )

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
    ) -> ExecutionTask | None:
        statement = sa.select(tasks).where(
            tasks.c.task_id == task_id,
            tasks.c.tenant_id == scope.tenant_id,
            tasks.c.client_id == scope.client_id,
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _task_from_row(row)

    async def transition(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        expected_version: int,
        target_status: TaskStatus,
        occurred_at: datetime,
        accepted_requirements: tuple[str, ...] | None = None,
        missing_requirements: tuple[str, ...] | None = None,
        result_reference: str | None = None,
        reason: TaskReasonEnvelope | None = None,
    ) -> bool:
        _require_aware_transition_time(occurred_at)
        statement = sa.select(tasks).where(
            tasks.c.task_id == task_id,
            tasks.c.tenant_id == scope.tenant_id,
            tasks.c.client_id == scope.client_id,
        )

        async with self._sessions.begin() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
            if row is None:
                raise LookupError("owned task not found")
            current = _task_from_row(row)
            if current.version != expected_version:
                return False

            validate_transition(current.status, target_status)
            accepted = (
                current.accepted_requirements
                if accepted_requirements is None
                else accepted_requirements
            )
            missing = (
                current.missing_requirements
                if missing_requirements is None
                else missing_requirements
            )
            validate_requirement_partition(current.requirements, accepted, missing)

            new_result = (
                current.result_reference
                if result_reference is None
                else result_reference
            )
            if (
                current.result_reference is not None
                and result_reference is not None
                and result_reference != current.result_reference
            ):
                raise ValueError("terminal result reference is immutable")

            terminal_at = occurred_at if target_status.terminal else None
            queued_at = occurred_at if target_status is TaskStatus.QUEUED else current.queued_at
            started_at = current.started_at
            if target_status is TaskStatus.RUNNING and started_at is None:
                started_at = occurred_at

            candidate = replace(
                current,
                status=target_status,
                accepted_requirements=accepted,
                missing_requirements=missing,
                result_reference=new_result,
                reason=reason,
                updated_at=occurred_at,
                queued_at=queued_at,
                started_at=started_at,
                terminal_at=terminal_at,
                version=current.version + 1,
            )

            update = (
                sa.update(tasks)
                .where(
                    tasks.c.task_id == task_id,
                    tasks.c.tenant_id == scope.tenant_id,
                    tasks.c.client_id == scope.client_id,
                    tasks.c.version == expected_version,
                    tasks.c.status == current.status.value,
                )
                .values(
                    status=candidate.status.value,
                    accepted_requirements=list(candidate.accepted_requirements),
                    missing_requirements=list(candidate.missing_requirements),
                    result_reference=candidate.result_reference,
                    **_reason_values(candidate.reason),
                    updated_at=candidate.updated_at,
                    queued_at=candidate.queued_at,
                    started_at=candidate.started_at,
                    terminal_at=candidate.terminal_at,
                    version=candidate.version,
                )
                .returning(tasks.c.task_id)
            )
            changed = (await database.execute(update)).scalar_one_or_none()
            if changed is None:
                return False

            await database.execute(
                sa.insert(task_transitions).values(
                    transition_id=uuid7(),
                    task_id=task_id,
                    tenant_id=scope.tenant_id,
                    client_id=scope.client_id,
                    from_status=current.status.value,
                    to_status=candidate.status.value,
                    task_version=candidate.version,
                    occurred_at=occurred_at,
                    **_reason_values(reason),
                )
            )
            return True

    async def get_cycle_decision(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        cycle_index: int,
    ) -> TaskCycleDecision | None:
        if cycle_index < 1:
            raise ValueError("cycle_index must be positive")
        statement = sa.select(task_cycle_decisions).where(
            task_cycle_decisions.c.task_id == task_id,
            task_cycle_decisions.c.tenant_id == scope.tenant_id,
            task_cycle_decisions.c.client_id == scope.client_id,
            task_cycle_decisions.c.cycle_index == cycle_index,
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _cycle_decision_from_row(row)

    async def record_cycle_decision(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        expected_version: int,
        decision: TaskCycleDecision,
    ) -> bool:
        statement = sa.select(tasks).where(
            tasks.c.task_id == task_id,
            tasks.c.tenant_id == scope.tenant_id,
            tasks.c.client_id == scope.client_id,
        )

        async with self._sessions.begin() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
            if row is None:
                raise LookupError("owned task not found")
            current = _task_from_row(row)
            if current.version != expected_version:
                return False
            if current.status is not TaskStatus.RUNNING:
                raise ValueError("cycle decisions can only be recorded while RUNNING")
            if decision.cycle_index != current.current_cycle + 1:
                raise ValueError("cycle_index must advance exactly by one")

            validate_requirement_partition(
                current.requirements,
                decision.accepted_snapshot,
                decision.missing_snapshot,
            )
            if current.requested_execution_mode is ExecutionMode.EXPLICIT_TARGET:
                assert current.effective_target is not None
                if any(
                    candidate != current.effective_target
                    for candidate in decision.candidate_order
                ):
                    raise ValueError(
                        "EXPLICIT_TARGET cycle cannot contain a cross-target candidate"
                    )

            if (
                current.result_reference is not None
                and decision.result_reference_snapshot is not None
                and current.result_reference != decision.result_reference_snapshot
            ):
                raise ValueError("running result reference snapshot is immutable")

            candidate = replace(
                current,
                accepted_requirements=decision.accepted_snapshot,
                missing_requirements=decision.missing_snapshot,
                current_cycle=decision.cycle_index,
                result_reference=(
                    current.result_reference
                    if decision.result_reference_snapshot is None
                    else decision.result_reference_snapshot
                ),
                updated_at=decision.recorded_at,
                version=current.version + 1,
            )

            update = (
                sa.update(tasks)
                .where(
                    tasks.c.task_id == task_id,
                    tasks.c.tenant_id == scope.tenant_id,
                    tasks.c.client_id == scope.client_id,
                    tasks.c.version == expected_version,
                    tasks.c.status == TaskStatus.RUNNING.value,
                    tasks.c.current_cycle == current.current_cycle,
                )
                .values(
                    accepted_requirements=list(candidate.accepted_requirements),
                    missing_requirements=list(candidate.missing_requirements),
                    current_cycle=candidate.current_cycle,
                    result_reference=candidate.result_reference,
                    updated_at=candidate.updated_at,
                    version=candidate.version,
                )
                .returning(tasks.c.task_id)
            )
            changed = (await database.execute(update)).scalar_one_or_none()
            if changed is None:
                return False

            await database.execute(
                sa.insert(task_cycle_decisions).values(
                    task_id=task_id,
                    tenant_id=scope.tenant_id,
                    client_id=scope.client_id,
                    cycle_index=decision.cycle_index,
                    task_version=candidate.version,
                    candidate_order=[
                        _target_json(item) for item in decision.candidate_order
                    ],
                    escalation_reason_code=decision.escalation_reason_code,
                    delay_seconds=decision.delay_seconds,
                    result_reference_snapshot=decision.result_reference_snapshot,
                    accepted_snapshot=list(decision.accepted_snapshot),
                    missing_snapshot=list(decision.missing_snapshot),
                    recorded_at=decision.recorded_at,
                )
            )
            return True


def _require_aware_transition_time(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
