"""Provider account, credential selection and external capacity control."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7

from .provider_credentials import (
    ProviderCredentialMetadata,
    ProviderCredentialRepository,
    ProviderCredentialStatus,
)


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ProviderAccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ExternalCapacitySource(StrEnum):
    PROVIDER_API = "PROVIDER_API"
    PROVIDER_CONSOLE = "PROVIDER_CONSOLE"


@dataclass(frozen=True)
class ProviderAccount:
    provider_account_id: UUID
    provider_id: str
    display_label: str
    status: ProviderAccountStatus
    priority: int
    created_at: datetime
    updated_at: datetime
    commercial_mode: str | None = None
    commercial_tier: str | None = None
    region: str | None = None
    contract_reference: str | None = None
    state_version: int = 1

    def __post_init__(self) -> None:
        for field, value, maximum in (
            ("provider_id", self.provider_id, 100),
            ("display_label", self.display_label, 200),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must contain 1..{maximum} characters")
        for field, value, maximum in (
            ("commercial_mode", self.commercial_mode, 100),
            ("commercial_tier", self.commercial_tier, 100),
            ("region", self.region, 100),
            ("contract_reference", self.contract_reference, 300),
        ):
            if value is not None and (not value.strip() or len(value) > maximum):
                raise ValueError(f"{field} must contain 1..{maximum} characters when present")
        if self.priority < 0:
            raise ValueError("priority cannot be negative")
        if self.state_version < 1:
            raise ValueError("state_version must be positive")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")

    def safe_admin_view(self) -> dict[str, object]:
        return {
            "provider_account_id": str(self.provider_account_id),
            "provider_id": self.provider_id,
            "display_label": self.display_label,
            "status": self.status.value,
            "priority": self.priority,
            "commercial_mode": self.commercial_mode,
            "commercial_tier": self.commercial_tier,
            "region": self.region,
            "contract_reference": self.contract_reference,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state_version": self.state_version,
        }


@dataclass(frozen=True)
class ExternalCapacitySnapshot:
    snapshot_id: UUID
    provider_account_id: UUID
    provider_id: str
    native_unit: str
    source: ExternalCapacitySource
    observed_at: datetime
    source_reference: str
    remaining: Decimal | None = None
    limit: Decimal | None = None
    reset_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or len(self.provider_id) > 100:
            raise ValueError("provider_id must contain 1..100 characters")
        if not self.native_unit.strip() or len(self.native_unit) > 100:
            raise ValueError("native_unit must contain 1..100 characters")
        if not self.source_reference.strip() or len(self.source_reference) > 500:
            raise ValueError("source_reference must contain 1..500 characters")
        if self.remaining is None and self.limit is None:
            raise ValueError("capacity snapshot requires provider-observed remaining or limit")
        for field, value in (("remaining", self.remaining), ("limit", self.limit)):
            if value is not None and value < 0:
                raise ValueError(f"{field} cannot be negative")
        if (
            self.remaining is not None
            and self.limit is not None
            and self.remaining > self.limit
        ):
            raise ValueError("remaining cannot exceed provider-reported limit")
        _require_aware(self.observed_at, "observed_at")
        if self.reset_at is not None:
            _require_aware(self.reset_at, "reset_at")


@dataclass(frozen=True)
class ProviderCredentialSelection:
    provider_account_id: UUID
    provider_credential_id: UUID
    provider_id: str
    key_version: int
    selected_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.selected_at, "selected_at")


class ProviderAccountRepository(Protocol):
    async def create(self, account: ProviderAccount) -> ProviderAccount: ...

    async def get(self, provider_account_id: UUID) -> ProviderAccount | None: ...

    async def replace(
        self,
        account: ProviderAccount,
        *,
        expected_state_version: int,
    ) -> ProviderAccount: ...

    async def list_for_provider(self, provider_id: str) -> tuple[ProviderAccount, ...]: ...


class ExternalCapacityRepository(Protocol):
    async def append(
        self,
        snapshot: ExternalCapacitySnapshot,
    ) -> ExternalCapacitySnapshot: ...

    async def latest(
        self,
        *,
        provider_account_id: UUID,
        native_unit: str,
    ) -> ExternalCapacitySnapshot | None: ...


class InMemoryProviderAccountRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, ProviderAccount] = {}

    async def create(self, account: ProviderAccount) -> ProviderAccount:
        if account.provider_account_id in self._items:
            raise ValueError("provider account already exists")
        self._items[account.provider_account_id] = account
        return account

    async def get(self, provider_account_id: UUID) -> ProviderAccount | None:
        return self._items.get(provider_account_id)

    async def replace(
        self,
        account: ProviderAccount,
        *,
        expected_state_version: int,
    ) -> ProviderAccount:
        existing = self._items.get(account.provider_account_id)
        if existing is None:
            raise LookupError("provider account not found")
        if existing.state_version != expected_state_version:
            raise ValueError("provider account version conflict")
        self._items[account.provider_account_id] = account
        return account

    async def list_for_provider(self, provider_id: str) -> tuple[ProviderAccount, ...]:
        return tuple(
            sorted(
                (item for item in self._items.values() if item.provider_id == provider_id),
                key=lambda item: (item.priority, item.created_at, str(item.provider_account_id)),
            )
        )


class InMemoryExternalCapacityRepository:
    def __init__(self) -> None:
        self._items: list[ExternalCapacitySnapshot] = []

    async def append(
        self,
        snapshot: ExternalCapacitySnapshot,
    ) -> ExternalCapacitySnapshot:
        self._items.append(snapshot)
        return snapshot

    async def latest(
        self,
        *,
        provider_account_id: UUID,
        native_unit: str,
    ) -> ExternalCapacitySnapshot | None:
        matches = [
            item
            for item in self._items
            if item.provider_account_id == provider_account_id
            and item.native_unit == native_unit
        ]
        return max(matches, key=lambda item: item.observed_at) if matches else None


class ProviderAccountService:
    def __init__(
        self,
        *,
        accounts: ProviderAccountRepository,
        credentials: ProviderCredentialRepository,
        capacity: ExternalCapacityRepository,
    ) -> None:
        self._accounts = accounts
        self._credentials = credentials
        self._capacity = capacity

    async def create_account(
        self,
        *,
        provider_id: str,
        display_label: str,
        occurred_at: datetime,
        priority: int = 100,
        commercial_mode: str | None = None,
        commercial_tier: str | None = None,
        region: str | None = None,
        contract_reference: str | None = None,
    ) -> ProviderAccount:
        account = ProviderAccount(
            provider_account_id=uuid7(),
            provider_id=provider_id,
            display_label=display_label,
            status=ProviderAccountStatus.ACTIVE,
            priority=priority,
            commercial_mode=commercial_mode,
            commercial_tier=commercial_tier,
            region=region,
            contract_reference=contract_reference,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        return await self._accounts.create(account)

    async def set_status(
        self,
        *,
        provider_account_id: UUID,
        status: ProviderAccountStatus,
        occurred_at: datetime,
    ) -> ProviderAccount:
        existing = await self._accounts.get(provider_account_id)
        if existing is None:
            raise LookupError("provider account not found")
        updated = replace(
            existing,
            status=status,
            updated_at=occurred_at,
            state_version=existing.state_version + 1,
        )
        return await self._accounts.replace(
            updated,
            expected_state_version=existing.state_version,
        )

    async def record_external_capacity(
        self,
        *,
        provider_account_id: UUID,
        native_unit: str,
        source: ExternalCapacitySource,
        source_reference: str,
        observed_at: datetime,
        remaining: Decimal | None = None,
        limit: Decimal | None = None,
        reset_at: datetime | None = None,
    ) -> ExternalCapacitySnapshot:
        account = await self._accounts.get(provider_account_id)
        if account is None:
            raise LookupError("provider account not found")
        snapshot = ExternalCapacitySnapshot(
            snapshot_id=uuid7(),
            provider_account_id=provider_account_id,
            provider_id=account.provider_id,
            native_unit=native_unit,
            source=source,
            source_reference=source_reference,
            observed_at=observed_at,
            remaining=remaining,
            limit=limit,
            reset_at=reset_at,
        )
        return await self._capacity.append(snapshot)

    async def select_credential(
        self,
        *,
        provider_id: str,
        occurred_at: datetime,
        region: str | None = None,
    ) -> ProviderCredentialSelection:
        _require_aware(occurred_at, "occurred_at")
        accounts = await self._accounts.list_for_provider(provider_id)
        for account in accounts:
            if account.status is not ProviderAccountStatus.ACTIVE:
                continue
            if region is not None and account.region not in {None, region}:
                continue
            candidates = await self._credentials.list_active(
                provider_account_id=account.provider_account_id,
            )
            eligible = tuple(
                credential
                for credential in candidates
                if _credential_is_eligible(credential, occurred_at)
            )
            if not eligible:
                continue
            selected = min(eligible, key=_credential_rank)
            return ProviderCredentialSelection(
                provider_account_id=account.provider_account_id,
                provider_credential_id=selected.credential_id,
                provider_id=provider_id,
                key_version=selected.key_version,
                selected_at=occurred_at,
            )
        raise LookupError("no active provider credential is available")


def _credential_is_eligible(
    credential: ProviderCredentialMetadata,
    occurred_at: datetime,
) -> bool:
    return (
        credential.status is ProviderCredentialStatus.ACTIVE
        and (credential.expires_at is None or credential.expires_at > occurred_at)
    )


def _credential_rank(
    credential: ProviderCredentialMetadata,
) -> tuple[int, datetime, str]:
    unhealthy = int(
        credential.last_failed_use_at is not None
        and (
            credential.last_successful_use_at is None
            or credential.last_failed_use_at > credential.last_successful_use_at
        )
    )
    health_time = (
        credential.last_successful_use_at
        or credential.last_failed_use_at
        or credential.created_at
    )
    return (unhealthy, health_time, str(credential.credential_id))
