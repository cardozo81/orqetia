"""Runtime quota bridge for durable provider attempts.

Control Plane remains the owner of policy definitions. Usage & Accounting remains
the owner of reservation/reconciliation state. This module only coordinates those
boundaries before and after a provider side effect.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol
from uuid import UUID

from orqetia.control_plane import (
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicyRepository,
    QuotaPolicySnapshot,
)
from orqetia.execution import ProviderAttempt, ProviderAttemptStatus
from orqetia.providers import (
    OutputKind,
    ProviderAttemptResult,
    ProviderOutcome,
    ProviderUsage,
)
from orqetia.usage_accounting.quotas import (
    QuotaEnforcer,
    QuotaReservation,
    QuotaReservationStatus,
)

from .attempt_accounting import provider_usage_to_technical

_ATTEMPT_QUOTA_METRICS = frozenset(
    {
        QuotaMetric.PROVIDER_REQUESTS,
        QuotaMetric.TOKENS,
        QuotaMetric.NATIVE_UNITS,
    }
)
_INTERNAL_QUOTA_ERROR_PREFIX = "ORQETIA_CLIENT_QUOTA_"


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


class AttemptQuotaCoordinator:
    """Reserve before dispatch and reconcile observed usage after durable completion."""

    def __init__(
        self,
        *,
        policies: QuotaPolicyRepository,
        quotas: RuntimeQuotaStore,
        usage_estimator: UsageReservationEstimator | None = None,
    ) -> None:
        self._policies = policies
        self._quotas = quotas
        self._usage_estimator = usage_estimator

    async def pre_dispatch(
        self,
        attempt: ProviderAttempt,
    ) -> ProviderAttemptResult | None:
        policies = await self._applicable_policies(attempt)
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
                occurred_at=attempt.created_at,
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
                attempt=attempt,
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
    ) -> tuple[QuotaPolicySnapshot, ...]:
        tenant = await self._policies.list_for_subject(
            tenant_id=attempt.ownership.tenant_id,
            client_id=None,
        )
        client = await self._policies.list_for_subject(
            tenant_id=attempt.ownership.tenant_id,
            client_id=attempt.ownership.client_id,
        )
        effective: dict[tuple[object, ...], QuotaPolicySnapshot] = {}
        for policy in (*tenant, *client):
            if policy.metric not in _ATTEMPT_QUOTA_METRICS:
                continue
            if not policy.active_at(attempt.created_at):
                continue
            if (
                policy.provider_id is not None
                and policy.provider_id != attempt.target.provider_id
            ):
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
                occurred_at=reservation.created_at,
            )

    @staticmethod
    def _actual_amount(
        *,
        reservation: QuotaReservation,
        attempt: ProviderAttempt,
        total_tokens: int | None,
        native: tuple[object, ...],
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
                    if getattr(item, "unit", None) == unit
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
