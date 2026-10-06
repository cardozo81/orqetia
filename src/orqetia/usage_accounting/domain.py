"""Canonical technical usage and internal provider-cost domain model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _non_negative(value: int | Decimal | None, field: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field} cannot be negative")


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized or len(normalized) > 12:
        raise ValueError("currency must contain 1..12 characters")
    return normalized


class PricingModel(StrEnum):
    TOKEN_STANDARD = "TOKEN_STANDARD"
    TOKEN_CONTEXT_TIERED = "TOKEN_CONTEXT_TIERED"
    TOKEN_TIME_WINDOW = "TOKEN_TIME_WINDOW"
    PER_REQUEST = "PER_REQUEST"
    PROVIDER_CREDITS = "PROVIDER_CREDITS"


class ReasoningBillingMode(StrEnum):
    INCLUDED_IN_OUTPUT = "INCLUDED_IN_OUTPUT"
    ADD_REASONING_TO_OUTPUT = "ADD_REASONING_TO_OUTPUT"
    SEPARATE_RATE = "SEPARATE_RATE"


class CostBasis(StrEnum):
    USAGE_DERIVED_ESTIMATE = "USAGE_DERIVED_ESTIMATE"
    PROVIDER_OBSERVED = "PROVIDER_OBSERVED"
    UNPRICED = "UNPRICED"


@dataclass(frozen=True)
class NativeUsageQuantity:
    name: str
    quantity: Decimal
    unit: str

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 100:
            raise ValueError("native usage name must contain 1..100 characters")
        _non_negative(self.quantity, "native usage quantity")
        if not self.unit.strip() or len(self.unit) > 100:
            raise ValueError("native usage unit must contain 1..100 characters")


@dataclass(frozen=True)
class TechnicalUsage:
    """Provider-neutral usage snapshot. Monetary values deliberately do not belong here."""

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    provider_total_tokens: int | None = None
    request_units: Decimal | None = None
    native: tuple[NativeUsageQuantity, ...] = ()

    def __post_init__(self) -> None:
        for field, value in (
            ("input_tokens", self.input_tokens),
            ("cached_input_tokens", self.cached_input_tokens),
            ("output_tokens", self.output_tokens),
            ("reasoning_tokens", self.reasoning_tokens),
            ("provider_total_tokens", self.provider_total_tokens),
        ):
            _non_negative(value, field)
        _non_negative(self.request_units, "request_units")
        if (
            self.cached_input_tokens is not None
            and self.input_tokens is not None
            and self.cached_input_tokens > self.input_tokens
        ):
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        identities = [(item.name, item.unit) for item in self.native]
        if len(identities) != len(set(identities)):
            raise ValueError("native usage components must be unique by name and unit")

    @property
    def total_tokens(self) -> int | None:
        """Provider total wins; reasoning is never double-counted into canonical total."""

        if self.provider_total_tokens is not None:
            return self.provider_total_tokens
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    @property
    def has_usage(self) -> bool:
        return any(
            value is not None
            for value in (
                self.input_tokens,
                self.cached_input_tokens,
                self.output_tokens,
                self.reasoning_tokens,
                self.provider_total_tokens,
                self.request_units,
            )
        ) or bool(self.native)

    def client_view(self) -> ClientTechnicalUsage:
        return ClientTechnicalUsage(
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            total_tokens=self.total_tokens,
            request_units=self.request_units,
            native=self.native,
        )


@dataclass(frozen=True)
class ClientTechnicalUsage:
    """Safe client-facing usage projection. No monetary fields exist by construction."""

    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    request_units: Decimal | None
    native: tuple[NativeUsageQuantity, ...]


@dataclass(frozen=True)
class MonetaryAmount:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        _non_negative(self.amount, "monetary amount")
        object.__setattr__(self, "currency", _currency(self.currency))


@dataclass(frozen=True)
class InternalCostSnapshot:
    """Immutable internal provider-cost snapshot for one attempt fragment."""

    basis: CostBasis
    amount: Decimal | None
    currency: str | None
    pricing_model: PricingModel | None = None
    pricing_rule_id: str | None = None
    pricing_rule_version: int | None = None
    pricing_reference: str | None = None
    source_reference: str | None = None
    unpriced_reason: str | None = None

    def __post_init__(self) -> None:
        _non_negative(self.amount, "internal cost amount")
        if self.amount is None:
            if self.basis is not CostBasis.UNPRICED:
                raise ValueError("missing amount requires UNPRICED basis")
            if self.currency is not None:
                raise ValueError("UNPRICED cost cannot carry currency")
            if not self.unpriced_reason:
                raise ValueError("UNPRICED cost requires unpriced_reason")
        else:
            if self.basis is CostBasis.UNPRICED:
                raise ValueError("priced cost cannot use UNPRICED basis")
            if self.currency is None:
                raise ValueError("priced cost requires currency")
            object.__setattr__(self, "currency", _currency(self.currency))
        if self.pricing_rule_version is not None and self.pricing_rule_version < 1:
            raise ValueError("pricing_rule_version must be positive")
        for field, value, maximum in (
            ("pricing_rule_id", self.pricing_rule_id, 200),
            ("pricing_reference", self.pricing_reference, 300),
            ("source_reference", self.source_reference, 500),
            ("unpriced_reason", self.unpriced_reason, 200),
        ):
            if value is not None and (not value.strip() or len(value) > maximum):
                raise ValueError(f"{field} must contain 1..{maximum} characters when present")


@dataclass(frozen=True)
class AccountingDimensions:
    tenant_id: UUID
    client_id: UUID
    attempt_id: UUID
    provider_id: str
    model_id: str
    reasoning_profile: str
    fragment_id: str = "primary"
    client_credential_id: UUID | None = None
    provider_account_id: UUID | None = None
    provider_credential_id: UUID | None = None
    session_id: UUID | None = None
    task_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.provider_credential_id is not None and self.provider_account_id is None:
            raise ValueError("provider credential provenance requires provider account")
        for field, value, maximum in (
            ("provider_id", self.provider_id, 100),
            ("model_id", self.model_id, 200),
            ("reasoning_profile", self.reasoning_profile, 100),
            ("fragment_id", self.fragment_id, 100),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must contain 1..{maximum} characters")


@dataclass(frozen=True)
class AccountingEntry:
    entry_id: UUID
    dimensions: AccountingDimensions
    usage: TechnicalUsage
    estimated_cost: InternalCostSnapshot
    observed_cost: InternalCostSnapshot | None
    recorded_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.recorded_at, "recorded_at")
        if (
            self.observed_cost is not None
            and self.observed_cost.basis is not CostBasis.PROVIDER_OBSERVED
        ):
            raise ValueError("observed_cost must use PROVIDER_OBSERVED basis")

    @property
    def effective_cost(self) -> InternalCostSnapshot:
        if self.observed_cost is not None and self.observed_cost.amount is not None:
            return self.observed_cost
        return self.estimated_cost

    def same_fact(self, other: AccountingEntry) -> bool:
        """Idempotency comparison excluding generated ledger identity."""

        return (
            self.dimensions == other.dimensions
            and self.usage == other.usage
            and self.estimated_cost == other.estimated_cost
            and self.observed_cost == other.observed_cost
            and self.recorded_at == other.recorded_at
        )


@dataclass(frozen=True)
class CurrencyTotal:
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", _currency(self.currency))
        _non_negative(self.amount, "currency total")


@dataclass(frozen=True)
class CostAggregation:
    totals: tuple[CurrencyTotal, ...]
    unpriced_entries: int

    def __post_init__(self) -> None:
        if self.unpriced_entries < 0:
            raise ValueError("unpriced_entries cannot be negative")


def aggregate_costs(entries: tuple[AccountingEntry, ...]) -> CostAggregation:
    """Aggregate by currency only. No FX conversion or cross-currency sum is allowed."""

    totals: dict[str, Decimal] = {}
    unpriced = 0
    for entry in entries:
        cost = entry.effective_cost
        if cost.amount is None or cost.currency is None:
            if entry.usage.has_usage:
                unpriced += 1
            continue
        totals[cost.currency] = totals.get(cost.currency, Decimal("0")) + cost.amount
    return CostAggregation(
        totals=tuple(
            CurrencyTotal(currency=currency, amount=amount)
            for currency, amount in sorted(totals.items())
        ),
        unpriced_entries=unpriced,
    )
