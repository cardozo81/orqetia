from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid7

import pytest

from orqetia.control_plane import (
    ProviderPricingAssignment,
    ProviderPricingCatalogVersion,
)
from orqetia.execution import (
    ExecutionTargetSnapshot,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
)
from orqetia.infrastructure.processes.attempt_accounting import (
    AttemptAccountingObserver,
    provider_usage_to_technical,
)
from orqetia.providers import NativeUsage, ProviderCostMetadata, ProviderUsage
from orqetia.usage_accounting import (
    InMemoryAccountingLedger,
    PricingModel,
    PricingRule,
    TokenRates,
)
from orqetia.control_plane.pricing_catalogs import (
    InMemoryProviderPricingCatalogRepository,
)

NOW = datetime(2026, 10, 6, 17, 0, tzinfo=UTC)


def _attempt(
    *,
    pricing_catalog_version_id=None,
    usage: ProviderUsage | None = None,
) -> ProviderAttempt:
    return ProviderAttempt(
        attempt_id=uuid7(),
        task_id=uuid7(),
        session_id=uuid7(),
        ownership=OwnershipScope(uuid7(), uuid7()),
        operation="TASK_EXECUTION",
        target=ExecutionTargetSnapshot("openai", "gpt-test", "standard"),
        cycle=1,
        attempt_index=1,
        request_reference="payload://attempt/accounting",
        request_fingerprint="a" * 64,
        status=ProviderAttemptStatus.COMPLETED,
        provider_account_id=uuid7(),
        provider_credential_id=uuid7(),
        pricing_catalog_version_id=pricing_catalog_version_id,
        provider_outcome="SUCCESS",
        response_reference="artifact://result/accounting",
        usage=usage or ProviderUsage(input_tokens=10, output_tokens=5),
        cost=ProviderCostMetadata(
            amount=Decimal("0.25"),
            comparison_group="CURRENCY:USD",
            currency="USD",
        ),
        created_at=NOW,
        updated_at=NOW,
        terminal_at=NOW,
    )


def test_provider_usage_lifts_normalized_token_markers() -> None:
    attempt = _attempt(
        usage=ProviderUsage(
            input_tokens=10,
            output_tokens=5,
            native=(
                NativeUsage("cached_input_tokens", Decimal("2"), "tokens"),
                NativeUsage("reasoning_output_tokens", Decimal("3"), "tokens"),
                NativeUsage("provider_total_tokens", Decimal("15"), "tokens"),
            ),
        )
    )
    usage = provider_usage_to_technical(attempt)
    assert usage.input_tokens == 10
    assert usage.cached_input_tokens == 2
    assert usage.output_tokens == 5
    assert usage.reasoning_tokens == 3
    assert usage.provider_total_tokens == 15
    assert usage.native == ()


@pytest.mark.asyncio
async def test_completed_attempt_accounting_is_historical_and_idempotent() -> None:
    pricing = InMemoryProviderPricingCatalogRepository()
    version = ProviderPricingCatalogVersion(
        catalog_version_id=uuid7(),
        version_number=1,
        rules=(
            PricingRule(
                rule_id="openai-test",
                version=1,
                provider_id="openai",
                model_id="gpt-test",
                reasoning_profile="standard",
                pricing_model=PricingModel.TOKEN_STANDARD,
                currency="USD",
                effective_from=NOW,
                token_rates=TokenRates(
                    input_per_million=Decimal("1"),
                    output_per_million=Decimal("2"),
                ),
            ),
        ),
        created_at=NOW,
    )
    await pricing.publish_and_activate(
        version=version,
        assignment=ProviderPricingAssignment(
            catalog_version_id=version.catalog_version_id,
            assignment_version=1,
            assigned_at=NOW,
        ),
        expected_assignment_version=None,
    )

    ledger = InMemoryAccountingLedger()
    observer = AttemptAccountingObserver(
        ledger=ledger,
        pricing_versions=pricing,
    )
    attempt = _attempt(
        pricing_catalog_version_id=version.catalog_version_id,
        usage=ProviderUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        ),
    )

    await observer.record(attempt)
    await observer.record(attempt)

    entry = await ledger.get(attempt_id=attempt.attempt_id)
    assert entry is not None
    assert entry.estimated_cost.amount == Decimal("3")
    assert entry.estimated_cost.currency == "USD"
    assert entry.observed_cost is not None
    assert entry.observed_cost.amount == Decimal("0.25")
    assert entry.dimensions.provider_account_id == attempt.provider_account_id
    assert entry.dimensions.provider_credential_id == attempt.provider_credential_id
