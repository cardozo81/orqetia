from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

import pytest

from orqetia.control_plane import (
    InMemoryQuotaPolicyRepository,
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicySnapshot,
    QuotaScope,
)
from orqetia.execution import (
    ExecutionTargetSnapshot,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
)
from orqetia.infrastructure.processes.runtime_quotas import (
    AttemptQuotaCoordinator,
)
from orqetia.providers import ProviderOutcome, ProviderUsage
from orqetia.usage_accounting.quotas import InMemoryQuotaEnforcer

NOW = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)


def _attempt(
    *,
    ownership: OwnershipScope,
    attempt_index: int,
    usage: ProviderUsage | None = None,
) -> ProviderAttempt:
    return ProviderAttempt(
        attempt_id=uuid7(),
        task_id=uuid7(),
        session_id=uuid7(),
        ownership=ownership,
        operation="TASK_EXECUTION",
        target=ExecutionTargetSnapshot("openai", "gpt-test", "standard"),
        cycle=1,
        attempt_index=attempt_index,
        request_reference=f"payload://quota/{attempt_index}",
        request_fingerprint="a" * 64,
        status=ProviderAttemptStatus.PREPARED,
        missing_requirements=("VALID_JSON",),
        created_at=NOW,
        updated_at=NOW,
        usage=usage,
    )


async def _policy(
    repository: InMemoryQuotaPolicyRepository,
    *,
    ownership: OwnershipScope,
    metric: QuotaMetric,
    limit: str,
    enforcement: QuotaEnforcementMode,
    provider_id: str | None = None,
    native_unit: str | None = None,
) -> None:
    await repository.append(
        QuotaPolicySnapshot(
            policy_id=uuid7(),
            version=1,
            scope=QuotaScope.CLIENT,
            tenant_id=ownership.tenant_id,
            client_id=ownership.client_id,
            metric=metric,
            limit=Decimal(limit),
            enforcement=enforcement,
            effective_from=NOW - timedelta(minutes=1),
            period_seconds=60,
            provider_id=provider_id,
            native_unit=native_unit,
        )
    )


@pytest.mark.asyncio
async def test_provider_request_quota_blocks_before_second_side_effect() -> None:
    ownership = OwnershipScope(uuid7(), uuid7())
    policies = InMemoryQuotaPolicyRepository()
    quotas = InMemoryQuotaEnforcer()
    await _policy(
        policies,
        ownership=ownership,
        metric=QuotaMetric.PROVIDER_REQUESTS,
        limit="1",
        enforcement=QuotaEnforcementMode.HARD,
        provider_id="openai",
    )
    coordinator = AttemptQuotaCoordinator(
        policies=policies,
        quotas=quotas,
    )

    first = _attempt(ownership=ownership, attempt_index=1)
    assert await coordinator.pre_dispatch(first) is None
    completed = replace(
        first,
        status=ProviderAttemptStatus.COMPLETED,
        provider_outcome=ProviderOutcome.SUCCESS.value,
        usage=ProviderUsage(input_tokens=2, output_tokens=1),
        terminal_at=NOW,
        updated_at=NOW,
    )
    await coordinator.record(completed)

    second = _attempt(ownership=ownership, attempt_index=2)
    blocked = await coordinator.pre_dispatch(second)
    assert blocked is not None
    assert blocked.outcome is ProviderOutcome.QUOTA_EXHAUSTED
    assert blocked.error_class == "ORQETIA_CLIENT_QUOTA_HARD_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_hard_token_quota_fails_closed_without_safe_estimator() -> None:
    ownership = OwnershipScope(uuid7(), uuid7())
    policies = InMemoryQuotaPolicyRepository()
    quotas = InMemoryQuotaEnforcer()
    await _policy(
        policies,
        ownership=ownership,
        metric=QuotaMetric.TOKENS,
        limit="100",
        enforcement=QuotaEnforcementMode.HARD,
    )
    coordinator = AttemptQuotaCoordinator(
        policies=policies,
        quotas=quotas,
    )

    blocked = await coordinator.pre_dispatch(
        _attempt(ownership=ownership, attempt_index=1)
    )
    assert blocked is not None
    assert (
        blocked.error_class
        == "ORQETIA_CLIENT_QUOTA_USAGE_RESERVATION_ESTIMATE_UNAVAILABLE"
    )


@pytest.mark.asyncio
async def test_soft_token_quota_reconciles_observed_usage() -> None:
    ownership = OwnershipScope(uuid7(), uuid7())
    policies = InMemoryQuotaPolicyRepository()
    quotas = InMemoryQuotaEnforcer()
    await _policy(
        policies,
        ownership=ownership,
        metric=QuotaMetric.TOKENS,
        limit="5",
        enforcement=QuotaEnforcementMode.SOFT,
    )
    coordinator = AttemptQuotaCoordinator(
        policies=policies,
        quotas=quotas,
    )

    attempt = _attempt(ownership=ownership, attempt_index=1)
    assert await coordinator.pre_dispatch(attempt) is None
    completed = replace(
        attempt,
        status=ProviderAttemptStatus.COMPLETED,
        provider_outcome=ProviderOutcome.SUCCESS.value,
        usage=ProviderUsage(input_tokens=4, output_tokens=3),
        terminal_at=NOW,
        updated_at=NOW,
    )
    await coordinator.record(completed)

    reservations = await quotas.list_by_idempotency_key(
        tenant_id=ownership.tenant_id,
        client_id=ownership.client_id,
        idempotency_key=f"attempt:{attempt.attempt_id}",
    )
    assert len(reservations) == 1
    assert reservations[0].actual_amount == Decimal("7")
