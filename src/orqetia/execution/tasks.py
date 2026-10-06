"""Durable task state-machine contracts owned by the Execution bounded context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from .sessions import ExecutionTargetSnapshot, OwnershipScope

_OPERATION = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _bounded_optional(value: str | None, field: str, maximum: int) -> None:
    if value is not None and len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            TaskStatus.PARTIAL,
            TaskStatus.COMPLETE,
            TaskStatus.UNAVAILABLE,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }


class ExecutionMode(StrEnum):
    AUTO = "AUTO"
    EXPLICIT_TARGET = "EXPLICIT_TARGET"


VALID_TRANSITIONS = {
    TaskStatus.CREATED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.COMPLETE,
            TaskStatus.PARTIAL,
            TaskStatus.UNAVAILABLE,
            TaskStatus.FAILED,
            TaskStatus.CANCELLING,
        }
    ),
    TaskStatus.CANCELLING: frozenset({TaskStatus.CANCELLED}),
    TaskStatus.PARTIAL: frozenset(),
    TaskStatus.COMPLETE: frozenset(),
    TaskStatus.UNAVAILABLE: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def validate_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in VALID_TRANSITIONS[current]:
        raise ValueError(f"invalid task transition: {current.value} -> {target.value}")


@dataclass(frozen=True)
class RequestedTargetSnapshot:
    provider_id: str
    model_id: str | None = None
    reasoning_profile: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id is required")
        if len(self.provider_id) > 100:
            raise ValueError("provider_id exceeds 100 characters")
        _bounded_optional(self.model_id, "model_id", 200)
        _bounded_optional(self.reasoning_profile, "reasoning_profile", 100)


@dataclass(frozen=True)
class TaskPayloadReferences:
    input_reference: str
    input_fingerprint: str
    context_reference: str | None = None
    schema_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.input_reference.strip():
            raise ValueError("input_reference is required")
        if len(self.input_reference) > 500:
            raise ValueError("input_reference exceeds 500 characters")
        if not _SHA256.fullmatch(self.input_fingerprint):
            raise ValueError("input_fingerprint must be lowercase SHA-256 hex")
        _bounded_optional(self.context_reference, "context_reference", 500)
        _bounded_optional(self.schema_reference, "schema_reference", 500)


@dataclass(frozen=True)
class TaskReasonEnvelope:
    reason_code: str | None = None
    error_class: str | None = None
    error_reference_id: UUID | None = None

    def __post_init__(self) -> None:
        _bounded_optional(self.reason_code, "reason_code", 200)
        _bounded_optional(self.error_class, "error_class", 200)


def validate_requirement_partition(
    requirements: tuple[str, ...],
    accepted: tuple[str, ...],
    missing: tuple[str, ...],
) -> None:
    if len(set(requirements)) != len(requirements):
        raise ValueError("requirements must not contain duplicates")
    if any(not item.strip() or len(item) > 200 for item in requirements):
        raise ValueError("requirements must contain non-empty values up to 200 characters")
    if len(set(accepted)) != len(accepted) or len(set(missing)) != len(missing):
        raise ValueError("accepted/missing requirements must not contain duplicates")

    requirement_set = set(requirements)
    accepted_set = set(accepted)
    missing_set = set(missing)
    if accepted_set & missing_set:
        raise ValueError("accepted and missing requirements must be disjoint")
    if accepted_set | missing_set != requirement_set:
        raise ValueError("accepted and missing requirements must partition requirements")


@dataclass(frozen=True)
class ExecutionTask:
    task_id: UUID
    session_id: UUID
    ownership: OwnershipScope
    operation: str
    status: TaskStatus
    effective_policy_version_id: UUID
    requested_execution_mode: ExecutionMode
    requirements: tuple[str, ...]
    accepted_requirements: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    payloads: TaskPayloadReferences
    created_at: datetime
    updated_at: datetime
    requested_target: RequestedTargetSnapshot | None = None
    effective_target: ExecutionTargetSnapshot | None = None
    external_reference: str | None = None
    attempt_reference_ids: tuple[UUID, ...] = ()
    usage_reference_ids: tuple[UUID, ...] = ()
    internal_cost_reference_ids: tuple[UUID, ...] = ()
    provenance_reference_ids: tuple[UUID, ...] = ()
    current_cycle: int = 0
    result_reference: str | None = None
    reason: TaskReasonEnvelope | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    terminal_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not _OPERATION.fullmatch(self.operation):
            raise ValueError("operation must use the canonical uppercase operation code format")

        validate_requirement_partition(
            self.requirements,
            self.accepted_requirements,
            self.missing_requirements,
        )

        if self.requested_execution_mode is ExecutionMode.AUTO:
            if self.requested_target is not None or self.effective_target is not None:
                raise ValueError("AUTO task cannot freeze requested/effective target")
        else:
            if self.requested_target is None or self.effective_target is None:
                raise ValueError("EXPLICIT_TARGET requires requested and effective targets")

        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        for field, value in (
            ("queued_at", self.queued_at),
            ("started_at", self.started_at),
            ("terminal_at", self.terminal_at),
        ):
            if value is not None:
                _require_aware(value, field)

        if self.status.terminal != (self.terminal_at is not None):
            raise ValueError("terminal task status and terminal_at must agree")
        if self.status is TaskStatus.COMPLETE and self.missing_requirements:
            raise ValueError("COMPLETE task cannot have missing requirements")
        if self.status is TaskStatus.PARTIAL and (
            not self.accepted_requirements or not self.missing_requirements
        ):
            raise ValueError("PARTIAL task requires accepted and missing requirements")
        if (
            self.status in {TaskStatus.COMPLETE, TaskStatus.PARTIAL}
            and (self.result_reference is None or not self.result_reference.strip())
        ):
            raise ValueError("successful terminal task requires result_reference")
        if self.result_reference is not None and len(self.result_reference) > 500:
            raise ValueError("result_reference exceeds 500 characters")

        _bounded_optional(self.external_reference, "external_reference", 200)
        if self.current_cycle < 0:
            raise ValueError("current_cycle cannot be negative")
        if self.version < 1:
            raise ValueError("version must be positive")

        for field, values in (
            ("attempt_reference_ids", self.attempt_reference_ids),
            ("usage_reference_ids", self.usage_reference_ids),
            ("internal_cost_reference_ids", self.internal_cost_reference_ids),
            ("provenance_reference_ids", self.provenance_reference_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{field} must not contain duplicates")


@dataclass(frozen=True)
class TaskCycleDecision:
    cycle_index: int
    candidate_order: tuple[ExecutionTargetSnapshot, ...]
    accepted_snapshot: tuple[str, ...]
    missing_snapshot: tuple[str, ...]
    recorded_at: datetime
    escalation_reason_code: str | None = None
    delay_seconds: int = 0

    def __post_init__(self) -> None:
        if self.cycle_index < 1:
            raise ValueError("cycle_index must be positive")
        if len(set(self.candidate_order)) != len(self.candidate_order):
            raise ValueError("candidate_order must not contain duplicate targets")
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        _bounded_optional(self.escalation_reason_code, "escalation_reason_code", 200)
        _require_aware(self.recorded_at, "recorded_at")


class ExecutionTaskStore(Protocol):
    async def create(self, task: ExecutionTask) -> None:
        """Persist one CREATED task under an ACTIVE owned session."""

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
    ) -> ExecutionTask | None:
        """Read only when tenant/client ownership matches."""

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
        """Compare-and-set one valid state transition."""

    async def get_cycle_decision(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        cycle_index: int,
    ) -> TaskCycleDecision | None:
        """Read one frozen cycle plan through tenant/client ownership."""

    async def record_cycle_decision(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        expected_version: int,
        decision: TaskCycleDecision,
    ) -> bool:
        """Persist one sequential cycle-decision fact without routing logic."""
