from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

import pytest
import sqlalchemy as sa

from orqetia.control_plane.quotas import (
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicySnapshot,
    QuotaScope,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings
from orqetia.usage_accounting.quota_postgres import PostgresQuotaEnforcer
from orqetia.usage_accounting.quotas import (
    InMemoryQuotaEnforcer,
    QuotaReservationStatus,
)

NOW = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)


def _policy(
    *,
    metric: QuotaMetric = QuotaMetric.REQUESTS,
    limit: str = "10",
    enforcement: QuotaEnforcementMode = QuotaEnforcementMode.HARD,
    scope: QuotaScope = QuotaScope.CLIENT,
    tenant_id=None,
    client_id=None,
    period_seconds: int | None = 60,
    burst: str = "0",
    native_unit: str | None = None,
    provider_id: str | None = None,
) -> QuotaPolicySnapshot:
    tenant = tenant_id or uuid7()
    client = client_id or uuid7()
    return QuotaPolicySnapshot(
        policy_id=uuid7(),
        version=1,
        scope=scope,
        tenant_id=tenant,
        client_id=client if scope is QuotaScope.CLIENT else None,
        metric=metric,
        limit=Decimal(limit),
        enforcement=enforcement,
        effective_from=NOW - timedelta(days=1),
        period_seconds=None if metric is QuotaMetric.CONCURRENT_TASKS else period_seconds,
        burst=Decimal(burst),
        native_unit=native_unit,
        provider_id=provider_id,
    )


@pytest.mark.asyncio
async def test_hard_limit_reserves_atomically_and_rejects_over_limit() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    policy = _policy(tenant_id=tenant_id, client_id=client_id, limit="2")
    quotas = InMemoryQuotaEnforcer()

    first = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="request-1",
        amount=Decimal("1"),
        occurred_at=NOW,
    )
    second = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="request-2",
        amount=Decimal("1"),
        occurred_at=NOW,
    )
    rejected = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="request-3",
        amount=Decimal("1"),
        occurred_at=NOW,
    )

    assert first.allowed and second.allowed
    assert not rejected.allowed
    assert rejected.reason_code == "HARD_LIMIT_EXCEEDED"
    assert rejected.reservation.status is QuotaReservationStatus.REJECTED


@pytest.mark.asyncio
async def test_reservation_and_reconcile_are_idempotent() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    policy = _policy(tenant_id=tenant_id, client_id=client_id, limit="100")
    quotas = InMemoryQuotaEnforcer()

    first = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="task-17",
        amount=Decimal("20"),
        occurred_at=NOW,
    )
    replay = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="task-17",
        amount=Decimal("20"),
        occurred_at=NOW,
    )
    assert replay.reservation.reservation_id == first.reservation.reservation_id

    reconciled = await quotas.reconcile(
        reservation_id=first.reservation.reservation_id,
        actual_amount=Decimal("12"),
        occurred_at=NOW + timedelta(seconds=1),
    )
    replay_reconcile = await quotas.reconcile(
        reservation_id=first.reservation.reservation_id,
        actual_amount=Decimal("12"),
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert reconciled.reservation.status is QuotaReservationStatus.RECONCILED
    assert replay_reconcile.reservation.actual_amount == Decimal("12")
    assert replay_reconcile.utilization.consumed == Decimal("12")


@pytest.mark.asyncio
async def test_soft_limit_allows_and_reports_overage_with_burst() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    policy = _policy(
        tenant_id=tenant_id,
        client_id=client_id,
        limit="10",
        burst="2",
        enforcement=QuotaEnforcementMode.SOFT,
    )
    quotas = InMemoryQuotaEnforcer()
    decision = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="burst",
        amount=Decimal("13"),
        occurred_at=NOW,
    )
    assert decision.allowed
    assert decision.overage
    assert decision.reason_code == "SOFT_OVERAGE"


@pytest.mark.asyncio
async def test_concurrency_reservation_releases_capacity() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    policy = _policy(
        metric=QuotaMetric.CONCURRENT_TASKS,
        tenant_id=tenant_id,
        client_id=client_id,
        limit="1",
        period_seconds=None,
    )
    quotas = InMemoryQuotaEnforcer()
    active = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="task-a",
        amount=Decimal("1"),
        occurred_at=NOW,
    )
    blocked = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="task-b",
        amount=Decimal("1"),
        occurred_at=NOW,
    )
    assert active.allowed
    assert not blocked.allowed

    await quotas.release(
        reservation_id=active.reservation.reservation_id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    next_task = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="task-c",
        amount=Decimal("1"),
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert next_task.allowed


@pytest.mark.asyncio
async def test_fixed_window_resets_without_erasing_historical_fact() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    policy = _policy(tenant_id=tenant_id, client_id=client_id, limit="1")
    quotas = InMemoryQuotaEnforcer()

    first = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="w1",
        amount=Decimal("1"),
        occurred_at=NOW,
    )
    assert first.allowed
    next_window = await quotas.reserve(
        policy=policy,
        tenant_id=tenant_id,
        client_id=client_id,
        idempotency_key="w2",
        amount=Decimal("1"),
        occurred_at=NOW + timedelta(seconds=61),
    )
    assert next_window.allowed
    assert next_window.reservation.window.key != first.reservation.window.key


@pytest.mark.asyncio
async def test_tenant_and_provider_specific_boundaries_fail_closed() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    policy = _policy(
        metric=QuotaMetric.PROVIDER_REQUESTS,
        tenant_id=tenant_id,
        client_id=client_id,
        provider_id="openai",
    )
    quotas = InMemoryQuotaEnforcer()

    with pytest.raises(PermissionError):
        await quotas.reserve(
            policy=policy,
            tenant_id=uuid7(),
            client_id=client_id,
            provider_id="openai",
            idempotency_key="wrong-tenant",
            amount=Decimal("1"),
            occurred_at=NOW,
        )
    with pytest.raises(ValueError, match="provider-specific"):
        await quotas.reserve(
            policy=policy,
            tenant_id=tenant_id,
            client_id=client_id,
            provider_id="anthropic",
            idempotency_key="wrong-provider",
            amount=Decimal("1"),
            occurred_at=NOW,
        )


def test_native_quota_and_scope_contracts() -> None:
    with pytest.raises(ValueError, match="native_unit"):
        _policy(metric=QuotaMetric.NATIVE_UNITS)
    with pytest.raises(ValueError, match="client_id"):
        QuotaPolicySnapshot(
            policy_id=uuid7(),
            version=1,
            scope=QuotaScope.CLIENT,
            tenant_id=uuid7(),
            client_id=None,
            metric=QuotaMetric.REQUESTS,
            limit=Decimal("1"),
            enforcement=QuotaEnforcementMode.HARD,
            effective_from=NOW,
            period_seconds=60,
        )


@pytest.mark.asyncio
@pytest.mark.skipif(
    "ORQETIA_DATABASE_DSN" not in os.environ,
    reason="PostgreSQL integration DSN is not configured",
)
async def test_postgres_quota_enforcer_is_atomic_and_replay_safe() -> None:
    settings = RuntimeSettings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    tenant_id, client_id = uuid7(), uuid7()
    policy = _policy(
        tenant_id=tenant_id,
        client_id=client_id,
        limit="2",
    )
    quotas = PostgresQuotaEnforcer(factory)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "TRUNCATE accounting.quota_reservations, "
                    "accounting.quota_windows CASCADE"
                )
            )

        first = await quotas.reserve(
            policy=policy,
            tenant_id=tenant_id,
            client_id=client_id,
            idempotency_key="postgres-1",
            amount=Decimal("1"),
            occurred_at=NOW,
        )
        replay = await quotas.reserve(
            policy=policy,
            tenant_id=tenant_id,
            client_id=client_id,
            idempotency_key="postgres-1",
            amount=Decimal("1"),
            occurred_at=NOW,
        )
        second = await quotas.reserve(
            policy=policy,
            tenant_id=tenant_id,
            client_id=client_id,
            idempotency_key="postgres-2",
            amount=Decimal("1"),
            occurred_at=NOW,
        )
        rejected = await quotas.reserve(
            policy=policy,
            tenant_id=tenant_id,
            client_id=client_id,
            idempotency_key="postgres-3",
            amount=Decimal("1"),
            occurred_at=NOW,
        )

        assert first.allowed and second.allowed
        assert replay.reservation.reservation_id == first.reservation.reservation_id
        assert not rejected.allowed

        reconciled = await quotas.reconcile(
            reservation_id=first.reservation.reservation_id,
            actual_amount=Decimal("1"),
            occurred_at=NOW + timedelta(seconds=1),
        )
        replay_reconcile = await quotas.reconcile(
            reservation_id=first.reservation.reservation_id,
            actual_amount=Decimal("1"),
            occurred_at=NOW + timedelta(seconds=2),
        )
        assert reconciled.utilization.consumed == Decimal("1")
        assert replay_reconcile.reservation.actual_amount == Decimal("1")
    finally:
        await engine.dispose()
