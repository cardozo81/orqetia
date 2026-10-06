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
        model_id=model,
        status=status,
        error_class=error_class,
        attempts=attempts,
        input_tokens=100 * attempts,
        cached_input_tokens=10 * attempts,
        output_tokens=20 * attempts,
        reasoning_tokens=5 * attempts,
        total_tokens=120 * attempts,
        latency_ms_total=latency,
        cycles=attempts,
        retries=retries,
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
