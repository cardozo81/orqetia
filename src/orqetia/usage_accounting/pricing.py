"""Versioned technical provider-pricing catalog and fail-closed resolver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .domain import (
    CostBasis,
    InternalCostSnapshot,
    PricingModel,
    ReasoningBillingMode,
    TechnicalUsage,
)

_MILLION = Decimal("1000000")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _rate(value: Decimal | None, field: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field} cannot be negative")


@dataclass(frozen=True)
class TokenRates:
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None
    reasoning_per_million: Decimal | None = None
    reasoning_billing_mode: ReasoningBillingMode = ReasoningBillingMode.INCLUDED_IN_OUTPUT

    def __post_init__(self) -> None:
        for field, value in (
            ("input_per_million", self.input_per_million),
            ("output_per_million", self.output_per_million),
            ("cached_input_per_million", self.cached_input_per_million),
            ("reasoning_per_million", self.reasoning_per_million),
        ):
            _rate(value, field)
        if (
            self.reasoning_billing_mode is ReasoningBillingMode.SEPARATE_RATE
            and self.reasoning_per_million is None
        ):
            raise ValueError("SEPARATE_RATE requires reasoning_per_million")

    @property
    def effective_cached_input_rate(self) -> Decimal:
        return (
            self.input_per_million
            if self.cached_input_per_million is None
            else self.cached_input_per_million
        )


@dataclass(frozen=True)
class ContextTier:
    max_input_tokens: int | None
    rates: TokenRates

    def __post_init__(self) -> None:
        if self.max_input_tokens is not None and self.max_input_tokens < 0:
            raise ValueError("max_input_tokens cannot be negative")


@dataclass(frozen=True)
class TimeWindow:
    start_hour_utc: int
    end_hour_utc: int
    rates: TokenRates

    def __post_init__(self) -> None:
        if not 0 <= self.start_hour_utc <= 23 or not 0 <= self.end_hour_utc <= 23:
            raise ValueError("time-window hours must be between 0 and 23")
        if self.start_hour_utc == self.end_hour_utc:
            raise ValueError("time window must not have identical start/end hours")

    def contains(self, hour: int) -> bool:
        if self.start_hour_utc < self.end_hour_utc:
            return self.start_hour_utc <= hour < self.end_hour_utc
        return hour >= self.start_hour_utc or hour < self.end_hour_utc


@dataclass(frozen=True)
class PricingRule:
    rule_id: str
    version: int
    provider_id: str
    model_id: str
    pricing_model: PricingModel
    currency: str
    effective_from: datetime
    effective_to: datetime | None = None
    reasoning_profile: str | None = None
    token_rates: TokenRates | None = None
    context_tiers: tuple[ContextTier, ...] = ()
    time_windows: tuple[TimeWindow, ...] = ()
    request_rate: Decimal | None = None
    native_unit: str | None = None
    native_rate: Decimal | None = None
    source_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or len(self.rule_id) > 200:
            raise ValueError("rule_id must contain 1..200 characters")
        if self.version < 1:
            raise ValueError("version must be positive")
        for field, value, maximum in (
            ("provider_id", self.provider_id, 100),
            ("model_id", self.model_id, 200),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must contain 1..{maximum} characters")
        if self.reasoning_profile is not None and (
            not self.reasoning_profile.strip() or len(self.reasoning_profile) > 100
        ):
            raise ValueError("reasoning_profile must contain 1..100 characters when present")
        normalized_currency = self.currency.strip().upper()
        if not normalized_currency or len(normalized_currency) > 12:
            raise ValueError("currency must contain 1..12 characters")
        object.__setattr__(self, "currency", normalized_currency)
        _require_aware(self.effective_from, "effective_from")
        if self.effective_to is not None:
            _require_aware(self.effective_to, "effective_to")
            if self.effective_to <= self.effective_from:
                raise ValueError("effective_to must be after effective_from")
        _rate(self.request_rate, "request_rate")
        _rate(self.native_rate, "native_rate")
        if self.source_reference is not None and (
            not self.source_reference.strip() or len(self.source_reference) > 500
        ):
            raise ValueError("source_reference must contain 1..500 characters when present")

        if self.pricing_model is PricingModel.TOKEN_STANDARD:
            if self.token_rates is None:
                raise ValueError("TOKEN_STANDARD requires token_rates")
        elif self.pricing_model is PricingModel.TOKEN_CONTEXT_TIERED:
            if not self.context_tiers:
                raise ValueError("TOKEN_CONTEXT_TIERED requires context_tiers")
            bounded = [item.max_input_tokens for item in self.context_tiers]
            if bounded.count(None) > 1:
                raise ValueError("context tiers allow at most one unbounded tier")
            concrete = [item for item in bounded if item is not None]
            if concrete != sorted(concrete) or len(concrete) != len(set(concrete)):
                raise ValueError("context tier bounds must be unique and increasing")
            if None in bounded and bounded[-1] is not None:
                raise ValueError("unbounded context tier must be last")
        elif self.pricing_model is PricingModel.TOKEN_TIME_WINDOW:
            if not self.time_windows:
                raise ValueError("TOKEN_TIME_WINDOW requires time_windows")
        elif self.pricing_model is PricingModel.PER_REQUEST:
            if self.request_rate is None:
                raise ValueError("PER_REQUEST requires request_rate")
        elif self.pricing_model is PricingModel.PROVIDER_CREDITS and (
            self.native_unit is None
            or not self.native_unit.strip()
            or len(self.native_unit) > 100
            or self.native_rate is None
        ):
            raise ValueError("PROVIDER_CREDITS requires native_unit and native_rate")

    @property
    def reference(self) -> str:
        return f"{self.rule_id}@{self.version}"

    def active_at(self, occurred_at: datetime) -> bool:
        _require_aware(occurred_at, "occurred_at")
        return self.effective_from <= occurred_at and (
            self.effective_to is None or occurred_at < self.effective_to
        )

    def matches(
        self,
        *,
        provider_id: str,
        model_id: str,
        reasoning_profile: str,
        occurred_at: datetime,
    ) -> bool:
        return (
            self.provider_id == provider_id
            and self.model_id == model_id
            and (
                self.reasoning_profile is None
                or self.reasoning_profile == reasoning_profile
            )
            and self.active_at(occurred_at)
        )


@dataclass(frozen=True)
class PricingCatalog:
    rules: tuple[PricingRule, ...]

    def __post_init__(self) -> None:
        identities = [(rule.rule_id, rule.version) for rule in self.rules]
        if len(identities) != len(set(identities)):
            raise ValueError("pricing rule id/version pairs must be unique")

    def resolve(
        self,
        *,
        provider_id: str,
        model_id: str,
        reasoning_profile: str,
        occurred_at: datetime,
    ) -> PricingRule | None:
        matches = [
            rule
            for rule in self.rules
            if rule.matches(
                provider_id=provider_id,
                model_id=model_id,
                reasoning_profile=reasoning_profile,
                occurred_at=occurred_at,
            )
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda rule: (
                rule.version,
                rule.effective_from,
                1 if rule.reasoning_profile is not None else 0,
            ),
            reverse=True,
        )
        return matches[0]


@dataclass(frozen=True)
class PricingQuote:
    cost: InternalCostSnapshot
    comparison_group: str

    @property
    def priced(self) -> bool:
        return self.cost.amount is not None


class PricingResolver:
    def __init__(self, catalog: PricingCatalog) -> None:
        self._catalog = catalog

    def quote(
        self,
        *,
        provider_id: str,
        model_id: str,
        reasoning_profile: str,
        usage: TechnicalUsage,
        occurred_at: datetime,
    ) -> PricingQuote:
        rule = self._catalog.resolve(
            provider_id=provider_id,
            model_id=model_id,
            reasoning_profile=reasoning_profile,
            occurred_at=occurred_at,
        )
        if rule is None:
            return self._unpriced(None, "NO_PRICING_RULE")

        amount: Decimal | None
        reason: str | None
        if rule.pricing_model is PricingModel.TOKEN_STANDARD:
            assert rule.token_rates is not None
            amount, reason = self._price_tokens(usage, rule.token_rates)
        elif rule.pricing_model is PricingModel.TOKEN_CONTEXT_TIERED:
            rates = self._context_rates(rule, usage)
            if rates is None:
                amount, reason = None, "CONTEXT_TIER_UNRESOLVED"
            else:
                amount, reason = self._price_tokens(usage, rates)
        elif rule.pricing_model is PricingModel.TOKEN_TIME_WINDOW:
            rates = next(
                (window.rates for window in rule.time_windows if window.contains(occurred_at.hour)),
                None,
            )
            if rates is None:
                amount, reason = None, "TIME_WINDOW_UNRESOLVED"
            else:
                amount, reason = self._price_tokens(usage, rates)
        elif rule.pricing_model is PricingModel.PER_REQUEST:
            assert rule.request_rate is not None
            units = usage.request_units if usage.request_units is not None else Decimal("1")
            amount, reason = units * rule.request_rate, None
        else:
            assert rule.native_unit is not None
            assert rule.native_rate is not None
            native_items = tuple(
                item for item in usage.native if item.unit == rule.native_unit
            )
            if not native_items:
                amount, reason = None, "NATIVE_USAGE_MISSING"
            elif any(
                value is not None
                for value in (usage.input_tokens, usage.output_tokens, usage.reasoning_tokens)
            ):
                amount, reason = None, "MIXED_NATIVE_AND_TOKEN_USAGE"
            else:
                native = sum((item.quantity for item in native_items), Decimal("0"))
                amount, reason = native * rule.native_rate, None

        if amount is None:
            return self._unpriced(rule, reason or "UNPRICEABLE_USAGE")
        snapshot = InternalCostSnapshot(
            basis=CostBasis.USAGE_DERIVED_ESTIMATE,
            amount=amount,
            currency=rule.currency,
            pricing_model=rule.pricing_model,
            pricing_rule_id=rule.rule_id,
            pricing_rule_version=rule.version,
            pricing_reference=rule.reference,
            source_reference=rule.source_reference,
        )
        return PricingQuote(snapshot, f"CURRENCY:{rule.currency}")

    @staticmethod
    def _context_rates(rule: PricingRule, usage: TechnicalUsage) -> TokenRates | None:
        if usage.input_tokens is None:
            return None
        for tier in rule.context_tiers:
            if tier.max_input_tokens is None or usage.input_tokens <= tier.max_input_tokens:
                return tier.rates
        return None

    @staticmethod
    def _price_tokens(
        usage: TechnicalUsage,
        rates: TokenRates,
    ) -> tuple[Decimal | None, str | None]:
        if usage.native:
            return None, "MIXED_NATIVE_AND_TOKEN_USAGE"
        if usage.input_tokens is None or usage.output_tokens is None:
            return None, "TOKEN_USAGE_INCOMPLETE"

        cached_rate = rates.effective_cached_input_rate
        if usage.cached_input_tokens is None:
            if cached_rate != rates.input_per_million:
                return None, "CACHE_SPLIT_REQUIRED"
            cached = 0
        else:
            cached = usage.cached_input_tokens
        uncached = usage.input_tokens - cached

        amount = (
            Decimal(uncached) * rates.input_per_million
            + Decimal(cached) * cached_rate
        ) / _MILLION

        reasoning = usage.reasoning_tokens or 0
        output = usage.output_tokens
        if rates.reasoning_billing_mode is ReasoningBillingMode.ADD_REASONING_TO_OUTPUT:
            amount += Decimal(output + reasoning) * rates.output_per_million / _MILLION
        else:
            amount += Decimal(output) * rates.output_per_million / _MILLION
            if rates.reasoning_billing_mode is ReasoningBillingMode.SEPARATE_RATE:
                assert rates.reasoning_per_million is not None
                amount += Decimal(reasoning) * rates.reasoning_per_million / _MILLION
        return amount, None

    @staticmethod
    def _unpriced(rule: PricingRule | None, reason: str) -> PricingQuote:
        snapshot = InternalCostSnapshot(
            basis=CostBasis.UNPRICED,
            amount=None,
            currency=None,
            pricing_model=None if rule is None else rule.pricing_model,
            pricing_rule_id=None if rule is None else rule.rule_id,
            pricing_rule_version=None if rule is None else rule.version,
            pricing_reference=None if rule is None else rule.reference,
            source_reference=None if rule is None else rule.source_reference,
            unpriced_reason=reason,
        )
        return PricingQuote(snapshot, "UNPRICED")
