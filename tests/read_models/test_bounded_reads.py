from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

import pytest

from orqetia.read_models import (
    CLIENT_USAGE_MAX_SOURCE_ROWS,
    BackofficeReportAccess,
    BackofficeReportingService,
    BoundedReadExceeded,
    ClientUsageReportService,
    InMemoryReportExportAuditSink,
    InMemoryReportRollupStore,
    ReportQuery,
    ReportRollup,
)
from orqetia.usage_accounting import NativeUsageQuantity

NOW = datetime(2026, 10, 6, 22, 30, tzinfo=UTC)


class _OverScanStore:
    async def query(self, *, filters, limit):
        del filters
        assert limit == CLIENT_USAGE_MAX_SOURCE_ROWS + 1
        return tuple(None for _ in range(limit))


@pytest.mark.asyncio
async def test_usage_fails_closed_before_unbounded_source_materialization() -> None:
    service = ClientUsageReportService(_OverScanStore())
    with pytest.raises(BoundedReadExceeded, match="source-row scan"):
        await service.read(
            tenant_id=uuid7(),
            client_id=uuid7(),
            period_from=None,
            period_to=None,
            cursor=None,
            limit=50,
        )


def _rollup(*, tenant_id, client_id, start):
    end = start + timedelta(hours=1)
    return ReportRollup(
        rollup_id=uuid7(),
        period_start=start,
        period_end=end,
        as_of=end,
        tenant_id=tenant_id,
        client_id=client_id,
        provider_id="synthetic",
        model_id="model",
        status="SUCCESS",
        attempts=1,
        input_tokens=1,
        cached_input_tokens=0,
        output_tokens=1,
        reasoning_tokens=0,
        total_tokens=2,
        native_usage=(
            NativeUsageQuantity(
                name="requests",
                unit="REQUEST",
                quantity=Decimal("1"),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_usage_cursor_cannot_be_reused_across_owner() -> None:
    store = InMemoryReportRollupStore()
    tenant_a, client_a = uuid7(), uuid7()
    tenant_b, client_b = uuid7(), uuid7()
    await store.put(_rollup(tenant_id=tenant_a, client_id=client_a, start=NOW))
    await store.put(
        _rollup(
            tenant_id=tenant_a,
            client_id=client_a,
            start=NOW + timedelta(hours=1),
        )
    )
    await store.put(_rollup(tenant_id=tenant_b, client_id=client_b, start=NOW))
    service = ClientUsageReportService(store)
    first = await service.read(
        tenant_id=tenant_a,
        client_id=client_a,
        period_from=None,
        period_to=None,
        cursor=None,
        limit=1,
    )
    assert first.next_cursor is not None

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        await service.read(
            tenant_id=tenant_b,
            client_id=client_b,
            period_from=None,
            period_to=None,
            cursor=first.next_cursor,
            limit=1,
        )


@pytest.mark.asyncio
async def test_backoffice_read_and_export_are_bounded() -> None:
    service = BackofficeReportingService(
        store=InMemoryReportRollupStore(),
        export_audit=InMemoryReportExportAuditSink(),
    )
    access = BackofficeReportAccess(
        can_view_financial=True,
        can_export=True,
        allowed_tenant_ids=None,
    )
    with pytest.raises(BoundedReadExceeded, match="page limit"):
        await service.read(access=access, filters=ReportQuery(), limit=201)
    with pytest.raises(BoundedReadExceeded, match="requires period_from"):
        await service.export(
            access=access,
            filters=ReportQuery(),
            occurred_at=NOW,
        )
    with pytest.raises(BoundedReadExceeded, match="time range"):
        ReportQuery(
            period_from=NOW - timedelta(days=367),
            period_to=NOW,
        )
