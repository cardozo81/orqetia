"""Customer-human identity bindings and client memberships for the Client Portal."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7

from .backoffice_authz import HumanAuthenticationContext


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class CustomerIdentityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class CustomerMembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class CustomerRole(StrEnum):
    OWNER = "OWNER"
    DEVELOPER = "DEVELOPER"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"


CUSTOMER_ROLE_MATRIX_VERSION = 1

_ROLE_PERMISSIONS: dict[CustomerRole, frozenset[str]] = {
    CustomerRole.OWNER: frozenset(
        {
            "portal:read",
            "credentials:read",
            "credentials:write",
            "sessions:read",
            "tasks:read",
            "tasks:write",
            "usage:read",
            "estimates:write",
            "providers:read",
            "audit:read",
            "docs:read",
        }
    ),
    CustomerRole.DEVELOPER: frozenset(
        {
            "portal:read",
            "credentials:read",
            "credentials:write",
            "sessions:read",
            "tasks:read",
            "tasks:write",
            "usage:read",
            "estimates:write",
            "providers:read",
            "docs:read",
        }
    ),
    CustomerRole.ANALYST: frozenset(
        {
            "portal:read",
            "sessions:read",
            "tasks:read",
            "usage:read",
            "providers:read",
            "audit:read",
            "docs:read",
        }
    ),
    CustomerRole.VIEWER: frozenset(
        {
            "portal:read",
            "sessions:read",
            "tasks:read",
            "providers:read",
            "docs:read",
        }
    ),
}

_SENSITIVE_PERMISSIONS = frozenset({"credentials:write"})


@dataclass(frozen=True)
class CustomerIdentity:
    identity_id: UUID
    issuer: str
    subject: str
    status: CustomerIdentityStatus
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
        object.__setattr__(self, "issuer", issuer)
        object.__setattr__(self, "subject", subject)
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.version < 1:
            raise ValueError("customer identity version must be positive")


@dataclass(frozen=True)
class CustomerMembership:
    membership_id: UUID
    identity_id: UUID
    tenant_id: UUID
    client_id: UUID
    roles: tuple[CustomerRole, ...]
    status: CustomerMembershipStatus
    role_matrix_version: int
    created_at: datetime
    updated_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        if any(not isinstance(role, CustomerRole) for role in self.roles):
            raise ValueError("customer membership contains unknown role")
        roles = tuple(sorted(set(self.roles), key=lambda item: item.value))
        if not roles:
            raise ValueError("customer membership requires at least one role")
        object.__setattr__(self, "roles", roles)
        if self.role_matrix_version != CUSTOMER_ROLE_MATRIX_VERSION:
            raise ValueError("unsupported customer role_matrix_version")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.version < 1:
            raise ValueError("customer membership version must be positive")

    @property
    def permissions(self) -> frozenset[str]:
        output: set[str] = set()
        for role in self.roles:
            output.update(_ROLE_PERMISSIONS[role])
        return frozenset(output)


@dataclass(frozen=True)
class CustomerPrincipal:
    identity_id: UUID
    membership_id: UUID
    issuer: str
    subject: str
    tenant_id: UUID
    client_id: UUID
    roles: tuple[CustomerRole, ...]
    permissions: frozenset[str]
    identity_version: int
    membership_version: int
    role_matrix_version: int
    authenticated_at: datetime
    mfa_satisfied: bool
    amr: tuple[str, ...]
    acr: str | None


@dataclass(frozen=True)
class CustomerAuthzAuditEvent:
    action: str
    identity_id: UUID
    occurred_at: datetime
    result: str
    membership_id: UUID | None = None
    tenant_id: UUID | None = None
    client_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.action.strip() or len(self.action) > 100:
            raise ValueError("audit action must contain 1..100 characters")
        if self.result not in {"SUCCESS", "DENIED"}:
            raise ValueError("audit result must be SUCCESS or DENIED")
        _aware(self.occurred_at, "occurred_at")


class CustomerIdentityRepository(Protocol):
    async def create_identity(self, identity: CustomerIdentity) -> CustomerIdentity: ...

    async def get_identity(self, identity_id: UUID) -> CustomerIdentity | None: ...

    async def get_by_external_identity(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> CustomerIdentity | None: ...

    async def replace_identity(
        self,
        identity: CustomerIdentity,
        *,
        expected_version: int,
    ) -> CustomerIdentity: ...

    async def create_membership(
        self,
        membership: CustomerMembership,
    ) -> CustomerMembership: ...

    async def get_membership(
        self,
        membership_id: UUID,
    ) -> CustomerMembership | None: ...

    async def find_membership(
        self,
        *,
        identity_id: UUID,
        tenant_id: UUID,
        client_id: UUID,
    ) -> CustomerMembership | None: ...

    async def list_memberships(
        self,
        *,
        identity_id: UUID,
    ) -> tuple[CustomerMembership, ...]: ...

    async def replace_membership(
        self,
        membership: CustomerMembership,
        *,
        expected_version: int,
    ) -> CustomerMembership: ...


class CustomerOwnerResolver(Protocol):
    async def resolve_active_owner(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> object: ...


class CustomerAuthzAuditSink(Protocol):
    async def record(self, event: CustomerAuthzAuditEvent) -> None: ...


class InMemoryCustomerIdentityRepository:
    def __init__(self) -> None:
        self._identities: dict[UUID, CustomerIdentity] = {}
        self._external: dict[tuple[str, str], UUID] = {}
        self._memberships: dict[UUID, CustomerMembership] = {}
        self._membership_index: dict[tuple[UUID, UUID, UUID], UUID] = {}

    async def create_identity(self, identity: CustomerIdentity) -> CustomerIdentity:
        key = (identity.issuer, identity.subject)
        if key in self._external:
            raise ValueError("customer external identity already exists")
        self._identities[identity.identity_id] = identity
        self._external[key] = identity.identity_id
        return identity

    async def get_identity(self, identity_id: UUID) -> CustomerIdentity | None:
        return self._identities.get(identity_id)

    async def get_by_external_identity(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> CustomerIdentity | None:
        identity_id = self._external.get((issuer.strip(), subject.strip()))
        return None if identity_id is None else self._identities[identity_id]

    async def replace_identity(
        self,
        identity: CustomerIdentity,
        *,
        expected_version: int,
    ) -> CustomerIdentity:
        current = self._identities.get(identity.identity_id)
        if current is None:
            raise LookupError("customer identity not found")
        if current.version != expected_version:
            raise ValueError("customer identity version conflict")
        if (current.issuer, current.subject) != (identity.issuer, identity.subject):
            raise ValueError("customer external identity is immutable")
        self._identities[identity.identity_id] = identity
        return identity

    async def create_membership(
        self,
        membership: CustomerMembership,
    ) -> CustomerMembership:
        if membership.identity_id not in self._identities:
            raise LookupError("customer identity not found")
        key = (membership.identity_id, membership.tenant_id, membership.client_id)
        if key in self._membership_index:
            raise ValueError("customer membership already exists")
        self._memberships[membership.membership_id] = membership
        self._membership_index[key] = membership.membership_id
        return membership

    async def get_membership(
        self,
        membership_id: UUID,
    ) -> CustomerMembership | None:
        return self._memberships.get(membership_id)

    async def find_membership(
        self,
        *,
        identity_id: UUID,
        tenant_id: UUID,
        client_id: UUID,
    ) -> CustomerMembership | None:
        membership_id = self._membership_index.get(
            (identity_id, tenant_id, client_id)
        )
        return None if membership_id is None else self._memberships[membership_id]

    async def list_memberships(
        self,
        *,
        identity_id: UUID,
    ) -> tuple[CustomerMembership, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._memberships.values()
                    if item.identity_id == identity_id
                ),
                key=lambda item: (
                    str(item.tenant_id),
                    str(item.client_id),
                    str(item.membership_id),
                ),
            )
        )

    async def replace_membership(
        self,
        membership: CustomerMembership,
        *,
        expected_version: int,
    ) -> CustomerMembership:
        current = self._memberships.get(membership.membership_id)
        if current is None:
            raise LookupError("customer membership not found")
        if current.version != expected_version:
            raise ValueError("customer membership version conflict")
        if (
            current.identity_id,
            current.tenant_id,
            current.client_id,
        ) != (
            membership.identity_id,
            membership.tenant_id,
            membership.client_id,
        ):
            raise ValueError("customer membership ownership is immutable")
        self._memberships[membership.membership_id] = membership
        return membership


class InMemoryCustomerAuthzAuditSink:
    def __init__(self) -> None:
        self.events: list[CustomerAuthzAuditEvent] = []

    async def record(self, event: CustomerAuthzAuditEvent) -> None:
        self.events.append(event)


class CustomerAuthorizationService:
    def __init__(
        self,
        *,
        repository: CustomerIdentityRepository,
        owner_resolver: CustomerOwnerResolver,
        audit: CustomerAuthzAuditSink,
        step_up_max_age: timedelta = timedelta(minutes=5),
    ) -> None:
        if step_up_max_age <= timedelta(0):
            raise ValueError("step_up_max_age must be positive")
        self._repository = repository
        self._owner_resolver = owner_resolver
        self._audit = audit
        self._step_up_max_age = step_up_max_age

    async def create_identity(
        self,
        *,
        issuer: str,
        subject: str,
        occurred_at: datetime,
    ) -> CustomerIdentity:
        _aware(occurred_at, "occurred_at")
        identity = CustomerIdentity(
            identity_id=uuid7(),
            issuer=issuer,
            subject=subject,
            status=CustomerIdentityStatus.ACTIVE,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        saved = await self._repository.create_identity(identity)
        await self._audit_event(
            action="IDENTITY_CREATE",
            identity=saved,
            membership=None,
            result="SUCCESS",
            occurred_at=occurred_at,
        )
        return saved

    async def set_identity_status(
        self,
        *,
        identity_id: UUID,
        status: CustomerIdentityStatus,
        occurred_at: datetime,
    ) -> CustomerIdentity:
        current = await self._required_identity(identity_id)
        if current.status is status:
            return current
        saved = await self._repository.replace_identity(
            replace(
                current,
                status=status,
                updated_at=occurred_at,
                version=current.version + 1,
            ),
            expected_version=current.version,
        )
        await self._audit_event(
            action=f"IDENTITY_{status.value}",
            identity=saved,
            membership=None,
            result="SUCCESS",
            occurred_at=occurred_at,
        )
        return saved

    async def add_membership(
        self,
        *,
        identity_id: UUID,
        tenant_id: UUID,
        client_id: UUID,
        roles: tuple[CustomerRole, ...],
        occurred_at: datetime,
    ) -> CustomerMembership:
        _aware(occurred_at, "occurred_at")
        identity = await self._required_identity(identity_id)
        if identity.status is not CustomerIdentityStatus.ACTIVE:
            raise PermissionError("customer identity is disabled")
        await self._owner_resolver.resolve_active_owner(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        membership = CustomerMembership(
            membership_id=uuid7(),
            identity_id=identity_id,
            tenant_id=tenant_id,
            client_id=client_id,
            roles=roles,
            status=CustomerMembershipStatus.ACTIVE,
            role_matrix_version=CUSTOMER_ROLE_MATRIX_VERSION,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        saved = await self._repository.create_membership(membership)
        await self._audit_event(
            action="MEMBERSHIP_CREATE",
            identity=identity,
            membership=saved,
            result="SUCCESS",
            occurred_at=occurred_at,
        )
        return saved

    async def set_membership_status(
        self,
        *,
        membership_id: UUID,
        status: CustomerMembershipStatus,
        occurred_at: datetime,
    ) -> CustomerMembership:
        current = await self._required_membership(membership_id)
        if status is CustomerMembershipStatus.ACTIVE:
            await self._owner_resolver.resolve_active_owner(
                tenant_id=current.tenant_id,
                client_id=current.client_id,
            )
        if current.status is status:
            return current
        saved = await self._repository.replace_membership(
            replace(
                current,
                status=status,
                updated_at=occurred_at,
                version=current.version + 1,
            ),
            expected_version=current.version,
        )
        identity = await self._required_identity(current.identity_id)
        await self._audit_event(
            action=f"MEMBERSHIP_{status.value}",
            identity=identity,
            membership=saved,
            result="SUCCESS",
            occurred_at=occurred_at,
        )
        return saved

    async def set_membership_roles(
        self,
        *,
        membership_id: UUID,
        roles: tuple[CustomerRole, ...],
        occurred_at: datetime,
    ) -> CustomerMembership:
        current = await self._required_membership(membership_id)
        updated = replace(
            current,
            roles=roles,
            updated_at=occurred_at,
            version=current.version + 1,
        )
        saved = await self._repository.replace_membership(
            updated,
            expected_version=current.version,
        )
        identity = await self._required_identity(current.identity_id)
        await self._audit_event(
            action="MEMBERSHIP_ROLES_UPDATE",
            identity=identity,
            membership=saved,
            result="SUCCESS",
            occurred_at=occurred_at,
        )
        return saved

    async def list_available_memberships(
        self,
        *,
        context: HumanAuthenticationContext,
    ) -> tuple[CustomerMembership, ...]:
        identity = await self._repository.get_by_external_identity(
            issuer=context.issuer,
            subject=context.subject,
        )
        if identity is None or identity.status is not CustomerIdentityStatus.ACTIVE:
            return ()
        output: list[CustomerMembership] = []
        for membership in await self._repository.list_memberships(
            identity_id=identity.identity_id
        ):
            if membership.status is not CustomerMembershipStatus.ACTIVE:
                continue
            try:
                await self._owner_resolver.resolve_active_owner(
                    tenant_id=membership.tenant_id,
                    client_id=membership.client_id,
                )
            except PermissionError:
                continue
            output.append(membership)
        return tuple(output)

    async def authenticate(
        self,
        *,
        context: HumanAuthenticationContext,
        tenant_id: UUID,
        client_id: UUID,
        occurred_at: datetime,
    ) -> CustomerPrincipal:
        _aware(occurred_at, "occurred_at")
        identity = await self._repository.get_by_external_identity(
            issuer=context.issuer,
            subject=context.subject,
        )
        if identity is None:
            raise PermissionError("customer identity is not provisioned")
        if identity.status is not CustomerIdentityStatus.ACTIVE:
            await self._audit_event(
                action="AUTHORIZATION",
                identity=identity,
                membership=None,
                result="DENIED",
                occurred_at=occurred_at,
            )
            raise PermissionError("customer identity is disabled")

        membership = await self._repository.find_membership(
            identity_id=identity.identity_id,
            tenant_id=tenant_id,
            client_id=client_id,
        )
        if membership is None:
            await self._audit_event(
                action="AUTHORIZATION",
                identity=identity,
                membership=None,
                result="DENIED",
                occurred_at=occurred_at,
            )
            raise PermissionError("customer membership is not provisioned")
        if membership.status is not CustomerMembershipStatus.ACTIVE:
            await self._audit_event(
                action="AUTHORIZATION",
                identity=identity,
                membership=membership,
                result="DENIED",
                occurred_at=occurred_at,
            )
            raise PermissionError("customer membership is disabled")

        await self._owner_resolver.resolve_active_owner(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        return CustomerPrincipal(
            identity_id=identity.identity_id,
            membership_id=membership.membership_id,
            issuer=identity.issuer,
            subject=identity.subject,
            tenant_id=tenant_id,
            client_id=client_id,
            roles=membership.roles,
            permissions=membership.permissions,
            identity_version=identity.version,
            membership_version=membership.version,
            role_matrix_version=membership.role_matrix_version,
            authenticated_at=context.authenticated_at,
            mfa_satisfied=context.mfa_satisfied,
            amr=context.amr,
            acr=context.acr,
        )

    def require_permission(
        self,
        *,
        principal: CustomerPrincipal,
        permission: str,
        occurred_at: datetime,
    ) -> None:
        _aware(occurred_at, "occurred_at")
        if permission not in principal.permissions:
            raise PermissionError("customer permission denied")
        if permission in _SENSITIVE_PERMISSIONS:
            self.require_step_up(principal=principal, occurred_at=occurred_at)

    def require_step_up(
        self,
        *,
        principal: CustomerPrincipal,
        occurred_at: datetime,
    ) -> None:
        _aware(occurred_at, "occurred_at")
        if not principal.mfa_satisfied:
            raise PermissionError("recent MFA/step-up is required")
        age = occurred_at - principal.authenticated_at
        if age < timedelta(0) or age > self._step_up_max_age:
            raise PermissionError("recent MFA/step-up is required")

    async def _required_identity(self, identity_id: UUID) -> CustomerIdentity:
        identity = await self._repository.get_identity(identity_id)
        if identity is None:
            raise LookupError("customer identity not found")
        return identity

    async def _required_membership(
        self,
        membership_id: UUID,
    ) -> CustomerMembership:
        membership = await self._repository.get_membership(membership_id)
        if membership is None:
            raise LookupError("customer membership not found")
        return membership

    async def _audit_event(
        self,
        *,
        action: str,
        identity: CustomerIdentity,
        membership: CustomerMembership | None,
        result: str,
        occurred_at: datetime,
    ) -> None:
        await self._audit.record(
            CustomerAuthzAuditEvent(
                action=action,
                identity_id=identity.identity_id,
                membership_id=(
                    None if membership is None else membership.membership_id
                ),
                tenant_id=None if membership is None else membership.tenant_id,
                client_id=None if membership is None else membership.client_id,
                occurred_at=occurred_at,
                result=result,
            )
        )
