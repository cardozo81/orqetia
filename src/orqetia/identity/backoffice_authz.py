"""Local Backoffice authorization bindings over externally authenticated humans."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class BackofficeBindingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class BackofficeRole(StrEnum):
    ADMIN = "ADMIN"
    PROVIDER_OPERATOR = "PROVIDER_OPERATOR"
    FINANCE_ANALYST = "FINANCE_ANALYST"
    SECURITY_ADMIN = "SECURITY_ADMIN"
    SUPPORT_READONLY = "SUPPORT_READONLY"


ROLE_MATRIX_VERSION = 1

_ROLE_PERMISSIONS: dict[BackofficeRole, frozenset[str]] = {
    BackofficeRole.ADMIN: frozenset(
        {
            "tenancy:admin",
            "users:admin",
            "policy:admin",
            "providers:admin",
            "client-credentials:admin",
            "quotas:admin",
            "reports:read",
            "reports:export",
            "security:admin",
        }
    ),
    BackofficeRole.PROVIDER_OPERATOR: frozenset(
        {
            "policy:admin",
            "providers:admin",
            "reports:read",
        }
    ),
    BackofficeRole.FINANCE_ANALYST: frozenset(
        {
            "reports:read",
            "reports:export",
        }
    ),
    BackofficeRole.SECURITY_ADMIN: frozenset(
        {
            "users:admin",
            "security:admin",
            "reports:read",
        }
    ),
    BackofficeRole.SUPPORT_READONLY: frozenset({"reports:read"}),
}

_SENSITIVE_PERMISSIONS = frozenset(
    {
        "tenancy:admin",
        "users:admin",
        "policy:admin",
        "providers:admin",
        "client-credentials:admin",
        "quotas:admin",
        "reports:export",
        "security:admin",
    }
)


@dataclass(frozen=True)
class BackofficeUserBinding:
    binding_id: UUID
    issuer: str
    subject: str
    status: BackofficeBindingStatus
    roles: tuple[BackofficeRole, ...]
    role_matrix_version: int
    created_at: datetime
    updated_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        issuer = self.issuer.strip()
        subject = self.subject.strip()
        if not issuer.startswith("https://") or len(issuer) > 500:
            raise ValueError("issuer must be an https URL-like identifier")
        if not subject or len(subject) > 500:
            raise ValueError("subject must contain 1..500 characters")
        if any(not isinstance(role, BackofficeRole) for role in self.roles):
            raise ValueError("Backoffice binding contains unknown role")
        roles = tuple(sorted(set(self.roles), key=lambda item: item.value))
        if not roles:
            raise ValueError("Backoffice binding requires at least one role")
        object.__setattr__(self, "issuer", issuer)
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "roles", roles)
        if self.role_matrix_version != ROLE_MATRIX_VERSION:
            raise ValueError("unsupported role_matrix_version")
        if self.version < 1:
            raise ValueError("binding version must be positive")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

    @property
    def permissions(self) -> frozenset[str]:
        output: set[str] = set()
        for role in self.roles:
            output.update(_ROLE_PERMISSIONS[role])
        return frozenset(output)


@dataclass(frozen=True)
class HumanAuthenticationContext:
    issuer: str
    subject: str
    authenticated_at: datetime
    mfa_satisfied: bool
    amr: tuple[str, ...] = ()
    acr: str | None = None

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.subject.strip():
            raise ValueError("issuer and subject are required")
        _aware(self.authenticated_at, "authenticated_at")
        normalized_amr = tuple(sorted(set(value.strip() for value in self.amr if value.strip())))
        object.__setattr__(self, "amr", normalized_amr)


@dataclass(frozen=True)
class BackofficePrincipal:
    binding_id: UUID
    issuer: str
    subject: str
    roles: tuple[BackofficeRole, ...]
    permissions: frozenset[str]
    binding_version: int
    role_matrix_version: int
    authenticated_at: datetime
    mfa_satisfied: bool
    amr: tuple[str, ...]
    acr: str | None


@dataclass(frozen=True)
class BackofficeAuthzAuditEvent:
    action: str
    binding_id: UUID
    issuer: str
    subject: str
    occurred_at: datetime
    result: str

    def __post_init__(self) -> None:
        if not self.action.strip() or len(self.action) > 100:
            raise ValueError("audit action must contain 1..100 characters")
        if self.result not in {"SUCCESS", "DENIED"}:
            raise ValueError("audit result must be SUCCESS or DENIED")
        _aware(self.occurred_at, "occurred_at")


class BackofficeBindingRepository(Protocol):
    async def create(
        self,
        binding: BackofficeUserBinding,
    ) -> BackofficeUserBinding: ...

    async def get_by_identity(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> BackofficeUserBinding | None: ...

    async def get(self, binding_id: UUID) -> BackofficeUserBinding | None: ...

    async def replace(
        self,
        binding: BackofficeUserBinding,
        *,
        expected_version: int,
    ) -> BackofficeUserBinding: ...


class BackofficeAuthzAuditSink(Protocol):
    async def record(self, event: BackofficeAuthzAuditEvent) -> None: ...


class InMemoryBackofficeBindingRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, BackofficeUserBinding] = {}
        self._identity: dict[tuple[str, str], UUID] = {}

    async def create(self, binding: BackofficeUserBinding) -> BackofficeUserBinding:
        identity = (binding.issuer, binding.subject)
        if identity in self._identity:
            raise ValueError("Backoffice identity binding already exists")
        self._items[binding.binding_id] = binding
        self._identity[identity] = binding.binding_id
        return binding

    async def get_by_identity(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> BackofficeUserBinding | None:
        binding_id = self._identity.get((issuer.strip(), subject.strip()))
        return None if binding_id is None else self._items[binding_id]

    async def get(self, binding_id: UUID) -> BackofficeUserBinding | None:
        return self._items.get(binding_id)

    async def replace(
        self,
        binding: BackofficeUserBinding,
        *,
        expected_version: int,
    ) -> BackofficeUserBinding:
        current = self._items.get(binding.binding_id)
        if current is None:
            raise LookupError("Backoffice binding not found")
        if current.version != expected_version:
            raise ValueError("Backoffice binding version conflict")
        if (current.issuer, current.subject) != (binding.issuer, binding.subject):
            raise ValueError("external identity binding is immutable")
        self._items[binding.binding_id] = binding
        return binding


class InMemoryBackofficeAuthzAuditSink:
    def __init__(self) -> None:
        self.events: list[BackofficeAuthzAuditEvent] = []

    async def record(self, event: BackofficeAuthzAuditEvent) -> None:
        self.events.append(event)


class BackofficeAuthorizationService:
    def __init__(
        self,
        *,
        repository: BackofficeBindingRepository,
        audit: BackofficeAuthzAuditSink,
        step_up_max_age: timedelta = timedelta(minutes=5),
    ) -> None:
        if step_up_max_age <= timedelta(0):
            raise ValueError("step_up_max_age must be positive")
        self._repository = repository
        self._audit = audit
        self._step_up_max_age = step_up_max_age

    async def create_binding(
        self,
        *,
        issuer: str,
        subject: str,
        roles: tuple[BackofficeRole, ...],
        occurred_at: datetime,
    ) -> BackofficeUserBinding:
        _aware(occurred_at, "occurred_at")
        binding = BackofficeUserBinding(
            binding_id=uuid7(),
            issuer=issuer,
            subject=subject,
            status=BackofficeBindingStatus.ACTIVE,
            roles=roles,
            role_matrix_version=ROLE_MATRIX_VERSION,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        saved = await self._repository.create(binding)
        await self._audit_event(saved, "BINDING_CREATE", "SUCCESS", occurred_at)
        return saved

    async def set_roles(
        self,
        *,
        binding_id: UUID,
        roles: tuple[BackofficeRole, ...],
        occurred_at: datetime,
    ) -> BackofficeUserBinding:
        current = await self._required(binding_id)
        updated = replace(
            current,
            roles=roles,
            updated_at=occurred_at,
            version=current.version + 1,
        )
        saved = await self._repository.replace(
            updated,
            expected_version=current.version,
        )
        await self._audit_event(saved, "ROLES_UPDATE", "SUCCESS", occurred_at)
        return saved

    async def set_status(
        self,
        *,
        binding_id: UUID,
        status: BackofficeBindingStatus,
        occurred_at: datetime,
    ) -> BackofficeUserBinding:
        current = await self._required(binding_id)
        if current.status is status:
            return current
        updated = replace(
            current,
            status=status,
            updated_at=occurred_at,
            version=current.version + 1,
        )
        saved = await self._repository.replace(
            updated,
            expected_version=current.version,
        )
        await self._audit_event(
            saved,
            f"BINDING_{status.value}",
            "SUCCESS",
            occurred_at,
        )
        return saved

    async def authenticate(
        self,
        *,
        context: HumanAuthenticationContext,
        occurred_at: datetime,
    ) -> BackofficePrincipal:
        _aware(occurred_at, "occurred_at")
        binding = await self._repository.get_by_identity(
            issuer=context.issuer,
            subject=context.subject,
        )
        if binding is None:
            raise PermissionError("Backoffice identity is not bound")
        if binding.status is not BackofficeBindingStatus.ACTIVE:
            await self._audit_event(binding, "AUTHORIZATION", "DENIED", occurred_at)
            raise PermissionError("Backoffice binding is disabled")
        return BackofficePrincipal(
            binding_id=binding.binding_id,
            issuer=binding.issuer,
            subject=binding.subject,
            roles=binding.roles,
            permissions=binding.permissions,
            binding_version=binding.version,
            role_matrix_version=binding.role_matrix_version,
            authenticated_at=context.authenticated_at,
            mfa_satisfied=context.mfa_satisfied,
            amr=context.amr,
            acr=context.acr,
        )

    def require_permission(
        self,
        *,
        principal: BackofficePrincipal,
        permission: str,
        occurred_at: datetime,
    ) -> None:
        _aware(occurred_at, "occurred_at")
        if permission not in principal.permissions:
            raise PermissionError("Backoffice permission denied")
        if permission in _SENSITIVE_PERMISSIONS:
            self.require_step_up(principal=principal, occurred_at=occurred_at)

    def require_step_up(
        self,
        *,
        principal: BackofficePrincipal,
        occurred_at: datetime,
    ) -> None:
        _aware(occurred_at, "occurred_at")
        if not principal.mfa_satisfied:
            raise PermissionError("recent MFA/step-up is required")
        age = occurred_at - principal.authenticated_at
        if age < timedelta(0) or age > self._step_up_max_age:
            raise PermissionError("recent MFA/step-up is required")

    async def _required(self, binding_id: UUID) -> BackofficeUserBinding:
        binding = await self._repository.get(binding_id)
        if binding is None:
            raise LookupError("Backoffice binding not found")
        return binding

    async def _audit_event(
        self,
        binding: BackofficeUserBinding,
        action: str,
        result: str,
        occurred_at: datetime,
    ) -> None:
        await self._audit.record(
            BackofficeAuthzAuditEvent(
                action=action,
                binding_id=binding.binding_id,
                issuer=binding.issuer,
                subject=binding.subject,
                occurred_at=occurred_at,
                result=result,
            )
        )
