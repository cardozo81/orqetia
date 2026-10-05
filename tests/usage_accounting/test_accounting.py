from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from orqetia.usage_accounting import (
    AccountingDimensions,
    AccountingEntry,
    AccountingService,
    ContextTier,
    CostBasis,
    InMemoryAccountingLedger,
    InternalCostSnapshot,
    MonetaryAmount,
    NativeUsageQuantity,
    PricingCatalog,
    PricingModel,
    PricingResolver,
    PricingRule,
    ReasoningBillingMode,
    TechnicalUsage,
    TimeWindow,
    TokenRates,
    aggregate_costs,
)

NOW = datetime(2026, 10, 5, 20, tzinfo=UTC)


def _rule(
    *,
    rule_id: str = "openai-gpt",
    version: int = 1,
    model: PricingModel = PricingModel.TOKEN_STANDARD,
    currency: str = "USD",
    token_rates: TokenRates | None = None,
    context_tiers: tuple[ContextTier, ...] = (),
    time_windows: tuple[TimeWindow, ...] = (),
    request_rate: Decimal | None = None,
    native_unit: str | None = None,
    native_rate: Decimal | None = None,
) -> PricingRule:
    return PricingRule(
        rule_id=rule_id,
        version=version,
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        pricing_model=model,
        currency=currency,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        token_rates=token_rates,
        context_tiers=context_tiers,
        time_windows=time_windows,
        request_rate=request_rate,
        native_unit=native_unit,
        native_rate=native_rate,
        source_reference="fixture://pricing",
    )


def _resolver(*rules: PricingRule) -> PricingResolver:
    return PricingResolver(PricingCatalog(tuple(rules)))


def _dimensions(*, attempt_id: object | None = None) -> AccountingDimensions:
    return AccountingDimensions(
        tenant_id=uuid4(),
        client_id=uuid4(),
        attempt_id=uuid4() if attempt_id is None else attempt_id,
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
    )


def test_provider_total_wins_and_reasoning_is_not_double_counted() -> None:
    usage = TechnicalUsage(
        input_tokens=100,
        output_tokens=50,
        reasoning_tokens=40,
        provider_total_tokens=151,
    )
    assert usage.total_tokens == 151
    assert (
        TechnicalUsage(input_tokens=100, output_tokens=50, reasoning_tokens=40).total_tokens
        == 150
    )


def test_token_pricing_cache_split_is_fail_closed() -> None:
    resolver = _resolver(
        _rule(
            token_rates=TokenRates(
                input_per_million=Decimal("1"),
                cached_input_per_million=Decimal("0.5"),
                output_per_million=Decimal("2"),
            )
        )
    )
    quote = resolver.quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(input_tokens=1000, output_tokens=100),
        occurred_at=NOW,
    )
    assert quote.cost.basis is CostBasis.UNPRICED
    assert quote.cost.unpriced_reason == "CACHE_SPLIT_REQUIRED"


def test_token_pricing_reasoning_modes_are_catalog_controlled() -> None:
    included = _resolver(
        _rule(
            token_rates=TokenRates(
                input_per_million=Decimal("0"),
                output_per_million=Decimal("10"),
                reasoning_billing_mode=ReasoningBillingMode.INCLUDED_IN_OUTPUT,
            )
        )
    ).quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(input_tokens=0, output_tokens=50, reasoning_tokens=40),
        occurred_at=NOW,
    )
    added = _resolver(
        _rule(
            token_rates=TokenRates(
                input_per_million=Decimal("0"),
                output_per_million=Decimal("10"),
                reasoning_billing_mode=ReasoningBillingMode.ADD_REASONING_TO_OUTPUT,
            )
        )
    ).quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(input_tokens=0, output_tokens=50, reasoning_tokens=40),
        occurred_at=NOW,
    )
    assert included.cost.amount == Decimal("0.0005")
    assert added.cost.amount == Decimal("0.0009")


def test_mixed_native_and_token_usage_is_not_silently_priced() -> None:
    resolver = _resolver(
        _rule(
            token_rates=TokenRates(
                input_per_million=Decimal("1"),
                output_per_million=Decimal("2"),
            )
        )
    )
    quote = resolver.quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(
            input_tokens=10,
            output_tokens=5,
            native=(NativeUsageQuantity("credits", Decimal("2"), "CREDIT"),),
        ),
        occurred_at=NOW,
    )
    assert quote.cost.basis is CostBasis.UNPRICED
    assert quote.cost.unpriced_reason == "MIXED_NATIVE_AND_TOKEN_USAGE"


def test_context_time_request_and_credit_models_resolve() -> None:
    low = TokenRates(Decimal("1"), Decimal("2"))
    high = TokenRates(Decimal("3"), Decimal("4"))
    context = _resolver(
        _rule(
            model=PricingModel.TOKEN_CONTEXT_TIERED,
            context_tiers=(ContextTier(1000, low), ContextTier(None, high)),
        )
    ).quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(input_tokens=2000, output_tokens=0),
        occurred_at=NOW,
    )
    assert context.cost.amount == Decimal("0.006")

    time_quote = _resolver(
        _rule(
            model=PricingModel.TOKEN_TIME_WINDOW,
            time_windows=(
                TimeWindow(0, 12, low),
                TimeWindow(12, 23, high),
                TimeWindow(23, 0, low),
            ),
        )
    ).quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(input_tokens=1000, output_tokens=0),
        occurred_at=NOW,
    )
    assert time_quote.cost.amount == Decimal("0.003")

    request_quote = _resolver(
        _rule(model=PricingModel.PER_REQUEST, request_rate=Decimal("0.2"))
    ).quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(request_units=Decimal("3")),
        occurred_at=NOW,
    )
    assert request_quote.cost.amount == Decimal("0.6")

    credit_quote = _resolver(
        _rule(
            model=PricingModel.PROVIDER_CREDITS,
            native_unit="CREDIT",
            native_rate=Decimal("0.01"),
        )
    ).quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(
            native=(NativeUsageQuantity("credits", Decimal("7"), "CREDIT"),)
        ),
        occurred_at=NOW,
    )
    assert credit_quote.cost.amount == Decimal("0.07")

    zero_credit_quote = _resolver(
        _rule(
            model=PricingModel.PROVIDER_CREDITS,
            native_unit="CREDIT",
            native_rate=Decimal("0.01"),
        )
    ).quote(
        provider_id="openai",
        model_id="gpt-x",
        reasoning_profile="medium",
        usage=TechnicalUsage(
            native=(NativeUsageQuantity("credits", Decimal("0"), "CREDIT"),)
        ),
        occurred_at=NOW,
    )
    assert zero_credit_quote.cost.amount == Decimal("0")
    assert zero_credit_quote.cost.basis is CostBasis.USAGE_DERIVED_ESTIMATE


@pytest.mark.asyncio
async def test_ledger_is_idempotent_and_immutable() -> None:
    ledger = InMemoryAccountingLedger()
    resolver = _resolver(
        _rule(token_rates=TokenRates(Decimal("1"), Decimal("2")))
    )
    service = AccountingService(pricing=resolver, ledger=ledger)
    attempt_id = uuid4()
    dimensions = _dimensions(attempt_id=attempt_id)
    usage = TechnicalUsage(input_tokens=1000, output_tokens=1000)

    first = await service.record(dimensions=dimensions, usage=usage, recorded_at=NOW)
    second = AccountingEntry(
        entry_id=uuid4(),
        dimensions=dimensions,
        usage=usage,
        estimated_cost=first.estimated_cost,
        observed_cost=None,
        recorded_at=NOW,
    )
    assert await ledger.append(second) == first

    changed = AccountingEntry(
        entry_id=uuid4(),
        dimensions=dimensions,
        usage=TechnicalUsage(input_tokens=1, output_tokens=1),
        estimated_cost=first.estimated_cost,
        observed_cost=None,
        recorded_at=NOW,
    )
    with pytest.raises(ValueError, match="immutable"):
        await ledger.append(changed)


@pytest.mark.asyncio
async def test_observed_cost_wins_and_currencies_never_combine() -> None:
    unpriced = InternalCostSnapshot(
        basis=CostBasis.UNPRICED,
        amount=None,
        currency=None,
        unpriced_reason="NO_PRICING_RULE",
    )
    dimensions_a = _dimensions()
    dimensions_b = _dimensions()
    dimensions_c = _dimensions()
    entries = (
        AccountingEntry(
            entry_id=uuid4(),
            dimensions=dimensions_a,
            usage=TechnicalUsage(input_tokens=1),
            estimated_cost=InternalCostSnapshot(
                basis=CostBasis.USAGE_DERIVED_ESTIMATE,
                amount=Decimal("1"),
                currency="USD",
            ),
            observed_cost=InternalCostSnapshot(
                basis=CostBasis.PROVIDER_OBSERVED,
                amount=Decimal("2.5"),
                currency="EUR",
            ),
            recorded_at=NOW,
        ),
        AccountingEntry(
            entry_id=uuid4(),
            dimensions=dimensions_b,
            usage=TechnicalUsage(input_tokens=1),
            estimated_cost=InternalCostSnapshot(
                basis=CostBasis.USAGE_DERIVED_ESTIMATE,
                amount=Decimal("3"),
                currency="USD",
            ),
            observed_cost=None,
            recorded_at=NOW,
        ),
        AccountingEntry(
            entry_id=uuid4(),
            dimensions=dimensions_c,
            usage=TechnicalUsage(input_tokens=10),
            estimated_cost=unpriced,
            observed_cost=None,
            recorded_at=NOW,
        ),
    )
    totals = aggregate_costs(entries)
    assert tuple((item.currency, item.amount) for item in totals.totals) == (
        ("EUR", Decimal("2.5")),
        ("USD", Decimal("3")),
    )
    assert totals.unpriced_entries == 1


@pytest.mark.asyncio
async def test_service_snapshots_pricing_version_and_observed_cost() -> None:
    old = _rule(
        version=1,
        token_rates=TokenRates(Decimal("1"), Decimal("1")),
    )
    new = _rule(
        version=2,
        token_rates=TokenRates(Decimal("2"), Decimal("2")),
    )
    service = AccountingService(
        pricing=_resolver(old, new),
        ledger=InMemoryAccountingLedger(),
    )
    entry = await service.record(
        dimensions=_dimensions(),
        usage=TechnicalUsage(input_tokens=1_000_000, output_tokens=0),
        observed_cost=MonetaryAmount(Decimal("1.75"), "eur"),
        observed_source_reference="provider://invoice-line",
        recorded_at=NOW,
    )
    assert entry.estimated_cost.pricing_rule_version == 2
    assert entry.estimated_cost.amount == Decimal("2")
    assert entry.effective_cost.amount == Decimal("1.75")
    assert entry.effective_cost.currency == "EUR"


def test_client_usage_projection_has_no_financial_fields() -> None:
    payload = asdict(
        TechnicalUsage(
            input_tokens=10,
            output_tokens=5,
            native=(NativeUsageQuantity("request", Decimal("1"), "REQUEST"),),
        ).client_view()
    )
    forbidden = {"cost", "currency", "price", "pricing", "charge"}
    assert all(not any(token in key.lower() for token in forbidden) for key in payload)
