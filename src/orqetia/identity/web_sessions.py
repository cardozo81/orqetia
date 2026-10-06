"""Server-side Backoffice browser sessions with revocation and CSRF binding."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid7

from .backoffice_authz import (
    BackofficeAuthorizationService,
    BackofficePrincipal,
    HumanAuthenticationContext,
)


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class WebSessionRejected(PermissionError):
    """Browser session is absent, expired, revoked or otherwise invalid."""


@dataclass(frozen=True)
class BackofficeWebSession:
    session_id: UUID
    token_hash: str
    csrf_hash: str
    issuer: str
    subject: str
    authenticated_at: datetime
    mfa_satisfied: bool
    amr: tuple[str, ...]
    acr: str | None
    created_at: datetime
    last_activity_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        for field, value in (("token_hash", self.token_hash), ("csrf_hash", self.csrf_hash)):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{field} must be lowercase SHA-256 hex")
        if not self.issuer.strip() or not self.subject.strip():
            raise ValueError("session issuer/subject are required")
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


@dataclass(frozen=True)
class EstablishedWebSession:
    session_token: str
    csrf_token: str
    record: BackofficeWebSession


@dataclass(frozen=True)
class AuthenticatedWebSession:
    record: BackofficeWebSession
    principal: BackofficePrincipal


class BackofficeWebSessionStore(Protocol):
    async def create(self, session: BackofficeWebSession) -> BackofficeWebSession: ...

    async def get_by_token_hash(
        self,
        token_hash: str,
    ) -> BackofficeWebSession | None: ...

    async def replace(
        self,
        session: BackofficeWebSession,
        *,
        expected_version: int,
    ) -> BackofficeWebSession: ...


class InMemoryBackofficeWebSessionStore:
    def __init__(self) -> None:
        self._items: dict[UUID, BackofficeWebSession] = {}
        self._by_hash: dict[str, UUID] = {}

    async def create(self, session: BackofficeWebSession) -> BackofficeWebSession:
        if session.token_hash in self._by_hash:
            raise ValueError("web session token collision")
        self._items[session.session_id] = session
        self._by_hash[session.token_hash] = session.session_id
        return session

    async def get_by_token_hash(
        self,
        token_hash: str,
    ) -> BackofficeWebSession | None:
        session_id = self._by_hash.get(token_hash)
        return None if session_id is None else self._items[session_id]

    async def replace(
        self,
        session: BackofficeWebSession,
        *,
        expected_version: int,
    ) -> BackofficeWebSession:
        current = self._items.get(session.session_id)
        if current is None:
            raise LookupError("web session not found")
        if current.version != expected_version:
            raise ValueError("web session version conflict")
        if current.token_hash != session.token_hash:
            raise ValueError("web session token identity is immutable")
        self._items[session.session_id] = session
        return session


class BackofficeWebSessionService:
    def __init__(
        self,
        *,
        store: BackofficeWebSessionStore,
        authorization: BackofficeAuthorizationService,
        idle_timeout: timedelta = timedelta(minutes=15),
        absolute_lifetime: timedelta = timedelta(hours=8),
    ) -> None:
        if idle_timeout <= timedelta(0) or absolute_lifetime <= idle_timeout:
            raise ValueError("invalid web session timeout policy")
        self._store = store
        self._authorization = authorization
        self._idle_timeout = idle_timeout
        self._absolute_lifetime = absolute_lifetime

    async def establish(
        self,
        *,
        principal: BackofficePrincipal,
        occurred_at: datetime,
    ) -> EstablishedWebSession:
        _aware(occurred_at, "occurred_at")
        self._authorization.require_step_up(
            principal=principal,
            occurred_at=occurred_at,
        )
        session_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        session = BackofficeWebSession(
            session_id=uuid7(),
            token_hash=_sha256(session_token),
            csrf_hash=_sha256(csrf_token),
            issuer=principal.issuer,
            subject=principal.subject,
            authenticated_at=principal.authenticated_at,
            mfa_satisfied=principal.mfa_satisfied,
            amr=principal.amr,
            acr=principal.acr,
            created_at=occurred_at,
            last_activity_at=occurred_at,
            absolute_expires_at=occurred_at + self._absolute_lifetime,
        )
        saved = await self._store.create(session)
        return EstablishedWebSession(
            session_token=session_token,
            csrf_token=csrf_token,
            record=saved,
        )

    async def authenticate(
        self,
        *,
        session_token: str,
        occurred_at: datetime,
    ) -> AuthenticatedWebSession:
        _aware(occurred_at, "occurred_at")
        if not session_token:
            raise WebSessionRejected("web session is required")
        current = await self._store.get_by_token_hash(_sha256(session_token))
        if current is None or current.revoked_at is not None:
            raise WebSessionRejected("web session is invalid")
        if occurred_at >= current.absolute_expires_at:
            raise WebSessionRejected("web session expired")
        if occurred_at - current.last_activity_at > self._idle_timeout:
            raise WebSessionRejected("web session idle timeout")

        try:
            principal = await self._authorization.authenticate(
                context=HumanAuthenticationContext(
                    issuer=current.issuer,
                    subject=current.subject,
                    authenticated_at=current.authenticated_at,
                    mfa_satisfied=current.mfa_satisfied,
                    amr=current.amr,
                    acr=current.acr,
                ),
                occurred_at=occurred_at,
            )
        except PermissionError as error:
            raise WebSessionRejected("web session authorization rejected") from error

        touched = replace(
            current,
            last_activity_at=occurred_at,
            version=current.version + 1,
        )
        saved = await self._store.replace(
            touched,
            expected_version=current.version,
        )
        return AuthenticatedWebSession(record=saved, principal=principal)

    def require_csrf(
        self,
        *,
        session: BackofficeWebSession,
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
        _aware(occurred_at, "occurred_at")
        current = await self._store.get_by_token_hash(_sha256(session_token))
        if current is None or current.revoked_at is not None:
            return
        await self._store.replace(
            replace(
                current,
                revoked_at=occurred_at,
                version=current.version + 1,
            ),
            expected_version=current.version,
        )
