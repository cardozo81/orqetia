"""Atomic quota reservation/reconciliation contracts and deterministic fake."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7

from orqetia.control_plane.quotas import (
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicySnapshot,
)


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class QuotaReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    REJECTED = "REJECTED"
    RECONCILED = "RECONCILED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class QuotaWindow:
    key: str
    starts_at: datetime | None
    ends_at: datetime | None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("quota window key is required")
        if self.starts_at is not None:
            _require_aware(self.starts_at, "starts_at")
        if self.ends_at is not None:
            _require_aware(self.ends_at, "ends_at")
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("quota window end must be after start")


@dataclass(frozen=True)
class QuotaReservation:
    reservation_id: UUID
    policy_id: UUID
    policy_version: int
    tenant_id: UUID
    client_id: UUID
    idempotency_key: str
    metric: QuotaMetric
    amount: Decimal
    status: QuotaReservationStatus
    window: QuotaWindow
    created_at: datetime
    expires_at: datetime
    provider_id: str | None = None
    native_unit: str | None = None
    actual_amount: Decimal | None = None
    reconciled_at: datetime | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip() or len(self.idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1..200 characters")
        if self.amount < 0:
            raise ValueError("reservation amount cannot be negative")
        if self.actual_amount is not None and self.actual_amount < 0:
            raise ValueError("actual_amount cannot be negative")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if self.reconciled_at is not None:
            _require_aware(self.reconciled_at, "reconciled_at")


@dataclass(frozen=True)
class QuotaUtilization:
    policy_id: UUID
    policy_version: int
    metric: QuotaMetric
    consumed: Decimal
    reserved: Decimal
    limit: Decimal
    burst: Decimal
    window: QuotaWindow

    @property
    def total_committed(self) -> Decimal:
        return self.consumed + self.reserved

    @property
    def remaining(self) -> Decimal:
        return max(self.limit + self.burst - self.total_committed, Decimal("0"))

    @property
    def overage(self) -> bool:
        return self.total_committed > self.limit + self.burst


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reservation: QuotaReservation
    utilization: QuotaUtilization
    reason_code: str

    @property
    def overage(self) -> bool:
        return self.utilization.overage


@dataclass(frozen=True)
class QuotaReconciliation:
    reservation: QuotaReservation
    utilization: QuotaUtilization
    overage: bool


class QuotaEnforcer(Protocol):
    async def reserve(
        self,
        *,
        policy: QuotaPolicySnapshot,
        tenant_id: UUID,
        client_id: UUID,
        idempotency_key: str,
        amount: Decimal,
        occurred_at: datetime,
        provider_id: str | None = None,
        native_unit: str | None = None,
    ) -> QuotaDecision: ...

    async def reconcile(
        self,
        *,
        reservation_id: UUID,
        actual_amount: Decimal,
        occurred_at: datetime,
    ) -> QuotaReconciliation: ...

    async def release(
        self,
        *,
        reservation_id: UUID,
        occurred_at: datetime,
    ) -> QuotaReservation: ...


def quota_window(policy: QuotaPolicySnapshot, occurred_at: datetime) -> QuotaWindow:
    _require_aware(occurred_at, "occurred_at")
    if policy.metric is QuotaMetric.CONCURRENT_TASKS:
        return QuotaWindow(
            key=f"{policy.reference}:ACTIVE",
            starts_at=None,
            ends_at=None,
        )
    assert policy.period_seconds is not None
    epoch = int(occurred_at.timestamp())
    start_epoch = epoch - epoch % policy.period_seconds
    starts_at = datetime.fromtimestamp(start_epoch, tz=occurred_at.tzinfo)
    ends_at = starts_at + timedelta(seconds=policy.period_seconds)
    return QuotaWindow(
        key=f"{policy.reference}:{start_epoch}",
        starts_at=starts_at,
        ends_at=ends_at,
    )


class InMemoryQuotaEnforcer:
    """Reference implementation proving quota semantics with deterministic state."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._consumed: dict[str, Decimal] = {}
        self._reservations: dict[UUID, QuotaReservation] = {}
        self._idempotency: dict[tuple[UUID, UUID, UUID, int, str], UUID] = {}
        self._policies: dict[tuple[UUID, int], QuotaPolicySnapshot] = {}

    async def reserve(
        self,
        *,
        policy: QuotaPolicySnapshot,
        tenant_id: UUID,
        client_id: UUID,
        idempotency_key: str,
        amount: Decimal,
        occurred_at: datetime,
        provider_id: str | None = None,
        native_unit: str | None = None,
    ) -> QuotaDecision:
        if amount < 0:
            raise ValueError("quota reservation amount cannot be negative")
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1..200 characters")
        policy.validate_subject(
            tenant_id=tenant_id,
            client_id=client_id,
            provider_id=provider_id,
            native_unit=native_unit,
            occurred_at=occurred_at,
        )
        window = quota_window(policy, occurred_at)
        idem = (tenant_id, client_id, policy.policy_id, policy.version, idempotency_key)

        async with self._lock:
            self._policies[(policy.policy_id, policy.version)] = policy
            self._expire(window=window, occurred_at=occurred_at)
            existing_id = self._idempotency.get(idem)
            if existing_id is not None:
                existing = self._reservations[existing_id]
                self._validate_replay(
                    existing=existing,
                    amount=amount,
                    provider_id=provider_id,
                    native_unit=native_unit,
                )
                utilization = self._utilization(policy=policy, window=window)
                return QuotaDecision(
                    allowed=existing.status is not QuotaReservationStatus.REJECTED,
                    reservation=existing,
                    utilization=utilization,
                    reason_code="IDEMPOTENT_REPLAY",
                )

            current = self._utilization(policy=policy, window=window)
            projected = current.total_committed + amount
            overage = projected > policy.capacity
            allowed = not (
                policy.enforcement is QuotaEnforcementMode.HARD and overage
            )
            status = (
                QuotaReservationStatus.RESERVED
                if allowed
                else QuotaReservationStatus.REJECTED
            )
            reason = (
                "HARD_LIMIT_EXCEEDED"
                if not allowed
                else "SOFT_OVERAGE"
                if overage
                else "RESERVED"
            )
            expires_at = occurred_at + timedelta(seconds=policy.reservation_ttl_seconds)
            if window.ends_at is not None:
                expires_at = min(expires_at, window.ends_at)
                if expires_at <= occurred_at:
                    expires_at = occurred_at + timedelta(microseconds=1)

            reservation = QuotaReservation(
                reservation_id=uuid7(),
                policy_id=policy.policy_id,
                policy_version=policy.version,
                tenant_id=tenant_id,
                client_id=client_id,
                idempotency_key=idempotency_key,
                metric=policy.metric,
                amount=amount,
                status=status,
                window=window,
                created_at=occurred_at,
                expires_at=expires_at,
                provider_id=provider_id,
                native_unit=native_unit,
                reason_code=reason,
            )
            self._reservations[reservation.reservation_id] = reservation
            self._idempotency[idem] = reservation.reservation_id
            utilization = self._utilization(policy=policy, window=window)
            return QuotaDecision(
                allowed=allowed,
                reservation=reservation,
                utilization=utilization,
                reason_code=reason,
            )

    async def reconcile(
        self,
        *,
        reservation_id: UUID,
        actual_amount: Decimal,
        occurred_at: datetime,
    ) -> QuotaReconciliation:
        if actual_amount < 0:
            raise ValueError("actual_amount cannot be negative")
        _require_aware(occurred_at, "occurred_at")
        async with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise LookupError("quota reservation not found")
            if reservation.status is QuotaReservationStatus.RECONCILED:
                if reservation.actual_amount != actual_amount:
                    raise ValueError("reconciled actual amount is immutable")
                policy = self._policy_from_reservation(reservation)
                utilization = self._utilization(policy=policy, window=reservation.window)
                return QuotaReconciliation(reservation, utilization, utilization.overage)
            if reservation.status not in {
                QuotaReservationStatus.RESERVED,
                QuotaReservationStatus.EXPIRED,
            }:
                raise ValueError("quota reservation cannot be reconciled from current status")

            if reservation.metric is not QuotaMetric.CONCURRENT_TASKS:
                self._consumed[reservation.window.key] = (
                    self._consumed.get(reservation.window.key, Decimal("0"))
                    + actual_amount
                )

            reconciled = replace(
                reservation,
                status=QuotaReservationStatus.RECONCILED,
                actual_amount=actual_amount,
                reconciled_at=occurred_at,
                reason_code="RECONCILED",
            )
            self._reservations[reservation_id] = reconciled
            policy = self._policy_from_reservation(reconciled)
            utilization = self._utilization(policy=policy, window=reconciled.window)
            return QuotaReconciliation(reconciled, utilization, utilization.overage)

    async def release(
        self,
        *,
        reservation_id: UUID,
        occurred_at: datetime,
    ) -> QuotaReservation:
        _require_aware(occurred_at, "occurred_at")
        async with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise LookupError("quota reservation not found")
            if reservation.status is QuotaReservationStatus.RELEASED:
                return reservation
            if reservation.status not in {
                QuotaReservationStatus.RESERVED,
                QuotaReservationStatus.EXPIRED,
            }:
                raise ValueError("quota reservation cannot be released from current status")
            released = replace(
                reservation,
                status=QuotaReservationStatus.RELEASED,
                reconciled_at=occurred_at,
                reason_code="RELEASED",
            )
            self._reservations[reservation_id] = released
            return released

    async def list_by_idempotency_key(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        idempotency_key: str,
    ) -> tuple[QuotaReservation, ...]:
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1..200 characters")
        async with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._reservations.values()
                        if item.tenant_id == tenant_id
                        and item.client_id == client_id
                        and item.idempotency_key == idempotency_key
                    ),
                    key=lambda item: (
                        str(item.policy_id),
                        item.policy_version,
                        str(item.reservation_id),
                    ),
                )
            )

    async def utilization(
        self,
        *,
        policy: QuotaPolicySnapshot,
        tenant_id: UUID,
        client_id: UUID,
        occurred_at: datetime,
        provider_id: str | None = None,
        native_unit: str | None = None,
    ) -> QuotaUtilization:
        policy.validate_subject(
            tenant_id=tenant_id,
            client_id=client_id,
            provider_id=provider_id,
            native_unit=native_unit,
            occurred_at=occurred_at,
        )
        window = quota_window(policy, occurred_at)
        async with self._lock:
            self._expire(window=window, occurred_at=occurred_at)
            return self._utilization(policy=policy, window=window)

    def _expire(self, *, window: QuotaWindow, occurred_at: datetime) -> None:
        for reservation_id, reservation in tuple(self._reservations.items()):
            if (
                reservation.window.key == window.key
                and reservation.status is QuotaReservationStatus.RESERVED
                and reservation.expires_at <= occurred_at
            ):
                self._reservations[reservation_id] = replace(
                    reservation,
                    status=QuotaReservationStatus.EXPIRED,
                    reason_code="RESERVATION_EXPIRED",
                )

    def _utilization(
        self,
        *,
        policy: QuotaPolicySnapshot,
        window: QuotaWindow,
    ) -> QuotaUtilization:
        reserved = sum(
            (
                item.amount
                for item in self._reservations.values()
                if item.window.key == window.key
                and item.status is QuotaReservationStatus.RESERVED
            ),
            Decimal("0"),
        )
        return QuotaUtilization(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            metric=policy.metric,
            consumed=(
                Decimal("0")
                if policy.metric is QuotaMetric.CONCURRENT_TASKS
                else self._consumed.get(window.key, Decimal("0"))
            ),
            reserved=reserved,
            limit=policy.limit,
            burst=policy.burst,
            window=window,
        )

    @staticmethod
    def _validate_replay(
        *,
        existing: QuotaReservation,
        amount: Decimal,
        provider_id: str | None,
        native_unit: str | None,
    ) -> None:
        if (
            existing.amount != amount
            or existing.provider_id != provider_id
            or existing.native_unit != native_unit
        ):
            raise ValueError("idempotency key reused with different quota reservation")

    def _policy_from_reservation(self, reservation: QuotaReservation) -> QuotaPolicySnapshot:
        """Rebuild only immutable enforcement facts needed for utilization.

        The in-memory fake keeps a private snapshot map in production-like callers via
        reserve inputs. To keep the reservation self-contained for tests, capacity is
        recovered from the window's original policy through the shadow map below.
        """

        policy = self._policies.get((reservation.policy_id, reservation.policy_version))
        if policy is None:
            raise RuntimeError("quota policy snapshot unavailable for reconciliation")
        return policy
