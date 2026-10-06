from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid7

import httpx

from orqetia.identity import AuthenticatedPrincipal
from orqetia.infrastructure.http import create_app
from orqetia.read_models import (
    BoundedReadExceeded,
    ClientUsageReportService,
    InMemoryReportRollupStore,
    ReportRollup,
)
from orqetia.usage_accounting import NativeUsageQuantity

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = json.loads(
    (ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json").read_text(
        encoding="utf-8"
    )
)
TENANT_ID = UUID("0199b39a-9bf1-7000-8000-000000000020")
CLIENT_ID = UUID("0199b39a-9bf1-7000-8000-000000000021")
START = datetime(2026, 10, 5, 20, tzinfo=UTC)
END = START + timedelta(hours=1)


class UsageAuthenticator:
    async def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal:
        assert token == "usage"
        return AuthenticatedPrincipal(
            subject_type="SERVICE_CLIENT",
            subject_id="usage",
            tenant_id=str(TENANT_ID),
            client_id=str(CLIENT_ID),
            scopes=frozenset({"usage:read"}),
        )


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.get(
            path,
            headers={"Authorization": "Bearer usage"},
        )


def _rollup(*, tenant_id, client_id, cost: str) -> ReportRollup:
    return ReportRollup(
        rollup_id=uuid7(),
        period_start=START,
        period_end=END,
        as_of=END + timedelta(minutes=1),
        tenant_id=tenant_id,
        client_id=client_id,
        provider_id="openai",
        provider_account_id=uuid7(),
        provider_credential_id=uuid7(),
        model_id="gpt-x",
        status="SUCCESS",
        attempts=1,
        input_tokens=100,
        cached_input_tokens=10,
        output_tokens=20,
        reasoning_tokens=5,
        total_tokens=120,
        native_usage=(
            NativeUsageQuantity(
                name="requests",
                unit="REQUEST",
                quantity=Decimal("1"),
            ),
        ),
        latency_ms_total=100,
        cycles=1,
        retries=0,
        estimated_cost=Decimal(cost),
        estimated_currency="USD",
        unpriced_attempts=0,
    )


def test_usage_endpoint_returns_only_owned_non_financial_rollups() -> None:
    store = InMemoryReportRollupStore()
    asyncio.run(store.put(_rollup(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        cost="1.23",
    )))
    asyncio.run(store.put(_rollup(
        tenant_id=uuid7(),
        client_id=uuid7(),
        cost="99.99",
    )))
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=UsageAuthenticator(),
        client_usage_service=ClientUsageReportService(store),
    )

    response = asyncio.run(_get(app, "/v1/usage?limit=50"))
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["usage"]["input_tokens"] == 100
    assert payload["items"][0]["usage"]["total_tokens"] == 120
    serialized = response.text.lower()
    for forbidden in (
        "cost",
        "currency",
        "provider_account",
        "provider_credential",
        "pricing",
        "charge",
        "99.99",
    ):
        assert forbidden not in serialized


def test_usage_endpoint_fails_closed_without_reporting_service() -> None:
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=UsageAuthenticator(),
    )
    response = asyncio.run(_get(app, "/v1/usage"))
    assert response.status_code == 503
    assert response.json()["code"] == "USAGE_REPORTING_UNAVAILABLE"


class _ExceededUsageService:
    async def read(self, **_kwargs):
        raise BoundedReadExceeded("synthetic bounded read overflow")


def test_usage_endpoint_returns_413_when_source_scan_is_too_large() -> None:
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=UsageAuthenticator(),
        client_usage_service=_ExceededUsageService(),
    )
    response = asyncio.run(_get(app, "/v1/usage?limit=50"))
    assert response.status_code == 413
    assert response.json()["code"] == "READ_LIMIT_EXCEEDED"
