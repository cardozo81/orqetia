"""Runtime quota bridge for durable provider attempts.

Control Plane remains the owner of policy definitions. Usage & Accounting remains
the owner of reservation/reconciliation state. This module only coordinates those
boundaries before and after a provider side effect.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from orqetia.control_plane import (
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicyRepository,
    QuotaPolicySnapshot,
)
from orqetia.execution import (
    ExecutionTask,
    ExecutionTaskStore,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
    TaskStatus,
)
from orqetia.providers import (
    OutputKind,
    ProviderAttemptResult,
    ProviderOutcome,
    ProviderUsage,
)
from orqetia.usage_accounting.domain import NativeUsageQuantity
from orqetia.shared.messaging import (
    DataClassification,
    QueueName,
    WorkItem,
    WorkLease,
    WorkQueuePort,
)
from orqetia.usage_accounting.quotas import (
    QuotaEnforcer,
    QuotaReservation,
    QuotaReservationStatus,
)

from .attempt_accounting import provider_usage_to_technical
from .worker import HandlerOutcome

_TASK_QUOTA_METRICS = frozenset(
    {
        QuotaMetric.REQUESTS,
        QuotaMetric.TASKS,
        QuotaMetric.CONCURRENT_TASKS,
    }
)
_ATTEMPT_QUOTA_METRICS = frozenset(
    {
        QuotaMetric.PROVIDER_REQUESTS,
        QuotaMetric.TOKENS,
        QuotaMetric.NATIVE_UNITS,
    }
)
_INTERNAL_QUOTA_ERROR_PREFIX = "ORQETIA_CLIENT_QUOTA_"
CONCURRENT_TASK_QUOTA_HEARTBEAT_OPERATION = "quota.concurrent_task.heartbeat"
CONCURRENT_TASK_QUOTA_HEARTBEAT_OPERATION_VERSION = 1
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RuntimeQuotaStore(QuotaEnforcer, Protocol):
    async def list_by_idempotency_key(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        idempotency_key: str,
    ) -> tuple[QuotaReservation, ...]: ...


class UsageReservationEstimator(Protocol):
    async def estimate(
        self,
        *,
        attempt: ProviderAttempt,
        metric: QuotaMetric,
        native_unit: str | None,
    ) -> Decimal | None:
        """Return a safe pre-dispatch reservation amount, or None if unavailable."""


class TaskQuotaCoordinator:
    """Enforce deterministic task-level quotas once per durable task identity."""

    def __init__(
        self,
        *,
        policies: QuotaPolicyRepository,
        quotas: RuntimeQuotaStore,
        work_queue: WorkQueuePort | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._policies = policies
        self._quotas = quotas
        self._work_queue = work_queue
        self._clock = clock

    async def preflight(self, task: ExecutionTask) -> bool:
        occurred_at = task.started_at or task.created_at
        policies = await self._applicable_policies(
            task,
            occurred_at=occurred_at,
        )
        held: list[QuotaReservation] = []
        for policy in policies:
            decision = await self._quotas.reserve(
                policy=policy,
                tenant_id=task.ownership.tenant_id,
                client_id=task.ownership.client_id,
                idempotency_key=self._idempotency_key(task),
                amount=Decimal("1"),
                occurred_at=occurred_at,
            )
            if not decision.allowed:
                await self._release_many(held)
                return False
            reservation = decision.reservation
            if policy.metric is QuotaMetric.CONCURRENT_TASKS:
                renewed = await self._quotas.renew(
                    policy=policy,
                    reservation_id=reservation.reservation_id,
                    tenant_id=task.ownership.tenant_id,
                    client_id=task.ownership.client_id,
                    occurred_at=self._clock(),
                )
                if not renewed.allowed:
                    await self._release_many(held)
                    return False
                reservation = renewed.reservation
            held.append(reservation)

        for reservation in held:
            if reservation.metric not in {
                QuotaMetric.REQUESTS,
                QuotaMetric.TASKS,
            }:
                continue
            await self._quotas.reconcile(
                reservation_id=reservation.reservation_id,
                actual_amount=Decimal("1"),
                occurred_at=occurred_at,
            )
        await self._schedule_heartbeat(task, policies=policies, generation=1)
        return True

    async def renew_active(self, task: ExecutionTask) -> tuple[bool, float | None]:
        anchor = task.started_at or task.created_at
        policies = await self._applicable_policies(
            task,
            occurred_at=anchor,
        )
        concurrent = tuple(
            policy
            for policy in policies
            if policy.metric is QuotaMetric.CONCURRENT_TASKS
        )
        if not concurrent:
            return True, None

        reservations = await self._quotas.list_by_idempotency_key(
            tenant_id=task.ownership.tenant_id,
            client_id=task.ownership.client_id,
            idempotency_key=self._idempotency_key(task),
        )
        by_policy = {
            (item.policy_id, item.policy_version): item
            for item in reservations
            if item.metric is QuotaMetric.CONCURRENT_TASKS
        }
        now = self._clock()
        for policy in concurrent:
            reservation = by_policy.get((policy.policy_id, policy.version))
            if reservation is None:
                return False, None
            decision = await self._quotas.renew(
                policy=policy,
                reservation_id=reservation.reservation_id,
                tenant_id=task.ownership.tenant_id,
                client_id=task.ownership.client_id,
                occurred_at=now,
            )
            if not decision.allowed:
                return False, None
        return True, self._heartbeat_delay_seconds(concurrent)

    async def schedule_next_heartbeat(
        self,
        task: ExecutionTask,
        *,
        generation: int,
    ) -> None:
        anchor = task.started_at or task.created_at
        policies = await self._applicable_policies(
            task,
            occurred_at=anchor,
        )
        await self._schedule_heartbeat(
            task,
            policies=policies,
            generation=generation,
        )

    async def release(self, task: ExecutionTask) -> None:
        reservations = await self._quotas.list_by_idempotency_key(
            tenant_id=task.ownership.tenant_id,
            client_id=task.ownership.client_id,
            idempotency_key=self._idempotency_key(task),
        )
        await self._release_many(
            [
                item
                for item in reservations
                if item.metric is QuotaMetric.CONCURRENT_TASKS
                and item.status
                in {
                    QuotaReservationStatus.RESERVED,
                    QuotaReservationStatus.EXPIRED,
                }
            ]
        )

    async def _applicable_policies(
        self,
        task: ExecutionTask,
        *,
        occurred_at: datetime,
    ) -> tuple[QuotaPolicySnapshot, ...]:
        policies = await _effective_subject_policies(
            repository=self._policies,
            tenant_id=task.ownership.tenant_id,
            client_id=task.ownership.client_id,
            occurred_at=occurred_at,
        )
        return tuple(
            policy
            for policy in policies
            if policy.metric in _TASK_QUOTA_METRICS
            and policy.provider_id is None
            and policy.native_unit is None
        )

    async def _schedule_heartbeat(
        self,
        task: ExecutionTask,
        *,
        policies: tuple[QuotaPolicySnapshot, ...],
        generation: int,
    ) -> None:
        if self._work_queue is None:
            return
        concurrent = tuple(
            policy
            for policy in policies
            if policy.metric is QuotaMetric.CONCURRENT_TASKS
        )
        delay = self._heartbeat_delay_seconds(concurrent)
        if delay is None:
            return
        await self._work_queue.enqueue(
            build_concurrent_task_quota_heartbeat_work_item(
                task=task,
                generation=generation,
                available_at=self._clock() + timedelta(seconds=delay),
            )
        )

    @staticmethod
    def _heartbeat_delay_seconds(
        policies: tuple[QuotaPolicySnapshot, ...],
    ) -> float | None:
        if not policies:
            return None
        shortest = min(policy.reservation_ttl_seconds for policy in policies)
        return max(0.25, shortest / 3)

    async def _release_many(
        self,
        reservations: list[QuotaReservation],
    ) -> None:
        for reservation in reservations:
            if reservation.status not in {
                QuotaReservationStatus.RESERVED,
                QuotaReservationStatus.EXPIRED,
            }:
                continue
            await self._quotas.release(
                reservation_id=reservation.reservation_id,
                occurred_at=self._clock(),
            )

    @staticmethod
    def _idempotency_key(task: ExecutionTask) -> str:
        return f"task:{task.task_id}"


def concurrent_task_quota_heartbeat_work_id(
    task_id: UUID,
    generation: int,
) -> UUID:
    if generation < 1:
        raise ValueError("heartbeat generation must be positive")
    return uuid5(
        NAMESPACE_URL,
        f"orqetia:quota-heartbeat:{task_id}:{generation}",
    )


def build_concurrent_task_quota_heartbeat_work_item(
    *,
    task: ExecutionTask,
    generation: int,
    available_at: datetime,
) -> WorkItem:
    if available_at.tzinfo is None or available_at.utcoffset() is None:
        raise ValueError("available_at must be timezone-aware")
    return WorkItem(
        work_id=concurrent_task_quota_heartbeat_work_id(
            task.task_id,
            generation,
        ),
        queue_name=QueueName.EXECUTION,
        operation_type=CONCURRENT_TASK_QUOTA_HEARTBEAT_OPERATION,
        operation_version=CONCURRENT_TASK_QUOTA_HEARTBEAT_OPERATION_VERSION,
        tenant_id=task.ownership.tenant_id,
        client_id=task.ownership.client_id,
        resource_type="execution_task",
        resource_id=task.task_id,
        data_classification=DataClassification.CLIENT_PRIVATE,
        payload={
            "task_id": str(task.task_id),
            "generation": generation,
        },
        available_at=available_at,
        logical_operation_id=str(task.task_id),
    )


class ConcurrentTaskQuotaHeartbeatHandler:
    def __init__(
        self,
        *,
        tasks: ExecutionTaskStore,
        quotas: TaskQuotaCoordinator,
        clock: Clock = _utc_now,
    ) -> None:
        self._tasks = tasks
        self._quotas = quotas
        self._clock = clock

    async def __call__(self, lease: WorkLease) -> HandlerOutcome:
        if lease.tenant_id is None or lease.client_id is None:
            return HandlerOutcome.complete()
        try:
            task_id = UUID(str(lease.payload.get("task_id")))
            generation = int(lease.payload.get("generation", 0))
        except (TypeError, ValueError, AttributeError):
            return HandlerOutcome.complete()
        if generation < 1:
            return HandlerOutcome.complete()
        if lease.resource_id is not None and lease.resource_id != task_id:
            return HandlerOutcome.complete()

        task = await self._tasks.get_owned(
            scope=OwnershipScope(
                tenant_id=lease.tenant_id,
                client_id=lease.client_id,
            ),
            task_id=task_id,
        )
        if task is None:
            return HandlerOutcome.complete()
        if task.status.terminal:
            try:
                await self._quotas.release(task)
            except Exception as exc:
                return HandlerOutcome.requeue_infrastructure(
                    available_at=self._clock() + timedelta(seconds=1),
                    error_class=(
                        "QUOTA_HEARTBEAT_RELEASE_"
                        f"{type(exc).__name__.upper()}"
                    ),
                )
            return HandlerOutcome.complete()
        if task.status not in {TaskStatus.RUNNING, TaskStatus.CANCELLING}:
            return HandlerOutcome.complete()

        try:
            allowed, delay = await self._quotas.renew_active(task)
            if not allowed or delay is None:
                return HandlerOutcome.complete()
            await self._quotas.schedule_next_heartbeat(
                task,
                generation=generation + 1,
            )
        except Exception as exc:
            return HandlerOutcome.requeue_infrastructure(
                available_at=self._clock() + timedelta(seconds=1),
                error_class=f"QUOTA_HEARTBEAT_{type(exc).__name__.upper()}",
            )
        return HandlerOutcome.complete()


class AttemptQuotaCoordinator:
    """Reserve before dispatch and reconcile observed usage after durable completion."""

    def __init__(
        self,
        *,
        policies: QuotaPolicyRepository,
        quotas: RuntimeQuotaStore,
        usage_estimator: UsageReservationEstimator | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._policies = policies
        self._quotas = quotas
        self._usage_estimator = usage_estimator
        self._clock = clock

    async def pre_dispatch(
        self,
        attempt: ProviderAttempt,
    ) -> ProviderAttemptResult | None:
        occurred_at = self._clock()
        policies = await self._applicable_policies(
            attempt,
            occurred_at=occurred_at,
        )
        held: list[QuotaReservation] = []
        for policy in policies:
            amount = await self._reservation_amount(
                policy=policy,
                attempt=attempt,
            )
            if amount is None:
                await self._release_many(held)
                return self._blocked(
                    attempt,
                    "USAGE_RESERVATION_ESTIMATE_UNAVAILABLE",
                )
            decision = await self._quotas.reserve(
                policy=policy,
                tenant_id=attempt.ownership.tenant_id,
                client_id=attempt.ownership.client_id,
                idempotency_key=self._idempotency_key(attempt),
                amount=amount,
                occurred_at=occurred_at,
                provider_id=attempt.target.provider_id,
                native_unit=policy.native_unit,
            )
            if not decision.allowed:
                await self._release_many(held)
                return self._blocked(attempt, "HARD_LIMIT_EXCEEDED")
            held.append(decision.reservation)
        return None

    async def release(self, attempt: ProviderAttempt) -> None:
        reservations = await self._reservations(attempt)
        await self._release_many(
            [
                item
                for item in reservations
                if item.status
                in {
                    QuotaReservationStatus.RESERVED,
                    QuotaReservationStatus.EXPIRED,
                }
            ]
        )

    async def mark_ambiguous(self, attempt: ProviderAttempt) -> None:
        """Count provider requests but keep unknown usage reserved until expiry."""

        for reservation in await self._reservations(attempt):
            if reservation.metric is not QuotaMetric.PROVIDER_REQUESTS:
                continue
            if reservation.status in {
                QuotaReservationStatus.RESERVED,
                QuotaReservationStatus.EXPIRED,
                QuotaReservationStatus.RECONCILED,
            }:
                await self._quotas.reconcile(
                    reservation_id=reservation.reservation_id,
                    actual_amount=Decimal("1"),
                    occurred_at=attempt.updated_at,
                )

    async def record(self, attempt: ProviderAttempt) -> None:
        if attempt.status is not ProviderAttemptStatus.COMPLETED:
            return
        technical = provider_usage_to_technical(attempt)
        for reservation in await self._reservations(attempt):
            if reservation.status in {
                QuotaReservationStatus.REJECTED,
                QuotaReservationStatus.RELEASED,
            }:
                continue
            actual = self._actual_amount(
                reservation=reservation,
                total_tokens=technical.total_tokens,
                native=technical.native,
            )
            await self._quotas.reconcile(
                reservation_id=reservation.reservation_id,
                actual_amount=actual,
                occurred_at=attempt.terminal_at or attempt.updated_at,
            )

    async def _applicable_policies(
        self,
        attempt: ProviderAttempt,
        *,
        occurred_at: datetime,
    ) -> tuple[QuotaPolicySnapshot, ...]:
        policies = await _effective_subject_policies(
            repository=self._policies,
            tenant_id=attempt.ownership.tenant_id,
            client_id=attempt.ownership.client_id,
            occurred_at=occurred_at,
        )
        return tuple(
            policy
            for policy in policies
            if policy.metric in _ATTEMPT_QUOTA_METRICS
            and (
                policy.provider_id is None
                or policy.provider_id == attempt.target.provider_id
            )
        )

    async def _reservation_amount(
        self,
        *,
        policy: QuotaPolicySnapshot,
        attempt: ProviderAttempt,
    ) -> Decimal | None:
        if policy.metric is QuotaMetric.PROVIDER_REQUESTS:
            return Decimal("1")
        if self._usage_estimator is not None:
            estimate = await self._usage_estimator.estimate(
                attempt=attempt,
                metric=policy.metric,
                native_unit=policy.native_unit,
            )
            if estimate is not None:
                if estimate < 0:
                    raise ValueError("usage reservation estimate cannot be negative")
                return estimate
        if policy.enforcement is QuotaEnforcementMode.SOFT:
            return Decimal("0")
        return None

    async def _reservations(
        self,
        attempt: ProviderAttempt,
    ) -> tuple[QuotaReservation, ...]:
        return await self._quotas.list_by_idempotency_key(
            tenant_id=attempt.ownership.tenant_id,
            client_id=attempt.ownership.client_id,
            idempotency_key=self._idempotency_key(attempt),
        )

    async def _release_many(
        self,
        reservations: list[QuotaReservation],
    ) -> None:
        for reservation in reservations:
            if reservation.status not in {
                QuotaReservationStatus.RESERVED,
                QuotaReservationStatus.EXPIRED,
            }:
                continue
            await self._quotas.release(
                reservation_id=reservation.reservation_id,
                occurred_at=self._clock(),
            )

    @staticmethod
    def _actual_amount(
        *,
        reservation: QuotaReservation,
        total_tokens: int | None,
        native: tuple[NativeUsageQuantity, ...],
    ) -> Decimal:
        if reservation.metric is QuotaMetric.PROVIDER_REQUESTS:
            return Decimal("1")
        if reservation.metric is QuotaMetric.TOKENS:
            return Decimal(total_tokens or 0)
        if reservation.metric is QuotaMetric.NATIVE_UNITS:
            unit = reservation.native_unit
            return sum(
                (
                    item.quantity
                    for item in native
                    if item.unit == unit
                ),
                Decimal("0"),
            )
        raise ValueError("unsupported attempt quota metric")

    @staticmethod
    def _idempotency_key(attempt: ProviderAttempt) -> str:
        return f"attempt:{attempt.attempt_id}"

    @staticmethod
    def _blocked(
        attempt: ProviderAttempt,
        reason: str,
    ) -> ProviderAttemptResult:
        return ProviderAttemptResult(
            attempt_id=attempt.attempt_id,
            outcome=ProviderOutcome.QUOTA_EXHAUSTED,
            output_kind=OutputKind.NONE,
            accepted_requirements=(),
            missing_requirements=attempt.missing_requirements,
            simulated_latency_ms=0,
            error_class=f"{_INTERNAL_QUOTA_ERROR_PREFIX}{reason}",
            usage=ProviderUsage(),
        )


def is_internal_quota_error(error_class: str | None) -> bool:
    return bool(
        error_class
        and error_class.startswith(_INTERNAL_QUOTA_ERROR_PREFIX)
    )


async def _effective_subject_policies(
    *,
    repository: QuotaPolicyRepository,
    tenant_id: UUID,
    client_id: UUID,
    occurred_at: datetime,
) -> tuple[QuotaPolicySnapshot, ...]:
    tenant = await repository.list_for_subject(
        tenant_id=tenant_id,
        client_id=None,
    )
    client = await repository.list_for_subject(
        tenant_id=tenant_id,
        client_id=client_id,
    )
    effective: dict[tuple[object, ...], QuotaPolicySnapshot] = {}
    for policy in (*tenant, *client):
        if not policy.active_at(occurred_at):
            continue
        key = (
            policy.scope,
            policy.tenant_id,
            policy.client_id,
            policy.metric,
            policy.provider_id,
            policy.native_unit,
        )
        current = effective.get(key)
        if current is None or (
            policy.effective_from,
            policy.version,
            str(policy.policy_id),
        ) > (
            current.effective_from,
            current.version,
            str(current.policy_id),
        ):
            effective[key] = policy
    return tuple(
        sorted(
            effective.values(),
            key=lambda policy: (
                policy.scope.value,
                policy.metric.value,
                policy.provider_id or "",
                policy.native_unit or "",
                str(policy.policy_id),
            ),
        )
    )
