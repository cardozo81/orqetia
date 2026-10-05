"""PostgreSQL repository and atomic scheduler for provider attempts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.infrastructure.messaging.tables import work_items
from orqetia.providers import (
    NativeUsage,
    OutputKind,
    ProviderAttemptResult,
    ProviderCostMetadata,
    ProviderOutcome,
    ProviderTarget,
    ProviderUsage,
)
from orqetia.shared.messaging import DataClassification, QueueName, WorkState

from .attempt_tables import provider_attempts
from .attempts import ProviderAttempt, ProviderAttemptStatus
from .sessions import OwnershipScope
from .tables import execution_sessions
from .task_tables import tasks
from .tasks import ExecutionMode, TaskStatus

SessionFactory = async_sessionmaker[AsyncSession]

PROVIDER_DISPATCH_OPERATION = "provider_attempt.dispatch"
PROVIDER_DISPATCH_VERSION = 1


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _target_json(target: ProviderTarget) -> dict[str, str]:
    return {
        "provider_id": target.provider_id,
        "model_id": target.model_id,
        "reasoning_profile": target.reasoning_profile,
    }


def _target_from_columns(row: RowMapping) -> ProviderTarget:
    return ProviderTarget(
        provider_id=cast(str, row["provider_id"]),
        model_id=cast(str, row["model_id"]),
        reasoning_profile=cast(str, row["reasoning_profile"]),
    )


def _usage_json(usage: ProviderUsage) -> dict[str, object]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "native": [
            {"name": item.name, "value": str(item.value), "unit": item.unit}
            for item in usage.native
        ],
    }


def _usage_from_json(value: object) -> ProviderUsage:
    if not isinstance(value, dict):
        raise ValueError("persisted usage must be an object")
    raw = cast(dict[str, object], value)
    native_value = raw.get("native", [])
    if not isinstance(native_value, list):
        raise ValueError("persisted native usage must be an array")
    native: list[NativeUsage] = []
    for item in native_value:
        if not isinstance(item, dict):
            raise ValueError("persisted native usage entry must be an object")
        entry = cast(dict[str, object], item)
        native.append(
            NativeUsage(
                name=str(entry["name"]),
                value=Decimal(str(entry["value"])),
                unit=str(entry["unit"]),
            )
        )
    return ProviderUsage(
        input_tokens=int(raw.get("input_tokens", 0)),
        output_tokens=int(raw.get("output_tokens", 0)),
        native=tuple(native),
    )


def _cost_json(cost: ProviderCostMetadata | None) -> dict[str, object] | None:
    if cost is None:
        return None
    return {
        "amount": None if cost.amount is None else str(cost.amount),
        "comparison_group": cost.comparison_group,
        "currency": cost.currency,
        "native_unit": cost.native_unit,
    }


def _cost_from_json(value: object) -> ProviderCostMetadata | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("persisted cost metadata must be an object")
    raw = cast(dict[str, object], value)
    amount = raw.get("amount")
    return ProviderCostMetadata(
        amount=None if amount is None else Decimal(str(amount)),
        comparison_group=str(raw["comparison_group"]),
        currency=None if raw.get("currency") is None else str(raw["currency"]),
        native_unit=(
            None if raw.get("native_unit") is None else str(raw["native_unit"])
        ),
    )


def _str_tuple(value: object | None, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"persisted {field} must be an array")
    return tuple(str(item) for item in value)


def _result_from_row(row: RowMapping) -> ProviderAttemptResult | None:
    if row["outcome"] is None:
        return None
    return ProviderAttemptResult(
        attempt_id=cast(UUID, row["attempt_id"]),
        outcome=ProviderOutcome(cast(str, row["outcome"])),
        output_kind=OutputKind(cast(str, row["output_kind"])),
        accepted_requirements=_str_tuple(
            row["accepted_requirements"],
            "accepted_requirements",
        ),
        missing_requirements=_str_tuple(
            row["remaining_requirements"],
            "remaining_requirements",
        ),
        simulated_latency_ms=cast(int, row["simulated_latency_ms"]),
        response_reference=cast(str | None, row["response_reference"]),
        error_class=cast(str | None, row["error_class"]),
        retry_after_seconds=cast(int | None, row["retry_after_seconds"]),
        usage=_usage_from_json(row["usage"]),
        cost=_cost_from_json(row["cost_metadata"]),
    )


def _attempt_from_row(row: RowMapping) -> ProviderAttempt:
    tenant_id = cast(UUID | None, row["tenant_id"])
    client_id = cast(UUID | None, row["client_id"])
    ownership = (
        None
        if tenant_id is None and client_id is None
        else OwnershipScope(
            tenant_id=cast(UUID, tenant_id),
            client_id=cast(UUID, client_id),
        )
    )
    return ProviderAttempt(
        attempt_id=cast(UUID, row["attempt_id"]),
        work_id=cast(UUID, row["work_id"]),
        ownership=ownership,
        session_id=cast(UUID | None, row["session_id"]),
        task_id=cast(UUID | None, row["task_id"]),
        operation=cast(str, row["operation"]),
        target=_target_from_columns(row),
        cycle=cast(int, row["cycle"]),
        attempt_index=cast(int, row["attempt_index"]),
        request_reference=cast(str, row["request_reference"]),
        request_fingerprint=cast(str, row["request_fingerprint"]),
        missing_requirements=_str_tuple(
            row["missing_requirements"],
            "missing_requirements",
        ),
        retry_of_attempt_id=cast(UUID | None, row["retry_of_attempt_id"]),
        fallback_from_attempt_id=cast(UUID | None, row["fallback_from_attempt_id"]),
        status=ProviderAttemptStatus(cast(str, row["status"])),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        dispatch_started_at=cast(datetime | None, row["dispatch_started_at"]),
        terminal_at=cast(datetime | None, row["terminal_at"]),
        result=_result_from_row(row),
        ambiguity_error_class=cast(str | None, row["ambiguity_error_class"]),
        version=cast(int, row["version"]),
    )


def _attempt_values(attempt: ProviderAttempt) -> dict[str, object]:
    ownership = attempt.ownership
    return {
        "attempt_id": attempt.attempt_id,
        "work_id": attempt.work_id,
        "tenant_id": None if ownership is None else ownership.tenant_id,
        "client_id": None if ownership is None else ownership.client_id,
        "session_id": attempt.session_id,
        "task_id": attempt.task_id,
        "operation": attempt.operation,
        "provider_id": attempt.target.provider_id,
        "model_id": attempt.target.model_id,
        "reasoning_profile": attempt.target.reasoning_profile,
        "cycle": attempt.cycle,
        "attempt_index": attempt.attempt_index,
        "request_reference": attempt.request_reference,
        "request_fingerprint": attempt.request_fingerprint,
        "missing_requirements": list(attempt.missing_requirements),
        "retry_of_attempt_id": attempt.retry_of_attempt_id,
        "fallback_from_attempt_id": attempt.fallback_from_attempt_id,
        "status": attempt.status.value,
        "created_at": attempt.created_at,
        "updated_at": attempt.updated_at,
        "version": attempt.version,
    }


class PostgresProviderAttemptStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def schedule(
        self,
        attempt: ProviderAttempt,
        *,
        available_at: datetime,
        priority: int = 0,
        max_infrastructure_attempts: int = 5,
    ) -> None:
        if attempt.status is not ProviderAttemptStatus.READY:
            raise ValueError("scheduled provider attempt must start READY")
        if attempt.version != 1:
            raise ValueError("scheduled provider attempt must start at version 1")
        _require_aware(available_at, "available_at")
        if not -100 <= priority <= 100:
            raise ValueError("priority must be between -100 and 100")
        if not 1 <= max_infrastructure_attempts <= 100:
            raise ValueError("max_infrastructure_attempts must be between 1 and 100")

        async with self._sessions.begin() as database:
            await self._validate_task_binding(database, attempt)
            await database.execute(
                sa.insert(provider_attempts).values(**_attempt_values(attempt))
            )
            await database.execute(
                sa.insert(work_items).values(
                    work_id=attempt.work_id,
                    queue_name=QueueName.EXECUTION.value,
                    operation_type=PROVIDER_DISPATCH_OPERATION,
                    operation_version=PROVIDER_DISPATCH_VERSION,
                    tenant_id=(
                        None if attempt.ownership is None else attempt.ownership.tenant_id
                    ),
                    client_id=(
                        None if attempt.ownership is None else attempt.ownership.client_id
                    ),
                    resource_type="provider_attempt",
                    resource_id=attempt.attempt_id,
                    data_classification=(
                        DataClassification.INTERNAL.value
                        if attempt.ownership is None
                        else DataClassification.CLIENT_PRIVATE.value
                    ),
                    payload={"attempt_id": str(attempt.attempt_id)},
                    priority=priority,
                    state=WorkState.READY.value,
                    available_at=available_at,
                    max_infrastructure_attempts=max_infrastructure_attempts,
                    correlation_id=attempt.task_id or attempt.session_id,
                    logical_operation_id=f"provider_attempt:{attempt.attempt_id}",
                )
            )

    async def _validate_task_binding(
        self,
        database: AsyncSession,
        attempt: ProviderAttempt,
    ) -> None:
        if attempt.task_id is None:
            return
        assert attempt.ownership is not None
        assert attempt.session_id is not None

        task_statement = (
            sa.select(
                tasks.c.session_id,
                tasks.c.operation,
                tasks.c.status,
                tasks.c.requested_execution_mode,
                tasks.c.effective_target,
                tasks.c.attempt_reference_ids,
            )
            .where(
                tasks.c.task_id == attempt.task_id,
                tasks.c.tenant_id == attempt.ownership.tenant_id,
                tasks.c.client_id == attempt.ownership.client_id,
            )
            .with_for_update()
        )
        task_row = (await database.execute(task_statement)).mappings().one_or_none()
        if task_row is None:
            raise LookupError("owned task not found for provider attempt")
        if cast(UUID, task_row["session_id"]) != attempt.session_id:
            raise ValueError("provider attempt session does not match task session")
        if cast(str, task_row["operation"]) != attempt.operation:
            raise ValueError("provider attempt operation does not match task operation")
        if TaskStatus(cast(str, task_row["status"])).terminal:
            raise ValueError("cannot schedule provider attempt for terminal task")

        mode = ExecutionMode(cast(str, task_row["requested_execution_mode"]))
        if mode is ExecutionMode.EXPLICIT_TARGET:
            effective = task_row["effective_target"]
            if effective != _target_json(attempt.target):
                raise PermissionError(
                    "provider attempt target differs from frozen explicit target"
                )
        else:
            session_statement = sa.select(
                execution_sessions.c.authorized_targets
            ).where(
                execution_sessions.c.session_id == attempt.session_id,
                execution_sessions.c.tenant_id == attempt.ownership.tenant_id,
                execution_sessions.c.client_id == attempt.ownership.client_id,
            )
            authorized_raw = (
                await database.execute(session_statement)
            ).scalar_one_or_none()
            if authorized_raw is None:
                raise LookupError("owned execution session not found")
            if _target_json(attempt.target) not in authorized_raw:
                raise PermissionError(
                    "provider attempt target is outside session policy snapshot"
                )

        refs = task_row["attempt_reference_ids"]
        if not isinstance(refs, list):
            raise ValueError("persisted attempt_reference_ids must be an array")
        rendered = str(attempt.attempt_id)
        if rendered in refs:
            raise ValueError("provider attempt is already registered on task")
        await database.execute(
            sa.update(tasks)
            .where(
                tasks.c.task_id == attempt.task_id,
                tasks.c.tenant_id == attempt.ownership.tenant_id,
                tasks.c.client_id == attempt.ownership.client_id,
            )
            .values(
                attempt_reference_ids=cast(list[object], refs) + [rendered],
                version=tasks.c.version + 1,
                updated_at=attempt.updated_at,
            )
        )

    async def get(self, attempt_id: UUID) -> ProviderAttempt | None:
        statement = sa.select(provider_attempts).where(
            provider_attempts.c.attempt_id == attempt_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _attempt_from_row(row)

    async def begin_dispatch(
        self,
        attempt_id: UUID,
        *,
        occurred_at: datetime,
        expected_version: int,
    ) -> bool:
        _require_aware(occurred_at, "occurred_at")
        statement = (
            sa.update(provider_attempts)
            .where(
                provider_attempts.c.attempt_id == attempt_id,
                provider_attempts.c.status == ProviderAttemptStatus.READY.value,
                provider_attempts.c.version == expected_version,
            )
            .values(
                status=ProviderAttemptStatus.DISPATCHING.value,
                dispatch_started_at=occurred_at,
                updated_at=occurred_at,
                version=provider_attempts.c.version + 1,
            )
            .returning(provider_attempts.c.attempt_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        return changed is not None

    async def complete(
        self,
        attempt_id: UUID,
        *,
        result: ProviderAttemptResult,
        occurred_at: datetime,
        expected_version: int,
    ) -> bool:
        _require_aware(occurred_at, "occurred_at")
        if result.attempt_id != attempt_id:
            raise ValueError("provider result attempt_id does not match persisted attempt")

        statement = (
            sa.update(provider_attempts)
            .where(
                provider_attempts.c.attempt_id == attempt_id,
                provider_attempts.c.status == ProviderAttemptStatus.DISPATCHING.value,
                provider_attempts.c.version == expected_version,
            )
            .values(
                status=ProviderAttemptStatus.COMPLETED.value,
                outcome=result.outcome.value,
                output_kind=result.output_kind.value,
                accepted_requirements=list(result.accepted_requirements),
                remaining_requirements=list(result.missing_requirements),
                response_reference=result.response_reference,
                error_class=result.error_class,
                retry_after_seconds=result.retry_after_seconds,
                simulated_latency_ms=result.simulated_latency_ms,
                usage=_usage_json(result.usage),
                cost_metadata=_cost_json(result.cost),
                terminal_at=occurred_at,
                updated_at=occurred_at,
                version=provider_attempts.c.version + 1,
            )
            .returning(provider_attempts.c.attempt_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        return changed is not None

    async def mark_ambiguous(
        self,
        attempt_id: UUID,
        *,
        error_class: str,
        occurred_at: datetime,
        expected_version: int,
    ) -> bool:
        if not error_class.strip() or len(error_class) > 200:
            raise ValueError("error_class must contain 1..200 characters")
        _require_aware(occurred_at, "occurred_at")
        statement = (
            sa.update(provider_attempts)
            .where(
                provider_attempts.c.attempt_id == attempt_id,
                provider_attempts.c.status == ProviderAttemptStatus.DISPATCHING.value,
                provider_attempts.c.version == expected_version,
            )
            .values(
                status=ProviderAttemptStatus.AMBIGUOUS.value,
                ambiguity_error_class=error_class,
                terminal_at=occurred_at,
                updated_at=occurred_at,
                version=provider_attempts.c.version + 1,
            )
            .returning(provider_attempts.c.attempt_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        return changed is not None
