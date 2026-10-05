"""Durable provider-attempt journal for crash-safe worker dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from orqetia.providers import ProviderAttemptResult

from .sessions import ExecutionTargetSnapshot, OwnershipScope


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ProviderAttemptStatus(StrEnum):
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    COMPLETED = "COMPLETED"
    AMBIGUOUS = "AMBIGUOUS"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            ProviderAttemptStatus.COMPLETED,
            ProviderAttemptStatus.AMBIGUOUS,
            ProviderAttemptStatus.CANCELLED,
        }


class DispatchAction(StrEnum):
    DISPATCH = "DISPATCH"
    SKIP_COMPLETED = "SKIP_COMPLETED"
    SKIP_TERMINAL = "SKIP_TERMINAL"
    DUPLICATE_WORK = "DUPLICATE_WORK"
    MARKED_AMBIGUOUS = "MARKED_AMBIGUOUS"
    CANCELLED_BY_TASK = "CANCELLED_BY_TASK"


@dataclass(frozen=True)
class ProviderAttempt:
    attempt_id: UUID
    ownership: OwnershipScope
    operation: str
    target: ExecutionTargetSnapshot
    cycle: int
    attempt_index: int
    request_reference: str
    request_fingerprint: str
    status: ProviderAttemptStatus
    created_at: datetime
    updated_at: datetime
    task_id: UUID | None = None
    session_id: UUID | None = None
    dispatch_work_id: UUID | None = None
    provider_outcome: str | None = None
    accepted_requirements: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    response_reference: str | None = None
    error_class: str | None = None
    retry_after_seconds: int | None = None
    latency_ms: int | None = None
    dispatch_started_at: datetime | None = None
    terminal_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.operation.strip() or len(self.operation) > 100:
            raise ValueError("operation must contain 1..100 characters")
        if self.cycle < 1 or self.attempt_index < 1:
            raise ValueError("cycle and attempt_index must be positive")
        if not self.request_reference.strip() or len(self.request_reference) > 500:
            raise ValueError("request_reference must contain 1..500 characters")
        if len(self.request_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.request_fingerprint
        ):
            raise ValueError("request_fingerprint must be lowercase SHA-256 hex")
        if self.task_id is not None and self.session_id is None:
            raise ValueError("task-scoped attempt requires session_id")
        if self.version < 1:
            raise ValueError("version must be positive")

        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        for field, value in (
            ("dispatch_started_at", self.dispatch_started_at),
            ("terminal_at", self.terminal_at),
        ):
            if value is not None:
                _require_aware(value, field)

        if self.status is ProviderAttemptStatus.PREPARED:
            if self.dispatch_work_id is not None or self.dispatch_started_at is not None:
                raise ValueError("PREPARED attempt cannot have dispatch ownership")
        if self.status is ProviderAttemptStatus.DISPATCHING:
            if self.dispatch_work_id is None or self.dispatch_started_at is None:
                raise ValueError("DISPATCHING attempt requires work and start timestamp")
        if self.status.terminal != (self.terminal_at is not None):
            raise ValueError("terminal attempt status and terminal_at must agree")
        if self.status is ProviderAttemptStatus.COMPLETED and self.provider_outcome is None:
            raise ValueError("COMPLETED attempt requires provider_outcome")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds cannot be negative")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if set(self.accepted_requirements) & set(self.missing_requirements):
            raise ValueError("accepted and missing requirements must be disjoint")


@dataclass(frozen=True)
class DispatchClaim:
    action: DispatchAction
    attempt: ProviderAttempt


class ProviderAttemptStore(Protocol):
    async def create(self, attempt: ProviderAttempt) -> None:
        """Persist PREPARED attempt before queue dispatch."""

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
    ) -> ProviderAttempt | None:
        """Read an attempt only through tenant/client ownership."""

    async def claim_dispatch(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
        work_id: UUID,
        occurred_at: datetime,
    ) -> DispatchClaim:
        """Atomically decide whether this work item may call the provider."""

    async def complete_dispatch(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
        work_id: UUID,
        result: ProviderAttemptResult,
        occurred_at: datetime,
    ) -> bool:
        """Persist one normalized result before work acknowledgement."""

    async def mark_ambiguous(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
        work_id: UUID,
        occurred_at: datetime,
        error_class: str,
    ) -> bool:
        """Terminalize an uncertain post-dispatch failure without redispatch."""
