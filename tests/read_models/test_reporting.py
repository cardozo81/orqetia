from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

import pytest

from orqetia.read_models import (
    BackofficeReportAccess,
    BackofficeReportingService,
    ClientUsageReportService,
    InMemoryReportExportAuditSink,
    InMemoryReportRollupStore,
    ReportQuery,
    ReportRollup,
)
from orqetia.usage_accounting import NativeUsageQuantity

START = datetime(2026, 10, 5, 20, tzinfo=UTC)
END = START + timedelta(hours=1)
AS_OF = END + timedelta(minutes=1)


def _rollup(
    *,
    tenant_id,
    client_id,
    provider_id: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    estimated_cost: str | None,
    estimated_currency: str | None,
    observed_cost: str | None = None,
    observed_currency: str | None = None,
    provider_account_id=None,
    provider_credential_id=None,
    client_credential_id=None,
    native_quantity: str = "0",
    unpriced_attempts: int = 0,
) -> ReportRollup:
    return ReportRollup(
        rollup_id=uuid7(),
        period_start=START,
        period_end=END,
        as_of=AS_OF,
        tenant_id=tenant_id,
        client_id=client_id,
        client_credential_id=client_credential_id,
        provider_id=provider_id,
        provider_account_id=provider_account_id,
        provider_credential_id=provider_credential_id,
        model_id=model_id,
        status="SUCCESS",
        attempts=1,
        input_tokens=input_tokens,
        cached_input_tokens=0,
        output_tokens=output_tokens,
        reasoning_tokens=5,
        total_tokens=total_tokens,
        native_usage=(
            NativeUsageQuantity(
                name="requests",
                unit="REQUEST",
                quantity=Decimal(native_quantity),
            ),
        ),
        latency_ms_total=100,
        cycles=1,
        retries=0,
        estimated_cost=(
            None if estimated_cost is None else Decimal(estimated_cost)
        ),
        estimated_currency=estimated_currency,
        observed_cost=(
            None if observed_cost is None else Decimal(observed_cost)
        ),
        observed_currency=observed_currency,
        unpriced_attempts=unpriced_attempts,
    )


@pytest.mark.asyncio
async def test_client_usage_aggregates_only_owned_technical_data() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    other_tenant, other_client = uuid7(), uuid7()
    store = InMemoryReportRollupStore()
    account_id, provider_credential_id = uuid7(), uuid7()
    await store.put(
        _rollup(
            tenant_id=tenant_id,
            client_id=client_id,
            provider_id="openai",
            model_id="gpt-x",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            estimated_cost="1.25",
            estimated_currency="USD",
            provider_account_id=account_id,
            provider_credential_id=provider_credential_id,
            native_quantity="1",
        )
    )
    await store.put(
        _rollup(
            tenant_id=tenant_id,
            client_id=client_id,
            provider_id="anthropic",
            model_id="claude-x",
            input_tokens=50,
            output_tokens=10,
            total_tokens=60,
            estimated_cost="2.50",
            estimated_currency="EUR",
            native_quantity="2",
        )
    )
    await store.put(
        _rollup(
            tenant_id=other_tenant,
            client_id=other_client,
            provider_id="openai",
            model_id="gpt-x",
            input_tokens=999,
            output_tokens=999,
            total_tokens=1998,
            estimated_cost="99",
            estimated_currency="USD",
        )
    )

    page = await ClientUsageReportService(store).read(
        tenant_id=tenant_id,
        client_id=client_id,
        period_from=None,
        period_to=None,
        cursor=None,
        limit=50,
    )
    assert len(page.items) == 1
    item = page.items[0]
    assert item.input_tokens == 150
    assert item.output_tokens == 30
    assert item.total_tokens == 180
    assert item.reasoning_tokens == 10
    assert item.native_usage[0].quantity == Decimal("3")

    payload = page.client_payload()
    serialized = str(payload).lower()
    for forbidden in (
        "cost",
        "currency",
        "provider_account",
        "provider_credential",
        "client_credential",
        "pricing",
        "charge",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_backoffice_report_preserves_currency_and_provenance() -> None:
    tenant_id, client_id = uuid7(), uuid7()
    client_credential_id = uuid7()
    account_id, provider_credential_id = uuid7(), uuid7()
    store = InMemoryReportRollupStore()
    await store.put(
        _rollup(
            tenant_id=tenant_id,
            client_id=client_id,
            provider_id="openai",
            model_id="gpt-x",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            estimated_cost="1",
            estimated_currency="USD",
            observed_cost="0.8",
            observed_currency="USD",
            provider_account_id=account_id,
            provider_credential_id=provider_credential_id,
            client_credential_id=client_credential_id,
        )
    )
    audit = InMemoryReportExportAuditSink()
    service = BackofficeReportingService(store=store, export_audit=audit)
    access = BackofficeReportAccess(
        can_view_financial=True,
        can_export=True,
        allowed_tenant_ids=frozenset({tenant_id}),
    )
    filters = ReportQuery(
        period_from=START,
        period_to=END + timedelta(minutes=1),
        tenant_id=tenant_id,
        client_id=client_id,
        provider_account_id=account_id,
        provider_credential_id=provider_credential_id,
    )
    page = await service.read(access=access, filters=filters)
    assert len(page.rows) == 1
    payload = page.rows[0].backoffice_payload()
    assert payload["estimated_currency"] == "USD"
    assert payload["observed_cost"] == "0.8"
    assert payload["provider_account_id"] == str(account_id)
    assert payload["provider_credential_id"] == str(provider_credential_id)
    assert payload["client_credential_id"] == str(client_credential_id)

    exported = await service.export(
        access=access,
        filters=filters,
        occurred_at=AS_OF,
    )
    assert len(exported) == 1
    assert len(audit.events) == 1
    assert audit.events[0].row_count == 1


@pytest.mark.asyncio
async def test_backoffice_filters_cannot_bypass_authorized_tenants() -> None:
    tenant_id = uuid7()
    store = InMemoryReportRollupStore()
    service = BackofficeReportingService(
        store=store,
        export_audit=InMemoryReportExportAuditSink(),
    )
    access = BackofficeReportAccess(
        can_view_financial=True,
        can_export=False,
        allowed_tenant_ids=frozenset({tenant_id}),
    )

    with pytest.raises(PermissionError, match="tenant filter is required"):
        await service.read(access=access, filters=ReportQuery())

    with pytest.raises(PermissionError, match="outside authorized scope"):
        await service.read(
            access=access,
            filters=ReportQuery(tenant_id=uuid7()),
        )

    with pytest.raises(PermissionError, match="export permission"):
        await service.export(
            access=access,
            filters=ReportQuery(tenant_id=tenant_id),
            occurred_at=AS_OF,
        )


def test_rollup_rejects_credential_provenance_without_account() -> None:
    with pytest.raises(ValueError, match="requires provider account"):
        _rollup(
            tenant_id=uuid7(),
            client_id=uuid7(),
            provider_id="openai",
            model_id="gpt-x",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            estimated_cost=None,
            estimated_currency=None,
            provider_credential_id=uuid7(),
        )
