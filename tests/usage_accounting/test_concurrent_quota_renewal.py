from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

import pytest
import sqlalchemy as sa

from orqetia.control_plane import (
    InMemoryQuotaPolicyRepository,
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicySnapshot,
    QuotaScope,
)
from orqetia.execution import (
    ExecutionMode,
    ExecutionTask,
    OwnershipScope,
    TaskPayloadReferences,
    TaskStatus,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.infrastructure.processes.runtime_quotas import (
    ConcurrentTaskQuotaHeartbeatHandler,
    TaskQuotaCoordinator,
)
from orqetia.settings import RuntimeSettings
from orqetia.shared.messaging import WorkLease
from orqetia.usage_accounting.quota_postgres import PostgresQuotaEnforcer
from orqetia.usage_accounting.quotas import (
    InMemoryQuotaEnforcer,
    QuotaReservationStatus,
)

NOW = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)


def _policy(
    *,
    ownership: OwnershipScope,
    ttl: int = 3,
    limit: str = "1",
) -> QuotaPolicySnapshot:
    return QuotaPolicySnapshot(
        policy_id=uuid7(),
        version=1,
        scope=QuotaScope.CLIENT,
        tenant_id=ownership.tenant_id,
        client_id=ownership.client_id,
        metric=QuotaMetric.CONCURRENT_TASKS,
        limit=Decimal(limit),
        enforcement=QuotaEnforcementMode.HARD,
        effective_from=NOW - timedelta(minutes=1),
        period_seconds=None,
        reservation_ttl_seconds=ttl,
    )


def _task(
    *,
    ownership: OwnershipScope,
    task_id=None,
) -> ExecutionTask:
    identifier = task_id or uuid7()
    return ExecutionTask(
        task_id=identifier,
        session_id=uuid7(),
        ownership=ownership,
        operation="TASK_EXECUTION",
        status=TaskStatus.RUNNING,
        effective_policy_version_id=uuid7(),
        requested_execution_mode=ExecutionMode.AUTO,
        requirements=("VALID_JSON",),
        accepted_requirements=(),
        missing_requirements=("VALID_JSON",),
        payloads=TaskPayloadReferences(
            input_reference=f"payload://quota/{identifier}",
            input_fingerprint="d" * 64,
        ),
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
    )


class _Queue:
    def __init__(self) -> None:
        self.items = []

    async def enqueue(self, item) -> None:
        if all(existing.work_id != item.work_id for existing in self.items):
            self.items.append(item)


class _TaskStore:
    def __init__(self, task: ExecutionTask) -> None:
        self.task = task

    async def get_owned(self, *, scope, task_id):
        if scope != self.task.ownership or task_id != self.task.task_id:
            return None
        return self.task


def _lease(item) -> WorkLease:
    return WorkLease(
        work_id=item.work_id,
        queue_name=item.queue_name,
        operation_type=item.operation_type,
        operation_version=item.operation_version,
        payload=item.payload,
        data_classification=item.data_classification,
        tenant_id=item.tenant_id,
        client_id=item.client_id,
        resource_type=item.resource_type,
        resource_id=item.resource_id,
        correlation_id=item.correlation_id,
        causation_id=item.causation_id,
        trace_id=item.trace_id,
        logical_operation_id=item.logical_operation_id,
        lease_owner="quota-test",
        lease_until=NOW + timedelta(minutes=1),
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_renewal_preserves_reservation_identity_and_extends_ttl() -> None:
    ownership = OwnershipScope(uuid7(), uuid7())
    policies = InMemoryQuotaPolicyRepository()
    policy = _policy(ownership=ownership)
    await policies.append(policy)
    quotas = InMemoryQuotaEnforcer()
    now = [NOW]
    coordinator = TaskQuotaCoordinator(
        policies=policies,
        quotas=quotas,
        clock=lambda: now[0],
    )
    task = _task(ownership=ownership)

    assert await coordinator.preflight(task)
    before = await quotas.list_by_idempotency_key(
        tenant_id=ownership.tenant_id,
        client_id=ownership.client_id,
        idempotency_key=f"task:{task.task_id}",
    )
    assert len(before) == 1
    original = before[0]

    now[0] = NOW + timedelta(seconds=2)
    allowed, delay = await coordinator.renew_active(task)
    assert allowed
    assert delay == 1.0

    after = await quotas.list_by_idempotency_key(
        tenant_id=ownership.tenant_id,
        client_id=ownership.client_id,
        idempotency_key=f"task:{task.task_id}",
    )
    assert len(after) == 1
    assert after[0].reservation_id == original.reservation_id
    assert after[0].expires_at == NOW + timedelta(seconds=5)
    assert after[0].status is QuotaReservationStatus.RESERVED


@pytest.mark.asyncio
async def test_expired_reservation_does_not_duplicate_when_capacity_was_reallocated() -> None:
    ownership = OwnershipScope(uuid7(), uuid7())
    policies = InMemoryQuotaPolicyRepository()
    policy = _policy(ownership=ownership)
    await policies.append(policy)
    quotas = InMemoryQuotaEnforcer()
    now = [NOW]
    coordinator = TaskQuotaCoordinator(
        policies=policies,
        quotas=quotas,
        clock=lambda: now[0],
    )
    first = _task(ownership=ownership)

    assert await coordinator.preflight(first)
    now[0] = NOW + timedelta(seconds=4)
    second = replace(
        _task(ownership=ownership),
        created_at=now[0],
        updated_at=now[0],
        started_at=now[0],
    )
    assert await coordinator.preflight(second)

    allowed, delay = await coordinator.renew_active(first)
    assert not allowed
    assert delay is None

    reservations = await quotas.list_by_idempotency_key(
        tenant_id=ownership.tenant_id,
        client_id=ownership.client_id,
        idempotency_key=f"task:{first.task_id}",
    )
    assert len(reservations) == 1
    assert reservations[0].status is QuotaReservationStatus.EXPIRED


@pytest.mark.asyncio
async def test_heartbeat_schedules_one_successor_and_terminal_task_releases() -> None:
    ownership = OwnershipScope(uuid7(), uuid7())
    policies = InMemoryQuotaPolicyRepository()
    await policies.append(_policy(ownership=ownership))
    quotas = InMemoryQuotaEnforcer()
    queue = _Queue()
    now = [NOW]
    task = _task(ownership=ownership)
    coordinator = TaskQuotaCoordinator(
        policies=policies,
        quotas=quotas,
        work_queue=queue,
        clock=lambda: now[0],
    )
    assert await coordinator.preflight(task)
    assert len(queue.items) == 1
    first_heartbeat = queue.items[0]
    assert first_heartbeat.payload["generation"] == 1

    now[0] = NOW + timedelta(seconds=1)
    handler = ConcurrentTaskQuotaHeartbeatHandler(
        tasks=_TaskStore(task),
        quotas=coordinator,
        clock=lambda: now[0],
    )
    outcome = await handler(_lease(first_heartbeat))
    assert outcome.disposition.value == "COMPLETE"
    assert len(queue.items) == 2
    assert queue.items[1].payload["generation"] == 2
    assert queue.items[1].work_id != first_heartbeat.work_id

    terminal = replace(
        task,
        status=TaskStatus.CANCELLED,
        terminal_at=NOW + timedelta(seconds=2),
        updated_at=NOW + timedelta(seconds=2),
    )
    terminal_handler = ConcurrentTaskQuotaHeartbeatHandler(
        tasks=_TaskStore(terminal),
        quotas=coordinator,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    await terminal_handler(_lease(queue.items[1]))
    assert len(queue.items) == 2

    reservations = await quotas.list_by_idempotency_key(
        tenant_id=ownership.tenant_id,
        client_id=ownership.client_id,
        idempotency_key=f"task:{task.task_id}",
    )
    assert reservations[0].status is QuotaReservationStatus.RELEASED


@pytest.mark.asyncio
@pytest.mark.skipif(
    "ORQETIA_DATABASE_DSN" not in os.environ,
    reason="PostgreSQL integration DSN is not configured",
)
async def test_postgres_concurrent_renewal_keeps_single_reservation() -> None:
    settings = RuntimeSettings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    ownership = OwnershipScope(uuid7(), uuid7())
    policy = _policy(ownership=ownership)
    quotas = PostgresQuotaEnforcer(factory)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "TRUNCATE accounting.quota_reservations, "
                    "accounting.quota_windows CASCADE"
                )
            )

        reserved = await quotas.reserve(
            policy=policy,
            tenant_id=ownership.tenant_id,
            client_id=ownership.client_id,
            idempotency_key="postgres-heartbeat",
            amount=Decimal("1"),
            occurred_at=NOW,
        )
        renewed = await quotas.renew(
            policy=policy,
            reservation_id=reserved.reservation.reservation_id,
            tenant_id=ownership.tenant_id,
            client_id=ownership.client_id,
            occurred_at=NOW + timedelta(seconds=2),
        )

        assert renewed.allowed
        assert (
            renewed.reservation.reservation_id
            == reserved.reservation.reservation_id
        )
        assert renewed.reservation.expires_at == NOW + timedelta(seconds=5)
        rows = await quotas.list_by_idempotency_key(
            tenant_id=ownership.tenant_id,
            client_id=ownership.client_id,
            idempotency_key="postgres-heartbeat",
        )
        assert len(rows) == 1
    finally:
        await engine.dispose()
