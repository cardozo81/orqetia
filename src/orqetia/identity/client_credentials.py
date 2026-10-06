"""Hashed ORQETIA client access-credential lifecycle and bearer authentication."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7

from .authentication import (
    AuthenticatedPrincipal,
    AuthenticationBackendUnavailable,
    AuthenticationRejected,
)

_PBKDF2_ITERATIONS = 120_000
_TOKEN_PREFIX = "oqt"


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ClientCredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class CredentialOperation(StrEnum):
    ISSUE = "ISSUE"
    ROTATE = "ROTATE"
    REVOKE = "REVOKE"


class IdempotencyConflict(ValueError):
    """One idempotency key was reused for a different credential mutation."""


class OneTimeCredentialSecret:
    """One-time application-layer secret wrapper; string/repr are always redacted."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value: str | None = value

    def reveal_once(self) -> str:
        value = self._value
        if value is None:
            raise RuntimeError("credential secret was already revealed")
        self._value = None
        return value

    def __repr__(self) -> str:
        return "OneTimeCredentialSecret('[REDACTED]')"

    def __str__(self) -> str:
        return "[REDACTED]"


@dataclass(frozen=True)
class ClientAccessCredential:
    credential_id: UUID
    tenant_id: UUID
    client_id: UUID
    display_label: str
    scopes: tuple[str, ...]
    secret_salt: str
    secret_hash: str
    fingerprint: str
    key_version: int
    state_version: int
    status: ClientCredentialStatus
    created_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.display_label.strip() or len(self.display_label) > 200:
            raise ValueError("display_label must contain 1..200 characters")
        normalized_scopes = tuple(sorted(set(self.scopes)))
        if not normalized_scopes:
            raise ValueError("client credential requires at least one scope")
        if any(not scope.strip() or len(scope) > 100 for scope in normalized_scopes):
            raise ValueError("credential scopes must contain 1..100 characters")
        object.__setattr__(self, "scopes", normalized_scopes)
        if len(self.secret_salt) != 32 or any(
            char not in "0123456789abcdef" for char in self.secret_salt
        ):
            raise ValueError("secret_salt must be 16-byte lowercase hex")
        if len(self.secret_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.secret_hash
        ):
            raise ValueError("secret_hash must be lowercase SHA-256 hex")
        if len(self.fingerprint) != 16 or any(
            char not in "0123456789abcdef" for char in self.fingerprint
        ):
            raise ValueError("fingerprint must be 16 lowercase hex characters")
        if self.key_version < 1 or self.state_version < 1:
            raise ValueError("credential versions must be positive")
        _require_aware(self.created_at, "created_at")
        for field, value in (
            ("rotated_at", self.rotated_at),
            ("revoked_at", self.revoked_at),
            ("expires_at", self.expires_at),
            ("last_used_at", self.last_used_at),
        ):
            if value is not None:
                _require_aware(value, field)
        if self.status is ClientCredentialStatus.ACTIVE and self.revoked_at is not None:
            raise ValueError("active client credential cannot have revoked_at")
        if self.status is ClientCredentialStatus.REVOKED and self.revoked_at is None:
            raise ValueError("revoked client credential requires revoked_at")

    def safe_view(self) -> dict[str, object]:
        return {
            "credential_id": str(self.credential_id),
            "display_label": self.display_label,
            "scopes": list(self.scopes),
            "fingerprint": self.fingerprint,
            "key_version": self.key_version,
            "status": self.status.value,
            "created_at": self.created_at,
            "rotated_at": self.rotated_at,
            "revoked_at": self.revoked_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
        }


@dataclass(frozen=True)
class CredentialMutationResult:
    credential: ClientAccessCredential
    secret: OneTimeCredentialSecret | None
    replayed: bool


class ClientCredentialStore(Protocol):
    async def issue(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at: datetime,
    ) -> tuple[ClientAccessCredential, bool]: ...

    async def rotate(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at: datetime,
    ) -> tuple[ClientAccessCredential, bool]: ...

    async def revoke(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at: datetime,
    ) -> tuple[ClientAccessCredential, bool]: ...

    async def get(self, credential_id: UUID) -> ClientAccessCredential | None: ...

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[ClientAccessCredential, ...]: ...

    async def record_last_used(
        self,
        *,
        credential_id: UUID,
        occurred_at: datetime,
    ) -> None: ...


class InMemoryClientCredentialStore:
    def __init__(self) -> None:
        self._items: dict[UUID, ClientAccessCredential] = {}
        self._operations: dict[
            tuple[UUID, UUID, CredentialOperation, str],
            tuple[str, UUID],
        ] = {}
        self._lock = asyncio.Lock()

    async def issue(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at: datetime,
    ) -> tuple[ClientAccessCredential, bool]:
        del occurred_at
        async with self._lock:
            replay = self._replay(
                tenant_id=credential.tenant_id,
                client_id=credential.client_id,
                operation=CredentialOperation.ISSUE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return self._items[replay], True
            if credential.credential_id in self._items:
                raise ValueError("client credential already exists")
            self._items[credential.credential_id] = credential
            self._remember(
                credential=credential,
                operation=CredentialOperation.ISSUE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            return credential, False

    async def rotate(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at: datetime,
    ) -> tuple[ClientAccessCredential, bool]:
        del occurred_at
        async with self._lock:
            replay = self._replay(
                tenant_id=credential.tenant_id,
                client_id=credential.client_id,
                operation=CredentialOperation.ROTATE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return self._items[replay], True
            current = self._items.get(credential.credential_id)
            if current is None:
                raise LookupError("client credential not found")
            if current.state_version + 1 != credential.state_version:
                raise ValueError("client credential version conflict")
            self._items[credential.credential_id] = credential
            self._remember(
                credential=credential,
                operation=CredentialOperation.ROTATE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            return credential, False

    async def revoke(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at: datetime,
    ) -> tuple[ClientAccessCredential, bool]:
        del occurred_at
        async with self._lock:
            replay = self._replay(
                tenant_id=credential.tenant_id,
                client_id=credential.client_id,
                operation=CredentialOperation.REVOKE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return self._items[replay], True
            current = self._items.get(credential.credential_id)
            if current is None:
                raise LookupError("client credential not found")
            if current.status is ClientCredentialStatus.REVOKED:
                saved = current
            else:
                if current.state_version + 1 != credential.state_version:
                    raise ValueError("client credential version conflict")
                self._items[credential.credential_id] = credential
                saved = credential
            self._remember(
                credential=saved,
                operation=CredentialOperation.REVOKE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
            )
            return saved, False

    async def get(self, credential_id: UUID) -> ClientAccessCredential | None:
        return self._items.get(credential_id)

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[ClientAccessCredential, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._items.values()
                    if item.tenant_id == tenant_id and item.client_id == client_id
                ),
                key=lambda item: (item.created_at, str(item.credential_id)),
            )
        )

    async def record_last_used(
        self,
        *,
        credential_id: UUID,
        occurred_at: datetime,
    ) -> None:
        _require_aware(occurred_at, "occurred_at")
        async with self._lock:
            current = self._items.get(credential_id)
            if current is None:
                raise LookupError("client credential not found")
            if current.last_used_at is None or occurred_at > current.last_used_at:
                self._items[credential_id] = replace(
                    current,
                    last_used_at=occurred_at,
                )

    def _replay(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        operation: CredentialOperation,
        key_hash: str,
        request_fingerprint: str,
    ) -> UUID | None:
        existing = self._operations.get(
            (tenant_id, client_id, operation, key_hash)
        )
        if existing is None:
            return None
        prior_fingerprint, credential_id = existing
        if prior_fingerprint != request_fingerprint:
            raise IdempotencyConflict(
                "idempotency key reused with different credential request"
            )
        return credential_id

    def _remember(
        self,
        *,
        credential: ClientAccessCredential,
        operation: CredentialOperation,
        key_hash: str,
        request_fingerprint: str,
    ) -> None:
        self._operations[
            (
                credential.tenant_id,
                credential.client_id,
                operation,
                key_hash,
            )
        ] = (request_fingerprint, credential.credential_id)


class ClientAccessCredentialService:
    def __init__(self, store: ClientCredentialStore) -> None:
        self._store = store

    async def issue(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        display_label: str,
        scopes: tuple[str, ...],
        idempotency_key: str,
        occurred_at: datetime,
        expires_at: datetime | None = None,
    ) -> CredentialMutationResult:
        _validate_idempotency_key(idempotency_key)
        _require_aware(occurred_at, "occurred_at")
        if expires_at is not None:
            _require_aware(expires_at, "expires_at")
            if expires_at <= occurred_at:
                raise ValueError("expires_at must be after occurred_at")

        request_fingerprint = _request_fingerprint(
            {
                "operation": CredentialOperation.ISSUE.value,
                "display_label": display_label,
                "scopes": sorted(set(scopes)),
                "expires_at": (
                    None if expires_at is None else expires_at.isoformat()
                ),
            }
        )
        credential, token = _new_credential(
            credential_id=uuid7(),
            tenant_id=tenant_id,
            client_id=client_id,
            display_label=display_label,
            scopes=scopes,
            key_version=1,
            state_version=1,
            created_at=occurred_at,
            expires_at=expires_at,
        )
        saved, replayed = await self._store.issue(
            credential=credential,
            key_hash=_key_hash(idempotency_key),
            request_fingerprint=request_fingerprint,
            occurred_at=occurred_at,
        )
        return CredentialMutationResult(
            credential=saved,
            secret=None if replayed else OneTimeCredentialSecret(token),
            replayed=replayed,
        )

    async def rotate(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        credential_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> CredentialMutationResult:
        _validate_idempotency_key(idempotency_key)
        _require_aware(occurred_at, "occurred_at")
        existing = await self._required_owned(
            tenant_id=tenant_id,
            client_id=client_id,
            credential_id=credential_id,
        )
        if existing.status is not ClientCredentialStatus.ACTIVE:
            raise ValueError("client credential is not active")

        request_fingerprint = _request_fingerprint(
            {
                "operation": CredentialOperation.ROTATE.value,
                "credential_id": str(credential_id),
            }
        )
        replacement, token = _new_credential(
            credential_id=credential_id,
            tenant_id=tenant_id,
            client_id=client_id,
            display_label=existing.display_label,
            scopes=existing.scopes,
            key_version=existing.key_version + 1,
            state_version=existing.state_version + 1,
            created_at=existing.created_at,
            expires_at=existing.expires_at,
            rotated_at=occurred_at,
            last_used_at=existing.last_used_at,
        )
        saved, replayed = await self._store.rotate(
            credential=replacement,
            key_hash=_key_hash(idempotency_key),
            request_fingerprint=request_fingerprint,
            occurred_at=occurred_at,
        )
        return CredentialMutationResult(
            credential=saved,
            secret=None if replayed else OneTimeCredentialSecret(token),
            replayed=replayed,
        )

    async def revoke(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        credential_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> CredentialMutationResult:
        _validate_idempotency_key(idempotency_key)
        _require_aware(occurred_at, "occurred_at")
        existing = await self._required_owned(
            tenant_id=tenant_id,
            client_id=client_id,
            credential_id=credential_id,
        )
        request_fingerprint = _request_fingerprint(
            {
                "operation": CredentialOperation.REVOKE.value,
                "credential_id": str(credential_id),
            }
        )
        if existing.status is ClientCredentialStatus.REVOKED:
            revoked = existing
        else:
            revoked = replace(
                existing,
                status=ClientCredentialStatus.REVOKED,
                state_version=existing.state_version + 1,
                revoked_at=occurred_at,
            )
        saved, replayed = await self._store.revoke(
            credential=revoked,
            key_hash=_key_hash(idempotency_key),
            request_fingerprint=request_fingerprint,
            occurred_at=occurred_at,
        )
        return CredentialMutationResult(
            credential=saved,
            secret=None,
            replayed=replayed,
        )

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[ClientAccessCredential, ...]:
        return await self._store.list_owned(
            tenant_id=tenant_id,
            client_id=client_id,
        )

    async def _required_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        credential_id: UUID,
    ) -> ClientAccessCredential:
        credential = await self._store.get(credential_id)
        if credential is None:
            raise LookupError("client credential not found")
        if credential.tenant_id != tenant_id or credential.client_id != client_id:
            raise PermissionError("client credential ownership mismatch")
        return credential


class StoredClientCredentialAuthenticator:
    def __init__(
        self,
        *,
        store: ClientCredentialStore,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))

    async def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal:
        credential_id = _credential_id_from_token(token)
        if credential_id is None:
            raise AuthenticationRejected()
        try:
            credential = await self._store.get(credential_id)
        except Exception as error:
            raise AuthenticationBackendUnavailable() from error
        if credential is None:
            raise AuthenticationRejected()

        occurred_at = self._now()
        _require_aware(occurred_at, "authentication time")
        if credential.status is not ClientCredentialStatus.ACTIVE:
            raise AuthenticationRejected()
        if credential.expires_at is not None and credential.expires_at <= occurred_at:
            raise AuthenticationRejected()
        if not _verify_token(token, credential):
            raise AuthenticationRejected()
        try:
            await self._store.record_last_used(
                credential_id=credential.credential_id,
                occurred_at=occurred_at,
            )
        except Exception as error:
            raise AuthenticationBackendUnavailable() from error

        return AuthenticatedPrincipal(
            subject_type="SERVICE_CLIENT",
            subject_id=str(credential.credential_id),
            tenant_id=str(credential.tenant_id),
            client_id=str(credential.client_id),
            scopes=frozenset(credential.scopes),
            credential_id=str(credential.credential_id),
            credential_fingerprint=credential.fingerprint,
        )


def _new_credential(
    *,
    credential_id: UUID,
    tenant_id: UUID,
    client_id: UUID,
    display_label: str,
    scopes: tuple[str, ...],
    key_version: int,
    state_version: int,
    created_at: datetime,
    expires_at: datetime | None,
    rotated_at: datetime | None = None,
    last_used_at: datetime | None = None,
) -> tuple[ClientAccessCredential, str]:
    secret = secrets.token_urlsafe(32)
    token = f"{_TOKEN_PREFIX}_{credential_id.hex}_{secret}"
    salt = secrets.token_bytes(16).hex()
    credential = ClientAccessCredential(
        credential_id=credential_id,
        tenant_id=tenant_id,
        client_id=client_id,
        display_label=display_label,
        scopes=scopes,
        secret_salt=salt,
        secret_hash=_derive_hash(token, salt),
        fingerprint=hashlib.sha256(token.encode("utf-8")).hexdigest()[:16],
        key_version=key_version,
        state_version=state_version,
        status=ClientCredentialStatus.ACTIVE,
        created_at=created_at,
        rotated_at=rotated_at,
        expires_at=expires_at,
        last_used_at=last_used_at,
    )
    return credential, token


def _credential_id_from_token(token: str) -> UUID | None:
    prefix, separator, remainder = token.partition("_")
    if separator != "_" or prefix != _TOKEN_PREFIX:
        return None
    raw_id, separator, secret = remainder.partition("_")
    if separator != "_" or not secret:
        return None
    try:
        return UUID(hex=raw_id)
    except ValueError:
        return None


def _verify_token(token: str, credential: ClientAccessCredential) -> bool:
    candidate = _derive_hash(token, credential.secret_salt)
    return hmac.compare_digest(candidate, credential.secret_hash)


def _derive_hash(token: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        token.encode("utf-8"),
        bytes.fromhex(salt_hex),
        _PBKDF2_ITERATIONS,
    ).hex()


def _validate_idempotency_key(value: str) -> None:
    if not value.strip() or len(value.encode("utf-8")) > 200:
        raise ValueError("idempotency_key must contain 1..200 UTF-8 bytes")


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_fingerprint(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
