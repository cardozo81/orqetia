"""Durable provider-attempt contracts for crash-safe worker dispatch."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from orqetia.providers import ProviderAttemptResult, ProviderTarget

from .sessions import OwnershipScope

_OPERATION = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ProviderAttemptStatus(StrEnum):
    READY = "READY"
    DISPATCHING = "DISPATCHING"
    COMPLETED = "COMPLETED"
    AMBIGUOUS = "AMBIGUOUS"

    @property
    def terminal(self) -> bool:
        return self in {
            ProviderAttemptStatus.COMPLETED,
            ProviderAttemptStatus.AMBIGUOUS,
        }


@dataclass(frozen=True)
class ProviderAttempt:
    attempt_id: UUID
    work_id: UUID
    operation: str
    target: ProviderTarget
    cycle: int
    attempt_index: int
    request_reference: str
    request_fingerprint: str
    missing_requirements: tuple[str, ...]
    status: ProviderAttemptStatus
    created_at: datetime
    updated_at: datetime
    ownership: OwnershipScope | None = None
    session_id: UUID | None = None
    task_id: UUID | None = None
    retry_of_attempt_id: UUID | None = None
    fallback_from_attempt_id: UUID | None = None
    dispatch_started_at: datetime | None = None
    terminal_at: datetime | None = None
    result: ProviderAttemptResult | None = None
    ambiguity_error_class: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not _OPERATION.fullmatch(self.operation):
            raise ValueError("operation must use canonical uppercase code format")
        if self.cycle < 1 or self.attempt_index < 1:
            raise ValueError("cycle and attempt_index must be positive")
        if not self.request_reference.strip() or len(self.request_reference) > 500:
            raise ValueError("request_reference must contain 1..500 characters")
        if not _SHA256.fullmatch(self.request_fingerprint):
            raise ValueError("request_fingerprint must be lowercase SHA-256 hex")
        if len(set(self.missing_requirements)) != len(self.missing_requirements):
            raise ValueError("missing_requirements must not contain duplicates")
        if any(not item.strip() or len(item) > 200 for item in self.missing_requirements):
            raise ValueError("missing_requirements contain an invalid requirement")
        if self.task_id is not None and (
            self.session_id is None or self.ownership is None
        ):
            raise ValueError("task-scoped attempt requires session and ownership")
        if (self.retry_of_attempt_id is not None and
            self.retry_of_attempt_id == self.attempt_id):
            raise ValueError("attempt cannot retry itself")
        if (
            self.fallback_from_attempt_id is not None
            and self.fallback_from_attempt_id == self.attempt_id
        ):
            raise ValueError("attempt cannot fall back from itself")
        if self.version < 1:
            raise ValueError("version must be positive")

        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.dispatch_started_at is not None:
            _require_aware(self.dispatch_started_at, "dispatch_started_at")
        if self.terminal_at is not None:
            _require_aware(self.terminal_at, "terminal_at")

        if self.status is ProviderAttemptStatus.READY:
            if (
                self.dispatch_started_at is not None
                or self.terminal_at is not None
                or self.result is not None
                or self.ambiguity_error_class is not None
            ):
                raise ValueError("READY attempt cannot contain dispatch/terminal outcome")
        elif self.status is ProviderAttemptStatus.DISPATCHING:
            if self.dispatch_started_at is None or self.terminal_at is not None:
                raise ValueError("DISPATCHING attempt requires dispatch_started_at only")
            if self.result is not None or self.ambiguity_error_class is not None:
                raise ValueError("DISPATCHING attempt cannot contain terminal outcome")
        elif self.status is ProviderAttemptStatus.COMPLETED:
            if self.dispatch_started_at is None or self.terminal_at is None:
                raise ValueError("COMPLETED attempt requires dispatch and terminal timestamps")
            if self.result is None or self.result.attempt_id != self.attempt_id:
                raise ValueError("COMPLETED attempt requires matching provider result")
            if self.ambiguity_error_class is not None:
                raise ValueError("COMPLETED attempt cannot be ambiguous")
        elif self.status is ProviderAttemptStatus.AMBIGUOUS:
            if self.dispatch_started_at is None or self.terminal_at is None:
                raise ValueError("AMBIGUOUS attempt requires dispatch and terminal timestamps")
            if self.result is not None:
                raise ValueError("AMBIGUOUS attempt cannot contain provider result")
            if not (self.ambiguity_error_class or "").strip():
                raise ValueError("AMBIGUOUS attempt requires ambiguity_error_class")


class ProviderAttemptStore(Protocol):
    async def schedule(
        self,
        attempt: ProviderAttempt,
        *,
        available_at: datetime,
        priority: int = 0,
        max_infrastructure_attempts: int = 5,
    ) -> None: ...

    async def get(self, attempt_id: UUID) -> ProviderAttempt | None: ...

    async def begin_dispatch(
        self,
        attempt_id: UUID,
        *,
        occurred_at: datetime,
        expected_version: int,
    ) -> bool: ...

    async def complete(
        self,
        attempt_id: UUID,
        *,
        result: ProviderAttemptResult,
        occurred_at: datetime,
        expected_version: int,
    ) -> bool: ...

    async def mark_ambiguous(
        self,
        attempt_id: UUID,
        *,
        error_class: str,
        occurred_at: datetime,
        expected_version: int,
    ) -> bool: ...
