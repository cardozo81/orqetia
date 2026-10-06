from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

import pytest

from orqetia.read_models import (
    BackofficeReportAccess,
    ExternalCapacityIndicator,
    InMemoryReportRollupStore,
    IntelligenceDimension,
    IntelligenceQuery,
    OperationalFinancialIntelligenceService,
    QuotaUtilizationIndicator,
    ReportQuery,
    ReportRollup,
)

START = datetime(2026, 10, 5, 20, tzinfo=UTC)
END = START + timedelta(hours=1)
AS_OF = END + timedelta(minutes=1)


def _rollup(
    *,
    tenant_id,
    client_id,
    provider: str,
    model: str,
    status: str,
    attempts: int,
    cost: str | None,
    currency: str | None,
    latency: int,
    retries: int = 0,
    error_class: str | None = None,
    period_start: datetime = START,
    requests: int = 1,
    tasks: int = 1,
    peak_concurrent: int = 1,
    quota_utilization: str | None = None,
    fallbacks: int = 0,
    health_events: int = 0,
    quarantine_events: int = 0,
    session_id=None,
    task_id=None,
    attempt_id=None,
    policy_version_id=None,
) -> ReportRollup:
    period_end = period_start + timedelta(hours=1)
    return ReportRollup(
        rollup_id=uuid7(),
        period_start=period_start,
        period_end=period_end,
        as_of=period_end + timedelta(minutes=1),
        tenant_id=tenant_id,
        client_id=client_id,
        client_credential_id=uuid7(),
        provider_id=provider,
        provider_account_id=uuid7(),
        provider_credential_id=uuid7(),
        session_id=session_id,
        task_id=task_id,
        attempt_id=attempt_id,
        policy_version_id=policy_version_id,
        model_id=model,
        status=status,
        error_class=error_class,
        requests=requests,
        tasks=tasks,
        attempts=attempts,
        peak_concurrent=peak_concurrent,
        quota_utilization=(
            None if quota_utilization is None else Decimal(quota_utilization)
        ),
        input_tokens=100 * attempts,
        cached_input_tokens=10 * attempts,
        output_tokens=20 * attempts,
        reasoning_tokens=5 * attempts,
        total_tokens=120 * attempts,
        latency_ms_total=latency,
        cycles=attempts,
        retries=retries,
        fallbacks=fallbacks,
        health_events=health_events,
        quarantine_events=quarantine_events,
        estimated_cost=None if cost is None else Decimal(cost),
        estimated_currency=currency,
        observed_cost=None if cost is None else Decimal(cost),
        observed_currency=currency,
        unpriced_attempts=attempts if cost is None else 0,
    )


class CapacitySource:
    async def read(self, *, filters):
        assert filters.provider_id == "openai"
        return (
            ExternalCapacityIndicator(
                provider_id="openai",
                provider_account_id=uuid7(),
                native_unit="CREDIT",
                observed_at=AS_OF,
                source="PROVIDER_API",
                remaining=Decimal("40"),
                limit=Decimal("100"),
            ),
        )


class QuotaSource:
    async def read(self, *, filters):
        assert filters.provider_id == "openai"
        return (
            QuotaUtilizationIndicator(
                tenant_id=filters.tenant_id,
                client_id=filters.client_id,
                metric="TOKENS",
                consumed=Decimal("1000"),
                reserved=Decimal("100"),
                limit=Decimal("5000"),
                burst=Decimal("500"),
                window_key="tokens:2026-10-05",
            ),
        )


@pytest.mark.asyncio
async def test_cross_dimension_intelligence_keeps_currency_separate() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    store = InMemoryReportRollupStore()
    await store.put(
        _rollup(
            tenant_id=tenant_id,
            client_id=client_id,
            provider="openai",
            model="gpt-x",
            status="SUCCESS",
            attempts=2,
            cost="1.25",
            currency="USD",
            latency=300,
            retries=1,
        )
    )
    await store.put(
        _rollup(
            tenant_id=tenant_id,
            client_id=client_id,
            provider="openai",
            model="gpt-x",
            status="FAILED",
            attempts=1,
            cost="2.00",
            currency="EUR",
            latency=400,
            error_class="RATE_LIMIT",
        )
    )

    service = OperationalFinancialIntelligenceService(
        rollups=store,
        capacity=CapacitySource(),
        quotas=QuotaSource(),
    )
    result = await service.analyze(
        access=BackofficeReportAccess(
            can_view_financial=True,
            can_export=True,
            allowed_tenant_ids=frozenset({tenant_id}),
        ),
        query=IntelligenceQuery(
            filters=ReportQuery(
                tenant_id=tenant_id,
                client_id=client_id,
                provider_id="openai",
            ),
            group_by=(
                IntelligenceDimension.CLIENT,
                IntelligenceDimension.PROVIDER,
                IntelligenceDimension.MODEL,
            ),
        ),
    )
    assert len(result.rows) == 1
    metrics = result.rows[0].metrics
    assert metrics.attempts == 3
    assert metrics.failures == 1
    assert metrics.successes == 2
    assert metrics.retries == 1
    assert metrics.average_latency_ms == Decimal("700") / Decimal("3")
    assert metrics.failure_rate == Decimal("1") / Decimal("3")
    assert [(item.currency, item.amount) for item in metrics.estimated_costs] == [
        ("EUR", Decimal("2.00")),
        ("USD", Decimal("1.25")),
    ]
    assert result.external_capacity[0].remaining == Decimal("40")
    assert result.quota_utilization[0].consumed == Decimal("1000")
    assert result.timezone == "UTC"


@pytest.mark.asyncio
async def test_intelligence_authorization_is_filter_bound() -> None:
    tenant_id = uuid7()
    service = OperationalFinancialIntelligenceService(
        rollups=InMemoryReportRollupStore()
    )
    access = BackofficeReportAccess(
        can_view_financial=True,
        can_export=False,
        allowed_tenant_ids=frozenset({tenant_id}),
    )
    with pytest.raises(PermissionError, match="tenant filter is required"):
        await service.analyze(
            access=access,
            query=IntelligenceQuery(
                filters=ReportQuery(),
                group_by=(IntelligenceDimension.PROVIDER,),
            ),
        )
    with pytest.raises(PermissionError, match="outside authorized scope"):
        await service.analyze(
            access=access,
            query=IntelligenceQuery(
                filters=ReportQuery(tenant_id=uuid7()),
                group_by=(IntelligenceDimension.PROVIDER,),
            ),
        )
    with pytest.raises(PermissionError, match="financial intelligence"):
        await service.analyze(
            access=BackofficeReportAccess(
                can_view_financial=False,
                can_export=False,
                allowed_tenant_ids=frozenset({tenant_id}),
            ),
            query=IntelligenceQuery(
                filters=ReportQuery(tenant_id=tenant_id),
                group_by=(IntelligenceDimension.PROVIDER,),
            ),
        )


@pytest.mark.asyncio
async def test_intelligence_synthetic_volume_uses_rollups_not_ledger() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    store = InMemoryReportRollupStore()
    for index in range(1000):
        await store.put(
            _rollup(
                tenant_id=tenant_id,
                client_id=client_id,
                provider=f"provider-{index % 5}",
                model=f"model-{index % 3}",
                status="PARTIAL" if index % 11 == 0 else "SUCCESS",
                attempts=1,
                cost="0.01",
                currency="USD",
                latency=10,
                retries=1 if index % 13 == 0 else 0,
                period_start=START + timedelta(hours=index % 24),
            )
        )

    result = await OperationalFinancialIntelligenceService(
        rollups=store
    ).analyze(
        access=BackofficeReportAccess(
            can_view_financial=True,
            can_export=False,
            allowed_tenant_ids=frozenset({tenant_id}),
        ),
        query=IntelligenceQuery(
            filters=ReportQuery(
                tenant_id=tenant_id,
                client_id=client_id,
            ),
            group_by=(
                IntelligenceDimension.PROVIDER,
                IntelligenceDimension.MODEL,
            ),
        ),
    )
    assert result.source_row_count == 1000
    assert sum(row.metrics.attempts for row in result.rows) == 1000
    assert len(result.rows) == 15



@pytest.mark.asyncio
async def test_intelligence_covers_execution_policy_and_operational_metrics() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    session_id, task_id, attempt_id, policy_version_id = (
        uuid7(),
        uuid7(),
        uuid7(),
        uuid7(),
    )
    store = InMemoryReportRollupStore()
    await store.put(
        _rollup(
            tenant_id=tenant_id,
            client_id=client_id,
            provider="openai",
            model="gpt-x",
            status="PARTIAL",
            attempts=3,
            cost="0.30",
            currency="USD",
            latency=900,
            retries=2,
            requests=2,
            tasks=1,
            peak_concurrent=4,
            quota_utilization="0.75",
            fallbacks=1,
            health_events=2,
            quarantine_events=1,
            session_id=session_id,
            task_id=task_id,
            attempt_id=attempt_id,
            policy_version_id=policy_version_id,
        )
    )

    result = await OperationalFinancialIntelligenceService(
        rollups=store
    ).analyze(
        access=BackofficeReportAccess(
            can_view_financial=True,
            can_export=False,
            allowed_tenant_ids=frozenset({tenant_id}),
        ),
        query=IntelligenceQuery(
            filters=ReportQuery(
                tenant_id=tenant_id,
                client_id=client_id,
                session_id=session_id,
                task_id=task_id,
                attempt_id=attempt_id,
                policy_version_id=policy_version_id,
            ),
            group_by=(
                IntelligenceDimension.SESSION,
                IntelligenceDimension.TASK,
                IntelligenceDimension.ATTEMPT,
                IntelligenceDimension.POLICY_VERSION,
            ),
        ),
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    assert [value.value for value in row.dimensions] == [
        str(session_id),
        str(task_id),
        str(attempt_id),
        str(policy_version_id),
    ]
    metrics = row.metrics
    assert metrics.requests == 2
    assert metrics.tasks == 1
    assert metrics.attempts == 3
    assert metrics.peak_concurrent == 4
    assert metrics.maximum_quota_utilization == Decimal("0.75")
    assert metrics.fallbacks == 1
    assert metrics.health_events == 2
    assert metrics.quarantine_events == 1
    assert metrics.throughput_attempts_per_hour == Decimal("3")
