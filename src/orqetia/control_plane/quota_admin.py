"""Administrative versioning for client quota policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid7

from .quotas import (
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicySnapshot,
    QuotaScope,
)


class QuotaOwnerResolver(Protocol):
    async def resolve_active_owner(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> object: ...


class QuotaPolicyRepository(Protocol):
    async def list_series(
        self,
        *,
        policy_id: UUID,
    ) -> tuple[QuotaPolicySnapshot, ...]: ...

    async def list_for_subject(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID | None,
    ) -> tuple[QuotaPolicySnapshot, ...]: ...

    async def append(self, policy: QuotaPolicySnapshot) -> QuotaPolicySnapshot: ...


@dataclass(frozen=True)
class QuotaPolicyKey:
    scope: QuotaScope
    tenant_id: UUID
    client_id: UUID | None
    metric: QuotaMetric
    provider_id: str | None = None
    native_unit: str | None = None


class InMemoryQuotaPolicyRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[UUID, int], QuotaPolicySnapshot] = {}

    async def list_series(
        self,
        *,
        policy_id: UUID,
    ) -> tuple[QuotaPolicySnapshot, ...]:
        return tuple(
            sorted(
                (
                    item
                    for (stored_id, _version), item in self._items.items()
                    if stored_id == policy_id
                ),
                key=lambda item: item.version,
            )
        )

    async def list_for_subject(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID | None,
    ) -> tuple[QuotaPolicySnapshot, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._items.values()
                    if item.tenant_id == tenant_id and item.client_id == client_id
                ),
                key=lambda item: (item.effective_from, item.version, str(item.policy_id)),
            )
        )

    async def append(self, policy: QuotaPolicySnapshot) -> QuotaPolicySnapshot:
        key = (policy.policy_id, policy.version)
        if key in self._items:
            raise ValueError("quota policy version already exists")
        series = await self.list_series(policy_id=policy.policy_id)
        if series and policy.version != series[-1].version + 1:
            raise ValueError("quota policy version must increment by one")
        if not series and policy.version != 1:
            raise ValueError("initial quota policy version must be 1")
        self._items[key] = policy
        return policy


class QuotaPolicyAdminService:
    def __init__(
        self,
        *,
        repository: QuotaPolicyRepository,
        owner_resolver: QuotaOwnerResolver,
    ) -> None:
        self._repository = repository
        self._owner_resolver = owner_resolver

    async def publish(
        self,
        *,
        scope: QuotaScope,
        tenant_id: UUID,
        client_id: UUID | None,
        metric: QuotaMetric,
        limit: Decimal,
        enforcement: QuotaEnforcementMode,
        effective_from: datetime,
        period_seconds: int | None = None,
        burst: Decimal = Decimal("0"),
        reservation_ttl_seconds: int = 300,
        native_unit: str | None = None,
        provider_id: str | None = None,
        effective_to: datetime | None = None,
        policy_id: UUID | None = None,
    ) -> QuotaPolicySnapshot:
        await self._validate_owner(
            scope=scope,
            tenant_id=tenant_id,
            client_id=client_id,
        )
        resolved_policy_id = policy_id or uuid7()
        versions = await self._repository.list_series(policy_id=resolved_policy_id)
        version = 1 if not versions else versions[-1].version + 1
        snapshot = QuotaPolicySnapshot(
            policy_id=resolved_policy_id,
            version=version,
            scope=scope,
            tenant_id=tenant_id,
            client_id=client_id,
            metric=metric,
            limit=limit,
            enforcement=enforcement,
            effective_from=effective_from,
            effective_to=effective_to,
            period_seconds=period_seconds,
            burst=burst,
            reservation_ttl_seconds=reservation_ttl_seconds,
            native_unit=native_unit,
            provider_id=provider_id,
        )
        if versions:
            previous = versions[-1]
            if _semantic_key(previous) != _semantic_key(snapshot):
                raise ValueError("quota policy series semantic key is immutable")
            if effective_from <= previous.effective_from:
                raise ValueError("new quota version must become effective after prior version")
        return await self._repository.append(snapshot)

    async def resolve_effective(
        self,
        *,
        key: QuotaPolicyKey,
        occurred_at: datetime,
    ) -> QuotaPolicySnapshot:
        await self._validate_owner(
            scope=key.scope,
            tenant_id=key.tenant_id,
            client_id=key.client_id,
        )
        candidates = await self._repository.list_for_subject(
            tenant_id=key.tenant_id,
            client_id=key.client_id,
        )
        matches = tuple(
            item
            for item in candidates
            if _semantic_key(item)
            == (
                key.scope,
                key.tenant_id,
                key.client_id,
                key.metric,
                key.provider_id,
                key.native_unit,
            )
            and item.active_at(occurred_at)
        )
        if not matches:
            raise LookupError("no effective quota policy")
        return max(matches, key=lambda item: (item.effective_from, item.version))

    async def list_for_subject(
        self,
        *,
        scope: QuotaScope,
        tenant_id: UUID,
        client_id: UUID | None,
    ) -> tuple[QuotaPolicySnapshot, ...]:
        await self._validate_owner(
            scope=scope,
            tenant_id=tenant_id,
            client_id=client_id,
        )
        return await self._repository.list_for_subject(
            tenant_id=tenant_id,
            client_id=client_id,
        )

    async def _validate_owner(
        self,
        *,
        scope: QuotaScope,
        tenant_id: UUID,
        client_id: UUID | None,
    ) -> None:
        if scope is QuotaScope.CLIENT:
            if client_id is None:
                raise ValueError("CLIENT quota requires client_id")
            await self._owner_resolver.resolve_active_owner(
                tenant_id=tenant_id,
                client_id=client_id,
            )
            return
        if client_id is not None:
            raise ValueError("TENANT quota must not define client_id")
        # Tenant-level policy still needs an active tenant. Resolve through any client
        # is not semantically valid, so the owner resolver may expose tenant validation.
        validate_tenant = getattr(self._owner_resolver, "require_active_tenant", None)
        if validate_tenant is None:
            raise RuntimeError("tenant quota owner resolver lacks require_active_tenant")
        await validate_tenant(tenant_id=tenant_id)


def _semantic_key(
    policy: QuotaPolicySnapshot,
) -> tuple[object, ...]:
    return (
        policy.scope,
        policy.tenant_id,
        policy.client_id,
        policy.metric,
        policy.provider_id,
        policy.native_unit,
    )
