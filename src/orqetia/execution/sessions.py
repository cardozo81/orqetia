"""Durable ExecutionSession contract owned by the Execution bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"

    @property
    def terminal(self) -> bool:
        return self is not SessionStatus.ACTIVE


class TargetHealth(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class QuarantineState(StrEnum):
    NONE = "NONE"
    TEMPORARY = "TEMPORARY"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True)
class OwnershipScope:
    tenant_id: UUID
    client_id: UUID


@dataclass(frozen=True)
class ExecutionTargetSnapshot:
    provider_id: str
    model_id: str
    reasoning_profile: str

    def __post_init__(self) -> None:
        values = (
            ("provider_id", self.provider_id, 100),
            ("model_id", self.model_id, 200),
            ("reasoning_profile", self.reasoning_profile, 100),
        )
        for field, value, maximum in values:
            if not value.strip():
                raise ValueError(f"{field} is required")
            if len(value) > maximum:
                raise ValueError(f"{field} exceeds {maximum} characters")


@dataclass(frozen=True)
class SessionPolicySnapshot:
    effective_policy_version_id: UUID
    authorized_targets: tuple[ExecutionTargetSnapshot, ...]
    requested_policy_version_id: UUID | None = None

    def __post_init__(self) -> None:
        if len(set(self.authorized_targets)) != len(self.authorized_targets):
            raise ValueError("authorized_targets must not contain duplicates")


@dataclass(frozen=True)
class TargetRuntimeState:
    target: ExecutionTargetSnapshot
    health: TargetHealth
    quarantine: QuarantineState
    updated_at: datetime
    quarantine_until: datetime | None = None
    quarantine_reason_code: str | None = None

    def __post_init__(self) -> None:
        _require_aware(self.updated_at, "updated_at")
        if self.quarantine_until is not None:
            _require_aware(self.quarantine_until, "quarantine_until")
        if self.quarantine_reason_code is not None and len(self.quarantine_reason_code) > 200:
            raise ValueError("quarantine_reason_code exceeds 200 characters")

        if self.quarantine is QuarantineState.NONE and self.quarantine_until is not None:
            raise ValueError("non-quarantined target cannot have quarantine_until")
        if self.quarantine is QuarantineState.TEMPORARY and self.quarantine_until is None:
            raise ValueError("temporary quarantine requires quarantine_until")
        if self.quarantine is QuarantineState.TERMINAL and self.quarantine_until is not None:
            raise ValueError("terminal quarantine cannot auto-expire")


@dataclass(frozen=True)
class ExecutionSession:
    session_id: UUID
    ownership: OwnershipScope
    status: SessionStatus
    policy: SessionPolicySnapshot
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    terminal_at: datetime | None = None
    external_reference: str | None = None
    target_runtime: tuple[TargetRuntimeState, ...] = ()
    usage_reference_ids: tuple[UUID, ...] = ()
    internal_cost_reference_ids: tuple[UUID, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "expires_at")
            if self.expires_at < self.created_at:
                raise ValueError("expires_at cannot precede created_at")
        if self.terminal_at is not None:
            _require_aware(self.terminal_at, "terminal_at")

        if self.status.terminal != (self.terminal_at is not None):
            raise ValueError("terminal session status and terminal_at must agree")
        if self.version < 1:
            raise ValueError("version must be positive")
        if self.external_reference is not None and len(self.external_reference) > 200:
            raise ValueError("external_reference exceeds 200 characters")
        if len(set(self.usage_reference_ids)) != len(self.usage_reference_ids):
            raise ValueError("usage_reference_ids must not contain duplicates")
        if len(set(self.internal_cost_reference_ids)) != len(self.internal_cost_reference_ids):
            raise ValueError("internal_cost_reference_ids must not contain duplicates")

        authorized = set(self.policy.authorized_targets)
        if any(state.target not in authorized for state in self.target_runtime):
            raise ValueError("target runtime state must refer to an authorized target")


class ExecutionSessionStore(Protocol):
    async def create(self, session: ExecutionSession) -> None:
        """Persist a new session and its immutable effective policy snapshot."""

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        session_id: UUID,
    ) -> ExecutionSession | None:
        """Read only when authoritative tenant/client ownership matches."""

    async def put_target_runtime_state(
        self,
        *,
        scope: OwnershipScope,
        session_id: UUID,
        state: TargetRuntimeState,
    ) -> bool:
        """Persist session-scoped health/quarantine state.

        Returns False when the session is terminal or an existing terminal
        quarantine would be weakened.
        """
