"""Immutable administrative execution-policy versions and client assignments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from orqetia.execution import (
    ExecutionTargetSnapshot,
    OrchestrationPolicy,
    SessionPolicySnapshot,
)
from uuid import UUID, uuid7


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, order=True)
class AuthorizedExecutionTarget:
    provider_id: str
    model_id: str
    reasoning_profile: str

    def __post_init__(self) -> None:
        for field, value, maximum in (
            ("provider_id", self.provider_id, 100),
            ("model_id", self.model_id, 200),
            ("reasoning_profile", self.reasoning_profile, 100),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must contain 1..{maximum} characters")


@dataclass(frozen=True)
class ExecutionPolicyVersion:
    policy_version_id: UUID
    tenant_id: UUID
    client_id: UUID
    version_number: int
    max_cycles: int
    max_attempts: int
    cycle_delay_seconds: int
    retry_after_cap_seconds: int
    authorized_targets: tuple[AuthorizedExecutionTarget, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.version_number < 1:
            raise ValueError("policy version_number must be positive")
        if self.max_cycles < 1 or self.max_attempts < 1:
            raise ValueError("policy cycle/attempt limits must be positive")
        if self.cycle_delay_seconds < 0:
            raise ValueError("cycle_delay_seconds cannot be negative")
        if self.retry_after_cap_seconds < 0 or self.retry_after_cap_seconds > 300:
            raise ValueError("retry_after_cap_seconds must be between 0 and 300")
        if not self.authorized_targets:
            raise ValueError("execution policy requires at least one authorized target")
        if len(set(self.authorized_targets)) != len(self.authorized_targets):
            raise ValueError("authorized_targets cannot contain duplicates")
        _aware(self.created_at, "created_at")

    def orchestration_policy(self) -> OrchestrationPolicy:
        return OrchestrationPolicy(
            max_cycles=self.max_cycles,
            max_attempts=self.max_attempts,
            cycle_delay_seconds=self.cycle_delay_seconds,
            retry_after_cap_seconds=self.retry_after_cap_seconds,
        )

    def session_policy_snapshot(self) -> SessionPolicySnapshot:
        return SessionPolicySnapshot(
            effective_policy_version_id=self.policy_version_id,
            authorized_targets=tuple(
                ExecutionTargetSnapshot(
                    provider_id=item.provider_id,
                    model_id=item.model_id,
                    reasoning_profile=item.reasoning_profile,
                )
                for item in self.authorized_targets
            ),
        )


@dataclass(frozen=True)
class ClientPolicyAssignment:
    tenant_id: UUID
    client_id: UUID
    policy_version_id: UUID
    assignment_version: int
    assigned_at: datetime

    def __post_init__(self) -> None:
        if self.assignment_version < 1:
            raise ValueError("assignment_version must be positive")
        _aware(self.assigned_at, "assigned_at")


@dataclass(frozen=True)
class EffectiveExecutionPolicy:
    version: ExecutionPolicyVersion
    assignment: ClientPolicyAssignment


class PolicyOwnerResolver(Protocol):
    async def resolve_active_owner(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> object: ...


class ExecutionPolicyRepository(Protocol):
    async def list_versions(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[ExecutionPolicyVersion, ...]: ...

    async def get_effective(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> EffectiveExecutionPolicy | None: ...

    async def publish_and_activate(
        self,
        *,
        version: ExecutionPolicyVersion,
        assignment: ClientPolicyAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveExecutionPolicy: ...


class InMemoryExecutionPolicyRepository:
    def __init__(self) -> None:
        self._versions: dict[UUID, ExecutionPolicyVersion] = {}
        self._assignments: dict[
            tuple[UUID, UUID],
            ClientPolicyAssignment,
        ] = {}

    async def list_versions(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[ExecutionPolicyVersion, ...]:
        return tuple(
            sorted(
                (
                    version
                    for version in self._versions.values()
                    if version.tenant_id == tenant_id
                    and version.client_id == client_id
                ),
                key=lambda item: item.version_number,
            )
        )

    async def get_effective(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> EffectiveExecutionPolicy | None:
        assignment = self._assignments.get((tenant_id, client_id))
        if assignment is None:
            return None
        version = self._versions.get(assignment.policy_version_id)
        if version is None:
            raise RuntimeError("policy assignment references missing version")
        return EffectiveExecutionPolicy(version=version, assignment=assignment)

    async def publish_and_activate(
        self,
        *,
        version: ExecutionPolicyVersion,
        assignment: ClientPolicyAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveExecutionPolicy:
        owner = (version.tenant_id, version.client_id)
        if owner != (assignment.tenant_id, assignment.client_id):
            raise ValueError("policy version/assignment owner mismatch")
        if version.policy_version_id != assignment.policy_version_id:
            raise ValueError("assignment must reference published policy version")
        if version.policy_version_id in self._versions:
            raise ValueError("policy version already exists")
        if any(
            existing.tenant_id == version.tenant_id
            and existing.client_id == version.client_id
            and existing.version_number == version.version_number
            for existing in self._versions.values()
        ):
            raise ValueError("policy version number already exists")

        current = self._assignments.get(owner)
        if current is None:
            if expected_assignment_version is not None:
                raise ValueError("policy assignment version conflict")
            if assignment.assignment_version != 1:
                raise ValueError("initial assignment_version must be 1")
        else:
            if current.assignment_version != expected_assignment_version:
                raise ValueError("policy assignment version conflict")
            if assignment.assignment_version != current.assignment_version + 1:
                raise ValueError("assignment_version must increment by one")

        self._versions[version.policy_version_id] = version
        self._assignments[owner] = assignment
        return EffectiveExecutionPolicy(version=version, assignment=assignment)


class ExecutionPolicyAdminService:
    def __init__(
        self,
        repository: ExecutionPolicyRepository,
        *,
        owner_resolver: PolicyOwnerResolver,
    ) -> None:
        self._repository = repository
        self._owner_resolver = owner_resolver

    async def publish_and_activate(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        max_cycles: int,
        max_attempts: int,
        cycle_delay_seconds: int,
        retry_after_cap_seconds: int,
        authorized_targets: tuple[AuthorizedExecutionTarget, ...],
        occurred_at: datetime,
    ) -> EffectiveExecutionPolicy:
        _aware(occurred_at, "occurred_at")
        await self._owner_resolver.resolve_active_owner(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        current = await self._repository.get_effective(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        versions = await self._repository.list_versions(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        next_number = 1 + max(
            (version.version_number for version in versions),
            default=0,
        )
        policy = ExecutionPolicyVersion(
            policy_version_id=uuid7(),
            tenant_id=tenant_id,
            client_id=client_id,
            version_number=next_number,
            max_cycles=max_cycles,
            max_attempts=max_attempts,
            cycle_delay_seconds=cycle_delay_seconds,
            retry_after_cap_seconds=retry_after_cap_seconds,
            authorized_targets=tuple(sorted(authorized_targets)),
            created_at=occurred_at,
        )
        expected = (
            None
            if current is None
            else current.assignment.assignment_version
        )
        assignment = ClientPolicyAssignment(
            tenant_id=tenant_id,
            client_id=client_id,
            policy_version_id=policy.policy_version_id,
            assignment_version=1 if current is None else expected + 1,
            assigned_at=occurred_at,
        )
        return await self._repository.publish_and_activate(
            version=policy,
            assignment=assignment,
            expected_assignment_version=expected,
        )

    async def resolve_effective(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> EffectiveExecutionPolicy:
        await self._owner_resolver.resolve_active_owner(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        effective = await self._repository.get_effective(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        if effective is None:
            raise LookupError("no effective execution policy")
        return effective
