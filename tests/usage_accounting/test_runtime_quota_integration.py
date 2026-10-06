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
            period_seconds=(
                None
                if metric is QuotaMetric.CONCURRENT_TASKS
                else 60
            ),
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


@pytest.mark.asyncio
async def test_task_quota_is_consumed_once_across_replay() -> None:
    ownership = OwnershipScope(uuid7(), uuid7())
    policies = InMemoryQuotaPolicyRepository()
    quotas = InMemoryQuotaEnforcer()
    await _policy(
        policies,
        ownership=ownership,
        metric=QuotaMetric.TASKS,
        limit="1",
        enforcement=QuotaEnforcementMode.HARD,
    )
    from orqetia.execution import (
        ExecutionMode,
        ExecutionTask,
        TaskPayloadReferences,
        TaskStatus,
    )
    from orqetia.infrastructure.processes.runtime_quotas import (
        TaskQuotaCoordinator,
    )

    task = ExecutionTask(
        task_id=uuid7(),
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
            input_reference="payload://quota/task",
            input_fingerprint="b" * 64,
        ),
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
    )
    coordinator = TaskQuotaCoordinator(
        policies=policies,
        quotas=quotas,
        clock=lambda: NOW,
    )
    assert await coordinator.preflight(task)
    assert await coordinator.preflight(task)

    reservations = await quotas.list_by_idempotency_key(
        tenant_id=ownership.tenant_id,
        client_id=ownership.client_id,
        idempotency_key=f"task:{task.task_id}",
    )
    assert len(reservations) == 1
    assert reservations[0].actual_amount == Decimal("1")


@pytest.mark.asyncio
async def test_concurrent_task_quota_releases_on_terminal_cleanup() -> None:
    ownership = OwnershipScope(uuid7(), uuid7())
    policies = InMemoryQuotaPolicyRepository()
    quotas = InMemoryQuotaEnforcer()
    await _policy(
        policies,
        ownership=ownership,
        metric=QuotaMetric.CONCURRENT_TASKS,
        limit="1",
        enforcement=QuotaEnforcementMode.HARD,
    )
    from orqetia.execution import (
        ExecutionMode,
        ExecutionTask,
        TaskPayloadReferences,
        TaskStatus,
    )
    from orqetia.infrastructure.processes.runtime_quotas import (
        TaskQuotaCoordinator,
    )

    def task_for(task_id):
        return ExecutionTask(
            task_id=task_id,
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
                input_reference=f"payload://quota/{task_id}",
                input_fingerprint="c" * 64,
            ),
            created_at=NOW,
            updated_at=NOW,
            started_at=NOW,
        )

    coordinator = TaskQuotaCoordinator(
        policies=policies,
        quotas=quotas,
        clock=lambda: NOW + timedelta(seconds=1),
    )
    first = task_for(uuid7())
    blocked = task_for(uuid7())
    assert await coordinator.preflight(first)
    assert not await coordinator.preflight(blocked)
    await coordinator.release(first)
    replacement = task_for(uuid7())
    assert await coordinator.preflight(replacement)
