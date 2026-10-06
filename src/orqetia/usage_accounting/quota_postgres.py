"""PostgreSQL quota enforcement with atomic reservation and idempotent reconciliation."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid7

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.control_plane.quotas import (
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicySnapshot,
)

from .quota_tables import quota_reservations, quota_windows
from .quotas import (
    QuotaDecision,
    QuotaReconciliation,
    QuotaReservation,
    QuotaReservationStatus,
    QuotaUtilization,
    QuotaWindow,
    quota_window,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")


def _window_from_row(row: RowMapping) -> QuotaWindow:
    return QuotaWindow(
        key=cast(str, row["window_key"]),
        starts_at=cast(datetime | None, row["starts_at"]),
        ends_at=cast(datetime | None, row["ends_at"]),
    )


def _reservation_from_row(row: RowMapping) -> QuotaReservation:
    return QuotaReservation(
        reservation_id=cast(UUID, row["reservation_id"]),
        policy_id=cast(UUID, row["policy_id"]),
        policy_version=cast(int, row["policy_version"]),
        tenant_id=cast(UUID, row["tenant_id"]),
        client_id=cast(UUID, row["client_id"]),
        idempotency_key=cast(str, row["idempotency_key"]),
        metric=QuotaMetric(cast(str, row["metric"])),
        amount=Decimal(row["reserved_amount"]),
        status=QuotaReservationStatus(cast(str, row["status"])),
        window=QuotaWindow(
            key=cast(str, row["window_key"]),
            starts_at=None,
            ends_at=None,
        ),
        created_at=cast(datetime, row["created_at"]),
        expires_at=cast(datetime, row["expires_at"]),
        provider_id=cast(str | None, row["provider_id"]),
        native_unit=cast(str | None, row["native_unit"]),
        actual_amount=(
            None
            if row["actual_amount"] is None
            else Decimal(row["actual_amount"])
        ),
        reconciled_at=cast(datetime | None, row["reconciled_at"]),
        reason_code=cast(str, row["reason_code"]),
    )


def _scope_key(
    *,
    tenant_id: UUID,
    client_id: UUID,
    policy: QuotaPolicySnapshot,
    idempotency_key: str,
) -> str:
    return (
        f"{tenant_id}:{client_id}:{policy.policy_id}:"
        f"{policy.version}:{idempotency_key}"
    )


class PostgresQuotaEnforcer:
    """Durable implementation of the canonical quota reservation contract."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

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
        idem_scope = _scope_key(
            tenant_id=tenant_id,
            client_id=client_id,
            policy=policy,
            idempotency_key=idempotency_key,
        )

        async with self._sessions.begin() as database:
            window_row = await self._lock_window(
                database=database,
                policy=policy,
                window=window,
                occurred_at=occurred_at,
            )
            await self._expire(
                database=database,
                window_key=window.key,
                occurred_at=occurred_at,
            )
            existing = (
                await database.execute(
                    sa.select(quota_reservations).where(
                        quota_reservations.c.idempotency_scope_key
                        == idem_scope
                    )
                )
            ).mappings().one_or_none()
            if existing is not None:
                reservation = await self._hydrate_reservation(
                    database=database,
                    row=existing,
                )
                self._validate_replay(
                    existing=reservation,
                    amount=amount,
                    provider_id=provider_id,
                    native_unit=native_unit,
                )
                utilization = await self._utilization(
                    database=database,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    metric=policy.metric,
                    limit=policy.limit,
                    burst=policy.burst,
                    window=window,
                    consumed=Decimal(window_row["consumed_amount"]),
                )
                return QuotaDecision(
                    allowed=(
                        reservation.status
                        is not QuotaReservationStatus.REJECTED
                    ),
                    reservation=reservation,
                    utilization=utilization,
                    reason_code="IDEMPOTENT_REPLAY",
                )

            before = await self._utilization(
                database=database,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                metric=policy.metric,
                limit=policy.limit,
                burst=policy.burst,
                window=window,
                consumed=Decimal(window_row["consumed_amount"]),
            )
            projected = before.total_committed + amount
            overage = projected > policy.capacity
            allowed = not (
                policy.enforcement is QuotaEnforcementMode.HARD
                and overage
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
            expires_at = occurred_at + timedelta(
                seconds=policy.reservation_ttl_seconds
            )
            if window.ends_at is not None:
                expires_at = min(expires_at, window.ends_at)
                if expires_at <= occurred_at:
                    expires_at = occurred_at + timedelta(microseconds=1)

            reservation_id = uuid7()
            values = {
                "reservation_id": reservation_id,
                "window_key": window.key,
                "idempotency_scope_key": idem_scope,
                "idempotency_key": idempotency_key,
                "policy_id": policy.policy_id,
                "policy_version": policy.version,
                "tenant_id": tenant_id,
                "client_id": client_id,
                "metric": policy.metric.value,
                "provider_id": provider_id,
                "native_unit": native_unit,
                "reserved_amount": amount,
                "actual_amount": None,
                "status": status.value,
                "reason_code": reason,
                "enforcement": policy.enforcement.value,
                "limit_snapshot": policy.limit,
                "burst_snapshot": policy.burst,
                "created_at": occurred_at,
                "expires_at": expires_at,
                "reconciled_at": None,
            }
            row = (
                await database.execute(
                    sa.insert(quota_reservations)
                    .values(**values)
                    .returning(*quota_reservations.c)
                )
            ).mappings().one()
            reservation = await self._hydrate_reservation(
                database=database,
                row=row,
            )
            utilization = await self._utilization(
                database=database,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                metric=policy.metric,
                limit=policy.limit,
                burst=policy.burst,
                window=window,
                consumed=Decimal(window_row["consumed_amount"]),
            )
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
        _aware(occurred_at)
        async with self._sessions.begin() as database:
            current = (
                await database.execute(
                    sa.select(quota_reservations).where(
                        quota_reservations.c.reservation_id == reservation_id
                    )
                )
            ).mappings().one_or_none()
            if current is None:
                raise LookupError("quota reservation not found")
            window_key = cast(str, current["window_key"])
            await database.execute(
                sa.select(quota_windows)
                .where(quota_windows.c.window_key == window_key)
                .with_for_update()
            )
            row = (
                await database.execute(
                    sa.select(quota_reservations)
                    .where(
                        quota_reservations.c.reservation_id
                        == reservation_id
                    )
                    .with_for_update()
                )
            ).mappings().one()
            status = QuotaReservationStatus(cast(str, row["status"]))
            if status is QuotaReservationStatus.RECONCILED:
                if Decimal(row["actual_amount"]) != actual_amount:
                    raise ValueError(
                        "reconciled actual amount is immutable"
                    )
            elif status in {
                QuotaReservationStatus.RESERVED,
                QuotaReservationStatus.EXPIRED,
            }:
                metric = QuotaMetric(cast(str, row["metric"]))
                if metric is not QuotaMetric.CONCURRENT_TASKS:
                    await database.execute(
                        sa.update(quota_windows)
                        .where(quota_windows.c.window_key == window_key)
                        .values(
                            consumed_amount=(
                                quota_windows.c.consumed_amount
                                + actual_amount
                            ),
                            updated_at=occurred_at,
                        )
                    )
                row = (
                    await database.execute(
                        sa.update(quota_reservations)
                        .where(
                            quota_reservations.c.reservation_id
                            == reservation_id
                        )
                        .values(
                            status=QuotaReservationStatus.RECONCILED.value,
                            actual_amount=actual_amount,
                            reconciled_at=occurred_at,
                            reason_code="RECONCILED",
                        )
                        .returning(*quota_reservations.c)
                    )
                ).mappings().one()
            else:
                raise ValueError(
                    "quota reservation cannot be reconciled from current status"
                )

            refreshed_window = (
                await database.execute(
                    sa.select(quota_windows).where(
                        quota_windows.c.window_key == window_key
                    )
                )
            ).mappings().one()
            reservation = await self._hydrate_reservation(
                database=database,
                row=row,
            )
            utilization = await self._utilization(
                database=database,
                policy_id=cast(UUID, row["policy_id"]),
                policy_version=cast(int, row["policy_version"]),
                metric=QuotaMetric(cast(str, row["metric"])),
                limit=Decimal(row["limit_snapshot"]),
                burst=Decimal(row["burst_snapshot"]),
                window=_window_from_row(refreshed_window),
                consumed=Decimal(refreshed_window["consumed_amount"]),
            )
            return QuotaReconciliation(
                reservation=reservation,
                utilization=utilization,
                overage=utilization.overage,
            )

    async def release(
        self,
        *,
        reservation_id: UUID,
        occurred_at: datetime,
    ) -> QuotaReservation:
        _aware(occurred_at)
        async with self._sessions.begin() as database:
            current = (
                await database.execute(
                    sa.select(quota_reservations).where(
                        quota_reservations.c.reservation_id == reservation_id
                    )
                )
            ).mappings().one_or_none()
            if current is None:
                raise LookupError("quota reservation not found")
            window_key = cast(str, current["window_key"])
            await database.execute(
                sa.select(quota_windows)
                .where(quota_windows.c.window_key == window_key)
                .with_for_update()
            )
            row = (
                await database.execute(
                    sa.select(quota_reservations)
                    .where(
                        quota_reservations.c.reservation_id
                        == reservation_id
                    )
                    .with_for_update()
                )
            ).mappings().one()
            status = QuotaReservationStatus(cast(str, row["status"]))
            if status is QuotaReservationStatus.RELEASED:
                return await self._hydrate_reservation(
                    database=database,
                    row=row,
                )
            if status not in {
                QuotaReservationStatus.RESERVED,
                QuotaReservationStatus.EXPIRED,
            }:
                raise ValueError(
                    "quota reservation cannot be released from current status"
                )
            released = (
                await database.execute(
                    sa.update(quota_reservations)
                    .where(
                        quota_reservations.c.reservation_id == reservation_id
                    )
                    .values(
                        status=QuotaReservationStatus.RELEASED.value,
                        reconciled_at=occurred_at,
                        reason_code="RELEASED",
                    )
                    .returning(*quota_reservations.c)
                )
            ).mappings().one()
            return await self._hydrate_reservation(
                database=database,
                row=released,
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
        async with self._sessions.begin() as database:
            window_row = await self._lock_window(
                database=database,
                policy=policy,
                window=window,
                occurred_at=occurred_at,
            )
            await self._expire(
                database=database,
                window_key=window.key,
                occurred_at=occurred_at,
            )
            return await self._utilization(
                database=database,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                metric=policy.metric,
                limit=policy.limit,
                burst=policy.burst,
                window=window,
                consumed=Decimal(window_row["consumed_amount"]),
            )

    async def _lock_window(
        self,
        *,
        database: AsyncSession,
        policy: QuotaPolicySnapshot,
        window: QuotaWindow,
        occurred_at: datetime,
    ) -> RowMapping:
        await database.execute(
            insert(quota_windows)
            .values(
                window_key=window.key,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                tenant_id=policy.tenant_id,
                client_id=policy.client_id,
                metric=policy.metric.value,
                provider_id=policy.provider_id,
                native_unit=policy.native_unit,
                starts_at=window.starts_at,
                ends_at=window.ends_at,
                consumed_amount=Decimal("0"),
                updated_at=occurred_at,
            )
            .on_conflict_do_nothing(
                index_elements=[quota_windows.c.window_key]
            )
        )
        row = (
            await database.execute(
                sa.select(quota_windows)
                .where(quota_windows.c.window_key == window.key)
                .with_for_update()
            )
        ).mappings().one()
        expected = (
            policy.policy_id,
            policy.version,
            policy.tenant_id,
            policy.client_id,
            policy.metric.value,
            policy.provider_id,
            policy.native_unit,
        )
        actual = (
            row["policy_id"],
            row["policy_version"],
            row["tenant_id"],
            row["client_id"],
            row["metric"],
            row["provider_id"],
            row["native_unit"],
        )
        if actual != expected:
            raise ValueError("quota window identity conflict")
        return row

    @staticmethod
    async def _expire(
        *,
        database: AsyncSession,
        window_key: str,
        occurred_at: datetime,
    ) -> None:
        await database.execute(
            sa.update(quota_reservations)
            .where(
                quota_reservations.c.window_key == window_key,
                quota_reservations.c.status
                == QuotaReservationStatus.RESERVED.value,
                quota_reservations.c.expires_at <= occurred_at,
            )
            .values(
                status=QuotaReservationStatus.EXPIRED.value,
                reason_code="RESERVATION_EXPIRED",
            )
        )

    @staticmethod
    async def _utilization(
        *,
        database: AsyncSession,
        policy_id: UUID,
        policy_version: int,
        metric: QuotaMetric,
        limit: Decimal,
        burst: Decimal,
        window: QuotaWindow,
        consumed: Decimal,
    ) -> QuotaUtilization:
        reserved = (
            await database.execute(
                sa.select(
                    sa.func.coalesce(
                        sa.func.sum(
                            quota_reservations.c.reserved_amount
                        ),
                        0,
                    )
                ).where(
                    quota_reservations.c.window_key == window.key,
                    quota_reservations.c.status
                    == QuotaReservationStatus.RESERVED.value,
                )
            )
        ).scalar_one()
        return QuotaUtilization(
            policy_id=policy_id,
            policy_version=policy_version,
            metric=metric,
            consumed=(
                Decimal("0")
                if metric is QuotaMetric.CONCURRENT_TASKS
                else Decimal(consumed)
            ),
            reserved=Decimal(reserved),
            limit=limit,
            burst=burst,
            window=window,
        )

    @staticmethod
    async def _hydrate_reservation(
        *,
        database: AsyncSession,
        row: RowMapping,
    ) -> QuotaReservation:
        window = (
            await database.execute(
                sa.select(quota_windows).where(
                    quota_windows.c.window_key == row["window_key"]
                )
            )
        ).mappings().one()
        reservation = _reservation_from_row(row)
        return QuotaReservation(
            reservation_id=reservation.reservation_id,
            policy_id=reservation.policy_id,
            policy_version=reservation.policy_version,
            tenant_id=reservation.tenant_id,
            client_id=reservation.client_id,
            idempotency_key=reservation.idempotency_key,
            metric=reservation.metric,
            amount=reservation.amount,
            status=reservation.status,
            window=_window_from_row(window),
            created_at=reservation.created_at,
            expires_at=reservation.expires_at,
            provider_id=reservation.provider_id,
            native_unit=reservation.native_unit,
            actual_amount=reservation.actual_amount,
            reconciled_at=reservation.reconciled_at,
            reason_code=reservation.reason_code,
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
            raise ValueError(
                "idempotency key reused with different quota reservation"
            )
