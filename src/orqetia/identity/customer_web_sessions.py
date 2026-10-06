"""Server-side browser sessions for the Customer Portal."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid7

from .backoffice_authz import HumanAuthenticationContext
from .customer_authz import CustomerAuthorizationService, CustomerPrincipal
from .web_sessions import WebSessionRejected


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CustomerPortalWebSession:
    session_id: UUID
    token_hash: str
    csrf_hash: str
    issuer: str
    subject: str
    authenticated_at: datetime
    mfa_satisfied: bool
    amr: tuple[str, ...]
    acr: str | None
    membership_id: UUID | None
    tenant_id: UUID | None
    client_id: UUID | None
    created_at: datetime
    last_activity_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        for field, value in (
            ("token_hash", self.token_hash),
            ("csrf_hash", self.csrf_hash),
        ):
            if len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise ValueError(f"{field} must be lowercase SHA-256 hex")
        if not self.issuer.strip() or not self.subject.strip():
            raise ValueError("session issuer/subject are required")
        selected = (
            self.membership_id,
            self.tenant_id,
            self.client_id,
        )
        if any(value is None for value in selected) and any(
            value is not None for value in selected
        ):
            raise ValueError(
                "membership_id/tenant_id/client_id must be all set or all absent"
            )
        for field, value in (
            ("authenticated_at", self.authenticated_at),
            ("created_at", self.created_at),
            ("last_activity_at", self.last_activity_at),
            ("absolute_expires_at", self.absolute_expires_at),
            ("revoked_at", self.revoked_at),
        ):
            if value is not None:
                _aware(value, field)
        if self.absolute_expires_at <= self.created_at:
            raise ValueError("absolute session expiry must follow creation")
        if self.last_activity_at < self.created_at:
            raise ValueError("last_activity_at cannot precede creation")
        if self.version < 1:
            raise ValueError("session version must be positive")
        object.__setattr__(self, "amr", tuple(sorted(set(self.amr))))

    @property
    def membership_selected(self) -> bool:
        return self.membership_id is not None

    def authentication_context(self) -> HumanAuthenticationContext:
        return HumanAuthenticationContext(
            issuer=self.issuer,
            subject=self.subject,
            authenticated_at=self.authenticated_at,
            mfa_satisfied=self.mfa_satisfied,
            amr=self.amr,
            acr=self.acr,
        )


@dataclass(frozen=True)
class EstablishedCustomerPortalSession:
    session_token: str
    csrf_token: str
    record: CustomerPortalWebSession


@dataclass(frozen=True)
class AuthenticatedCustomerIdentitySession:
    record: CustomerPortalWebSession
    context: HumanAuthenticationContext


@dataclass(frozen=True)
class AuthenticatedCustomerPortalSession:
    record: CustomerPortalWebSession
    principal: CustomerPrincipal


class CustomerPortalWebSessionStore(Protocol):
    async def create(
        self,
        session: CustomerPortalWebSession,
    ) -> CustomerPortalWebSession: ...

    async def get_by_token_hash(
        self,
        token_hash: str,
    ) -> CustomerPortalWebSession | None: ...

    async def replace(
        self,
        session: CustomerPortalWebSession,
        *,
        expected_version: int,
    ) -> CustomerPortalWebSession: ...


class InMemoryCustomerPortalWebSessionStore:
    def __init__(self) -> None:
        self._items: dict[UUID, CustomerPortalWebSession] = {}
        self._by_hash: dict[str, UUID] = {}

    async def create(
        self,
        session: CustomerPortalWebSession,
    ) -> CustomerPortalWebSession:
        if session.token_hash in self._by_hash:
            raise ValueError("customer portal session token collision")
        self._items[session.session_id] = session
        self._by_hash[session.token_hash] = session.session_id
        return session

    async def get_by_token_hash(
        self,
        token_hash: str,
    ) -> CustomerPortalWebSession | None:
        session_id = self._by_hash.get(token_hash)
        return None if session_id is None else self._items[session_id]

    async def replace(
        self,
        session: CustomerPortalWebSession,
        *,
        expected_version: int,
    ) -> CustomerPortalWebSession:
        current = self._items.get(session.session_id)
        if current is None:
            raise LookupError("customer portal session not found")
        if current.version != expected_version:
            raise ValueError("customer portal session version conflict")
        if current.token_hash != session.token_hash:
            raise ValueError("customer portal session token identity is immutable")
        self._items[session.session_id] = session
        return session


class CustomerPortalWebSessionService:
    def __init__(
        self,
        *,
        store: CustomerPortalWebSessionStore,
        authorization: CustomerAuthorizationService,
        idle_timeout: timedelta = timedelta(minutes=30),
        absolute_lifetime: timedelta = timedelta(hours=8),
    ) -> None:
        if idle_timeout <= timedelta(0) or absolute_lifetime <= idle_timeout:
            raise ValueError("invalid customer portal session timeout policy")
        self._store = store
        self._authorization = authorization
        self._idle_timeout = idle_timeout
        self._absolute_lifetime = absolute_lifetime

    async def establish_identity(
        self,
        *,
        context: HumanAuthenticationContext,
        occurred_at: datetime,
    ) -> EstablishedCustomerPortalSession:
        _aware(occurred_at, "occurred_at")
        session_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        session = CustomerPortalWebSession(
            session_id=uuid7(),
            token_hash=_sha256(session_token),
            csrf_hash=_sha256(csrf_token),
            issuer=context.issuer,
            subject=context.subject,
            authenticated_at=context.authenticated_at,
            mfa_satisfied=context.mfa_satisfied,
            amr=context.amr,
            acr=context.acr,
            membership_id=None,
            tenant_id=None,
            client_id=None,
            created_at=occurred_at,
            last_activity_at=occurred_at,
            absolute_expires_at=occurred_at + self._absolute_lifetime,
        )
        saved = await self._store.create(session)
        return EstablishedCustomerPortalSession(
            session_token=session_token,
            csrf_token=csrf_token,
            record=saved,
        )

    async def authenticate_identity(
        self,
        *,
        session_token: str,
        occurred_at: datetime,
    ) -> AuthenticatedCustomerIdentitySession:
        current = await self._validated_session(
            session_token=session_token,
            occurred_at=occurred_at,
        )
        saved = await self._touch(current=current, occurred_at=occurred_at)
        return AuthenticatedCustomerIdentitySession(
            record=saved,
            context=saved.authentication_context(),
        )

    async def bind_membership(
        self,
        *,
        session_token: str,
        membership_id: UUID,
        occurred_at: datetime,
    ) -> AuthenticatedCustomerPortalSession:
        current = await self._validated_session(
            session_token=session_token,
            occurred_at=occurred_at,
        )
        if (
            current.membership_id is not None
            and current.membership_id != membership_id
        ):
            raise WebSessionRejected(
                "customer portal membership is immutable for this session"
            )
        try:
            principal = await self._authorization.authenticate_membership(
                context=current.authentication_context(),
                membership_id=membership_id,
                occurred_at=occurred_at,
            )
        except PermissionError as error:
            raise WebSessionRejected(
                "customer portal membership selection rejected"
            ) from error

        selected = replace(
            current,
            membership_id=principal.membership_id,
            tenant_id=principal.tenant_id,
            client_id=principal.client_id,
            last_activity_at=occurred_at,
            version=current.version + 1,
        )
        saved = await self._store.replace(
            selected,
            expected_version=current.version,
        )
        return AuthenticatedCustomerPortalSession(
            record=saved,
            principal=principal,
        )

    async def authenticate(
        self,
        *,
        session_token: str,
        occurred_at: datetime,
    ) -> AuthenticatedCustomerPortalSession:
        current = await self._validated_session(
            session_token=session_token,
            occurred_at=occurred_at,
        )
        if (
            current.membership_id is None
            or current.tenant_id is None
            or current.client_id is None
        ):
            raise WebSessionRejected("customer portal membership selection required")
        try:
            principal = await self._authorization.authenticate(
                context=current.authentication_context(),
                tenant_id=current.tenant_id,
                client_id=current.client_id,
                occurred_at=occurred_at,
            )
        except PermissionError as error:
            raise WebSessionRejected(
                "customer portal session authorization rejected"
            ) from error
        if principal.membership_id != current.membership_id:
            raise WebSessionRejected("customer portal membership identity changed")
        saved = await self._touch(current=current, occurred_at=occurred_at)
        return AuthenticatedCustomerPortalSession(
            record=saved,
            principal=principal,
        )

    def require_csrf(
        self,
        *,
        session: CustomerPortalWebSession,
        csrf_token: str,
    ) -> None:
        if not csrf_token:
            raise WebSessionRejected("CSRF token is required")
        if not hmac.compare_digest(session.csrf_hash, _sha256(csrf_token)):
            raise WebSessionRejected("CSRF token rejected")

    async def revoke(
        self,
        *,
        session_token: str,
        occurred_at: datetime,
    ) -> None:
        current = await self._validated_session(
            session_token=session_token,
            occurred_at=occurred_at,
            allow_revoked=True,
        )
        if current.revoked_at is not None:
            return
        await self._store.replace(
            replace(
                current,
                revoked_at=occurred_at,
                version=current.version + 1,
            ),
            expected_version=current.version,
        )

    async def _validated_session(
        self,
        *,
        session_token: str,
        occurred_at: datetime,
        allow_revoked: bool = False,
    ) -> CustomerPortalWebSession:
        _aware(occurred_at, "occurred_at")
        if not session_token:
            raise WebSessionRejected("customer portal session is required")
        current = await self._store.get_by_token_hash(_sha256(session_token))
        if current is None:
            raise WebSessionRejected("customer portal session is invalid")
        if current.revoked_at is not None and not allow_revoked:
            raise WebSessionRejected("customer portal session is revoked")
        if occurred_at >= current.absolute_expires_at:
            raise WebSessionRejected("customer portal session expired")
        if occurred_at - current.last_activity_at > self._idle_timeout:
            raise WebSessionRejected("customer portal session idle timeout")
        return current

    async def _touch(
        self,
        *,
        current: CustomerPortalWebSession,
        occurred_at: datetime,
    ) -> CustomerPortalWebSession:
        touched = replace(
            current,
            last_activity_at=occurred_at,
            version=current.version + 1,
        )
        return await self._store.replace(
            touched,
            expected_version=current.version,
        )
