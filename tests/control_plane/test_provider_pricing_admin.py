from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from orqetia.control_plane import (
    InMemoryProviderPricingAuditSink,
    InMemoryProviderPricingCatalogRepository,
    ProviderPricingAdminService,
)
from orqetia.control_plane.pricing_catalog_postgres import (
    _rules_from_json,
    _rules_json,
)
from orqetia.usage_accounting import (
    ContextTier,
    NativeUsageQuantity,
    PricingModel,
    PricingRule,
    ReasoningBillingMode,
    TechnicalUsage,
    TimeWindow,
    TokenRates,
)

NOW = datetime(2026, 10, 6, 6, 30, tzinfo=UTC)


def _standard_rule(*, currency: str = "USD", version: int = 1) -> PricingRule:
    return PricingRule(
        rule_id="alpha-standard",
        version=version,
        provider_id="alpha",
        model_id="alpha-1",
        reasoning_profile="standard",
        pricing_model=PricingModel.TOKEN_STANDARD,
        currency=currency,
        effective_from=NOW - timedelta(days=1),
        token_rates=TokenRates(
            input_per_million=Decimal("1.00"),
            output_per_million=Decimal("2.00"),
            cached_input_per_million=Decimal("0.50"),
            reasoning_per_million=Decimal("3.00"),
            reasoning_billing_mode=ReasoningBillingMode.SEPARATE_RATE,
        ),
        source_reference="contract-2026-10",
    )


@pytest.mark.asyncio
async def test_publish_versions_pricing_and_materializes_canonical_resolver() -> None:
    repository = InMemoryProviderPricingCatalogRepository()
    audit = InMemoryProviderPricingAuditSink()
    service = ProviderPricingAdminService(repository=repository, audit=audit)

    first = await service.publish_and_activate(
        rules=(_standard_rule(),),
        occurred_at=NOW,
    )
    second = await service.publish_and_activate(
        rules=(_standard_rule(currency="EUR", version=2),),
        occurred_at=NOW + timedelta(seconds=1),
    )

    assert first.version.version_number == 1
    assert second.version.version_number == 2
    assert second.assignment.assignment_version == 2
    assert [event.action for event in audit.events] == [
        "PRICING_PUBLISHED",
        "PRICING_ACTIVATED",
        "PRICING_PUBLISHED",
        "PRICING_ACTIVATED",
    ]

    quote = second.resolver.quote(
        provider_id="alpha",
        model_id="alpha-1",
        reasoning_profile="standard",
        usage=TechnicalUsage(
            input_tokens=1_000_000,
            cached_input_tokens=200_000,
            output_tokens=100_000,
            reasoning_tokens=50_000,
        ),
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert quote.priced
    assert quote.cost.currency == "EUR"
    assert quote.comparison_group == "CURRENCY:EUR"
    assert quote.cost.amount == Decimal("1.25")


@pytest.mark.asyncio
async def test_unpriced_remains_distinct_from_zero_and_no_fx_is_introduced() -> None:
    service = ProviderPricingAdminService(
        repository=InMemoryProviderPricingCatalogRepository(),
        audit=InMemoryProviderPricingAuditSink(),
    )
    effective = await service.publish_and_activate(
        rules=(
            _standard_rule(currency="USD"),
            PricingRule(
                rule_id="beta-request",
                version=1,
                provider_id="beta",
                model_id="beta-1",
                pricing_model=PricingModel.PER_REQUEST,
                currency="EUR",
                effective_from=NOW - timedelta(days=1),
                request_rate=Decimal("0"),
            ),
        ),
        occurred_at=NOW,
    )

    missing = effective.resolver.quote(
        provider_id="missing",
        model_id="missing",
        reasoning_profile="standard",
        usage=TechnicalUsage(request_units=Decimal("1")),
        occurred_at=NOW,
    )
    assert not missing.priced
    assert missing.cost.amount is None
    assert missing.cost.currency is None
    assert missing.comparison_group == "UNPRICED"

    zero = effective.resolver.quote(
        provider_id="beta",
        model_id="beta-1",
        reasoning_profile="standard",
        usage=TechnicalUsage(request_units=Decimal("2")),
        occurred_at=NOW,
    )
    assert zero.priced
    assert zero.cost.amount == Decimal("0")
    assert zero.cost.currency == "EUR"
    assert zero.comparison_group == "CURRENCY:EUR"


def test_postgres_json_roundtrip_preserves_all_canonical_pricing_models() -> None:
    rules = (
        _standard_rule(),
        PricingRule(
            rule_id="tiered",
            version=1,
            provider_id="alpha",
            model_id="alpha-tiered",
            pricing_model=PricingModel.TOKEN_CONTEXT_TIERED,
            currency="USD",
            effective_from=NOW,
            context_tiers=(
                ContextTier(
                    max_input_tokens=1000,
                    rates=TokenRates(
                        input_per_million=Decimal("1"),
                        output_per_million=Decimal("2"),
                    ),
                ),
                ContextTier(
                    max_input_tokens=None,
                    rates=TokenRates(
                        input_per_million=Decimal("2"),
                        output_per_million=Decimal("4"),
                    ),
                ),
            ),
        ),
        PricingRule(
            rule_id="window",
            version=1,
            provider_id="alpha",
            model_id="alpha-window",
            pricing_model=PricingModel.TOKEN_TIME_WINDOW,
            currency="USD",
            effective_from=NOW,
            time_windows=(
                TimeWindow(
                    start_hour_utc=0,
                    end_hour_utc=12,
                    rates=TokenRates(
                        input_per_million=Decimal("1"),
                        output_per_million=Decimal("1"),
                    ),
                ),
            ),
        ),
        PricingRule(
            rule_id="credits",
            version=1,
            provider_id="gamma",
            model_id="gamma-1",
            pricing_model=PricingModel.PROVIDER_CREDITS,
            currency="CREDIT",
            effective_from=NOW,
            native_unit="CREDIT",
            native_rate=Decimal("0.5"),
        ),
    )
    restored = _rules_from_json(_rules_json(rules))
    assert restored == rules

    credit_quote = ProviderPricingAdminService(
        repository=InMemoryProviderPricingCatalogRepository(),
        audit=InMemoryProviderPricingAuditSink(),
    )
    assert credit_quote is not None


def test_native_credit_pricing_preserves_provider_native_unit() -> None:
    rule = PricingRule(
        rule_id="credits",
        version=1,
        provider_id="gamma",
        model_id="gamma-1",
        pricing_model=PricingModel.PROVIDER_CREDITS,
        currency="CREDIT",
        effective_from=NOW - timedelta(seconds=1),
        native_unit="CREDIT",
        native_rate=Decimal("0.5"),
    )
    from orqetia.usage_accounting import PricingCatalog, PricingResolver

    quote = PricingResolver(PricingCatalog((rule,))).quote(
        provider_id="gamma",
        model_id="gamma-1",
        reasoning_profile="standard",
        usage=TechnicalUsage(
            native=(
                NativeUsageQuantity(
                    name="provider_credit",
                    unit="CREDIT",
                    quantity=Decimal("3"),
                ),
            )
        ),
        occurred_at=NOW,
    )
    assert quote.cost.amount == Decimal("1.5")
    assert quote.cost.currency == "CREDIT"
