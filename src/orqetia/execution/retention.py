"""Execution-owned retention contracts for client-private artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .sessions import OwnershipScope


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True)
class ArtifactRetentionCutoffs:
    request_before: datetime
    result_before: datetime
    provider_response_before: datetime
    evidence_before: datetime

    def __post_init__(self) -> None:
        for field, value in (
            ("request_before", self.request_before),
            ("result_before", self.result_before),
            ("provider_response_before", self.provider_response_before),
            ("evidence_before", self.evidence_before),
        ):
            _aware(value, field)


@dataclass(frozen=True)
class ClientArtifactRetentionPolicy:
    version: int
    request_days: int
    result_days: int
    provider_response_days: int
    evidence_days: int

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("retention policy version must be positive")
        for field, value in (
            ("request_days", self.request_days),
            ("result_days", self.result_days),
            ("provider_response_days", self.provider_response_days),
            ("evidence_days", self.evidence_days),
        ):
            if value < 1:
                raise ValueError(f"{field} must be positive")

    def cutoffs(self, occurred_at: datetime) -> ArtifactRetentionCutoffs:
        _aware(occurred_at, "occurred_at")
        return ArtifactRetentionCutoffs(
            request_before=occurred_at - timedelta(days=self.request_days),
            result_before=occurred_at - timedelta(days=self.result_days),
            provider_response_before=occurred_at
            - timedelta(days=self.provider_response_days),
            evidence_before=occurred_at - timedelta(days=self.evidence_days),
        )


DEFAULT_CLIENT_ARTIFACT_RETENTION_POLICY = ClientArtifactRetentionPolicy(
    version=1,
    request_days=30,
    result_days=30,
    provider_response_days=14,
    evidence_days=14,
)


@dataclass(frozen=True)
class RetentionHold:
    reason_code: str
    created_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.reason_code.strip() or len(self.reason_code) > 100:
            raise ValueError("retention hold reason_code must contain 1..100 characters")
        _aware(self.created_at, "created_at")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.created_at:
                raise ValueError("retention hold expiry must follow creation")

    def active_at(self, occurred_at: datetime) -> bool:
        _aware(occurred_at, "occurred_at")
        return self.expires_at is None or occurred_at < self.expires_at


@dataclass(frozen=True)
class RetentionPurgeResult:
    policy_version: int | None
    deleted_count: int
    skipped_due_to_hold: bool

    def __post_init__(self) -> None:
        if self.policy_version is not None and self.policy_version < 1:
            raise ValueError("policy_version must be positive")
        if self.deleted_count < 0:
            raise ValueError("deleted_count cannot be negative")
        if self.skipped_due_to_hold and self.deleted_count:
            raise ValueError("held purge cannot delete content")


class ClientArtifactRetentionStore(Protocol):
    async def purge_owned(
        self,
        *,
        scope: OwnershipScope,
        cutoffs: ArtifactRetentionCutoffs,
    ) -> int: ...

    async def purge_owned_all(
        self,
        *,
        scope: OwnershipScope,
    ) -> int: ...


class ClientArtifactRetentionCoordinator:
    """Apply one explicit retention decision to one tenant/client owner."""

    def __init__(self, store: ClientArtifactRetentionStore) -> None:
        self._store = store

    async def purge_due(
        self,
        *,
        scope: OwnershipScope,
        policy: ClientArtifactRetentionPolicy,
        occurred_at: datetime,
        hold: RetentionHold | None,
    ) -> RetentionPurgeResult:
        _aware(occurred_at, "occurred_at")
        if hold is not None and hold.active_at(occurred_at):
            return RetentionPurgeResult(
                policy_version=policy.version,
                deleted_count=0,
                skipped_due_to_hold=True,
            )
        deleted = await self._store.purge_owned(
            scope=scope,
            cutoffs=policy.cutoffs(occurred_at),
        )
        return RetentionPurgeResult(
            policy_version=policy.version,
            deleted_count=deleted,
            skipped_due_to_hold=False,
        )

    async def purge_offboarded_owner(
        self,
        *,
        scope: OwnershipScope,
        occurred_at: datetime,
        hold: RetentionHold | None,
    ) -> RetentionPurgeResult:
        _aware(occurred_at, "occurred_at")
        if hold is not None and hold.active_at(occurred_at):
            return RetentionPurgeResult(
                policy_version=None,
                deleted_count=0,
                skipped_due_to_hold=True,
            )
        deleted = await self._store.purge_owned_all(scope=scope)
        return RetentionPurgeResult(
            policy_version=None,
            deleted_count=deleted,
            skipped_due_to_hold=False,
        )
