"""Provider credential metadata and secret-store lifecycle boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ProviderCredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class SecretValue:
    """Explicitly revealable secret wrapper whose repr/string never exposes material."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not value or not value.strip():
            raise ValueError("secret value cannot be empty")
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "SecretValue('[REDACTED]')"

    def __str__(self) -> str:
        return "[REDACTED]"


@dataclass(frozen=True)
class SecretReference:
    value: str

    def __post_init__(self) -> None:
        if "://" not in self.value or any(char.isspace() for char in self.value):
            raise ValueError("secret reference must be opaque scheme://identifier")


@dataclass(frozen=True)
class ProviderCredentialMetadata:
    credential_id: UUID
    provider_id: str
    provider_account_id: UUID
    secret_reference: SecretReference
    fingerprint: str
    key_version: int
    state_version: int
    status: ProviderCredentialStatus
    created_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime | None = None
    last_successful_use_at: datetime | None = None
    last_failed_use_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or len(self.provider_id) > 100:
            raise ValueError("provider_id must contain 1..100 characters")
        if len(self.fingerprint) != 16 or any(
            char not in "0123456789abcdef" for char in self.fingerprint
        ):
            raise ValueError("fingerprint must be 16 lowercase hex characters")
        if self.key_version < 1:
            raise ValueError("key_version must be positive")
        if self.state_version < 1:
            raise ValueError("state_version must be positive")
        _require_aware(self.created_at, "created_at")
        for field, value in (
            ("rotated_at", self.rotated_at),
            ("revoked_at", self.revoked_at),
            ("expires_at", self.expires_at),
            ("last_successful_use_at", self.last_successful_use_at),
            ("last_failed_use_at", self.last_failed_use_at),
        ):
            if value is not None:
                _require_aware(value, field)
        if self.status is ProviderCredentialStatus.REVOKED and self.revoked_at is None:
            raise ValueError("revoked credential requires revoked_at")

    def safe_view(self) -> dict[str, object]:
        """Backoffice-safe metadata. Secret reference/material are deliberately absent."""

        return {
            "credential_id": str(self.credential_id),
            "provider_id": self.provider_id,
            "provider_account_id": str(self.provider_account_id),
            "fingerprint": self.fingerprint,
            "key_version": self.key_version,
            "state_version": self.state_version,
            "status": self.status.value,
            "created_at": self.created_at,
            "rotated_at": self.rotated_at,
            "revoked_at": self.revoked_at,
            "expires_at": self.expires_at,
            "last_successful_use_at": self.last_successful_use_at,
            "last_failed_use_at": self.last_failed_use_at,
        }


@dataclass(frozen=True)
class CredentialPreflightResult:
    ok: bool
    reason_code: str


@dataclass(frozen=True)
class CredentialAuditEvent:
    credential_id: UUID
    provider_id: str
    action: str
    result: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.occurred_at, "occurred_at")


class ProviderSecretStore(Protocol):
    async def put(self, secret: SecretValue) -> SecretReference: ...

    async def get(self, reference: SecretReference) -> SecretValue: ...

    async def delete(self, reference: SecretReference) -> None: ...


class ProviderCredentialRepository(Protocol):
    async def create(
        self,
        metadata: ProviderCredentialMetadata,
    ) -> ProviderCredentialMetadata: ...

    async def get(self, credential_id: UUID) -> ProviderCredentialMetadata | None: ...

    async def list_active(
        self,
        *,
        provider_account_id: UUID,
    ) -> tuple[ProviderCredentialMetadata, ...]: ...

    async def replace(
        self,
        metadata: ProviderCredentialMetadata,
        *,
        expected_state_version: int,
    ) -> ProviderCredentialMetadata: ...


class ProviderCredentialPreflight(Protocol):
    async def check(
        self,
        *,
        provider_id: str,
        secret: SecretValue,
    ) -> CredentialPreflightResult: ...


class CredentialAuditSink(Protocol):
    async def record(self, event: CredentialAuditEvent) -> None: ...


class InMemoryProviderSecretStore:
    """Test-only secret store. Production composition must use managed/envelope storage."""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    async def put(self, secret: SecretValue) -> SecretReference:
        reference = SecretReference(f"memory://{uuid7()}")
        self._secrets[reference.value] = secret.reveal()
        return reference

    async def get(self, reference: SecretReference) -> SecretValue:
        try:
            value = self._secrets[reference.value]
        except KeyError as error:
            raise LookupError("secret reference not found") from error
        return SecretValue(value)

    async def delete(self, reference: SecretReference) -> None:
        self._secrets.pop(reference.value, None)


class InMemoryProviderCredentialRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, ProviderCredentialMetadata] = {}

    async def create(
        self,
        metadata: ProviderCredentialMetadata,
    ) -> ProviderCredentialMetadata:
        if metadata.credential_id in self._items:
            raise ValueError("provider credential already exists")
        self._items[metadata.credential_id] = metadata
        return metadata

    async def get(self, credential_id: UUID) -> ProviderCredentialMetadata | None:
        return self._items.get(credential_id)

    async def list_active(
        self,
        *,
        provider_account_id: UUID,
    ) -> tuple[ProviderCredentialMetadata, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._items.values()
                    if item.provider_account_id == provider_account_id
                    and item.status is ProviderCredentialStatus.ACTIVE
                ),
                key=lambda item: (item.created_at, str(item.credential_id)),
            )
        )

    async def replace(
        self,
        metadata: ProviderCredentialMetadata,
        *,
        expected_state_version: int,
    ) -> ProviderCredentialMetadata:
        existing = self._items.get(metadata.credential_id)
        if existing is None:
            raise LookupError("provider credential not found")
        if existing.state_version != expected_state_version:
            raise ValueError("provider credential version conflict")
        self._items[metadata.credential_id] = metadata
        return metadata


class InMemoryCredentialAuditSink:
    def __init__(self) -> None:
        self.events: list[CredentialAuditEvent] = []

    async def record(self, event: CredentialAuditEvent) -> None:
        self.events.append(event)


class ProviderCredentialService:
    def __init__(
        self,
        *,
        secrets: ProviderSecretStore,
        repository: ProviderCredentialRepository,
        audit: CredentialAuditSink,
        preflight: ProviderCredentialPreflight | None = None,
    ) -> None:
        self._secrets = secrets
        self._repository = repository
        self._audit = audit
        self._preflight = preflight

    async def create(
        self,
        *,
        provider_id: str,
        provider_account_id: UUID,
        secret: SecretValue,
        occurred_at: datetime,
        expires_at: datetime | None = None,
    ) -> ProviderCredentialMetadata:
        _require_aware(occurred_at, "occurred_at")
        reference = await self._secrets.put(secret)
        metadata = ProviderCredentialMetadata(
            credential_id=uuid7(),
            provider_id=provider_id,
            provider_account_id=provider_account_id,
            secret_reference=reference,
            fingerprint=_fingerprint(secret),
            key_version=1,
            state_version=1,
            status=ProviderCredentialStatus.ACTIVE,
            created_at=occurred_at,
            expires_at=expires_at,
        )
        try:
            created = await self._repository.create(metadata)
        except Exception:
            await self._secrets.delete(reference)
            raise
        await self._audit_event(created, "CREATE", "SUCCESS", occurred_at)
        return created

    async def rotate(
        self,
        *,
        credential_id: UUID,
        new_secret: SecretValue,
        occurred_at: datetime,
    ) -> ProviderCredentialMetadata:
        _require_aware(occurred_at, "occurred_at")
        existing = await self._required_active(credential_id)
        new_reference = await self._secrets.put(new_secret)
        rotated = replace(
            existing,
            secret_reference=new_reference,
            fingerprint=_fingerprint(new_secret),
            key_version=existing.key_version + 1,
            state_version=existing.state_version + 1,
            rotated_at=occurred_at,
        )
        try:
            saved = await self._repository.replace(
                rotated,
                expected_state_version=existing.state_version,
            )
        except Exception:
            await self._secrets.delete(new_reference)
            raise
        await self._retire_secret(
            metadata=saved,
            reference=existing.secret_reference,
            occurred_at=occurred_at,
        )
        await self._audit_event(saved, "ROTATE", "SUCCESS", occurred_at)
        return saved

    async def revoke(
        self,
        *,
        credential_id: UUID,
        occurred_at: datetime,
    ) -> ProviderCredentialMetadata:
        _require_aware(occurred_at, "occurred_at")
        existing = await self._required_active(credential_id)
        revoked = replace(
            existing,
            status=ProviderCredentialStatus.REVOKED,
            state_version=existing.state_version + 1,
            revoked_at=occurred_at,
        )
        saved = await self._repository.replace(
            revoked,
            expected_state_version=existing.state_version,
        )
        await self._retire_secret(
            metadata=saved,
            reference=existing.secret_reference,
            occurred_at=occurred_at,
        )
        await self._audit_event(saved, "REVOKE", "SUCCESS", occurred_at)
        return saved

    async def preflight(
        self,
        *,
        credential_id: UUID,
        occurred_at: datetime,
    ) -> CredentialPreflightResult:
        _require_aware(occurred_at, "occurred_at")
        existing = await self._required_active(credential_id)
        if self._preflight is None:
            return CredentialPreflightResult(False, "PREFLIGHT_NOT_CONFIGURED")
        secret = await self._secrets.get(existing.secret_reference)
        result = await self._preflight.check(
            provider_id=existing.provider_id,
            secret=secret,
        )
        updated = replace(
            existing,
            last_successful_use_at=(
                occurred_at if result.ok else existing.last_successful_use_at
            ),
            last_failed_use_at=(
                existing.last_failed_use_at if result.ok else occurred_at
            ),
            state_version=existing.state_version + 1,
        )
        await self._repository.replace(updated, expected_state_version=existing.state_version)
        await self._audit_event(
            updated,
            "PREFLIGHT",
            "SUCCESS" if result.ok else "FAILED",
            occurred_at,
        )
        return result

    async def _required_active(
        self,
        credential_id: UUID,
    ) -> ProviderCredentialMetadata:
        metadata = await self._repository.get(credential_id)
        if metadata is None:
            raise LookupError("provider credential not found")
        if metadata.status is not ProviderCredentialStatus.ACTIVE:
            raise ValueError("provider credential is not active")
        return metadata

    async def _retire_secret(
        self,
        *,
        metadata: ProviderCredentialMetadata,
        reference: SecretReference,
        occurred_at: datetime,
    ) -> None:
        try:
            await self._secrets.delete(reference)
        except Exception:
            await self._audit_event(
                metadata,
                "SECRET_CLEANUP",
                "FAILED",
                occurred_at,
            )

    async def _audit_event(
        self,
        metadata: ProviderCredentialMetadata,
        action: str,
        result: str,
        occurred_at: datetime,
    ) -> None:
        await self._audit.record(
            CredentialAuditEvent(
                credential_id=metadata.credential_id,
                provider_id=metadata.provider_id,
                action=action,
                result=result,
                occurred_at=occurred_at,
            )
        )


def _fingerprint(secret: SecretValue) -> str:
    return hashlib.sha256(secret.reveal().encode("utf-8")).hexdigest()[:16]
