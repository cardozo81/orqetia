"""Idempotent post-dispatch accounting projection from durable provider attempts."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from orqetia.execution import ProviderAttempt, ProviderAttemptStatus
from orqetia.usage_accounting import (
    AccountingDimensions,
    AccountingEntry,
    AccountingLedger,
    CostBasis,
    InternalCostSnapshot,
    NativeUsageQuantity,
    PricingResolver,
    TechnicalUsage,
)


class PricingCatalogVersion(Protocol):
    def resolver(self) -> PricingResolver: ...


class PricingCatalogVersionLookup(Protocol):
    async def get_version(
        self,
        catalog_version_id: UUID,
    ) -> PricingCatalogVersion | None: ...


def provider_usage_to_technical(attempt: ProviderAttempt) -> TechnicalUsage:
    """Normalize provider usage without double-representing token subcomponents."""

    provider_usage = attempt.usage
    if provider_usage is None:
        return TechnicalUsage(request_units=Decimal("1"))

    cached: int | None = None
    reasoning: int | None = None
    provider_total: int | None = None
    native: list[NativeUsageQuantity] = []
    has_token_marker = False

    for item in provider_usage.native:
        token_value = _token_marker_value(item.value, item.unit)
        if item.name == "cached_input_tokens" and token_value is not None:
            cached = token_value
            has_token_marker = True
        elif (
            item.name in {"reasoning_output_tokens", "reasoning_tokens"}
            and token_value is not None
        ):
            reasoning = token_value
            has_token_marker = True
        elif item.name == "provider_total_tokens" and token_value is not None:
            provider_total = token_value
            has_token_marker = True
        else:
            native.append(
                NativeUsageQuantity(
                    name=item.name,
                    quantity=item.value,
                    unit=item.unit,
                )
            )

    input_tokens: int | None = provider_usage.input_tokens
    output_tokens: int | None = provider_usage.output_tokens
    if (
        native
        and not has_token_marker
        and provider_usage.input_tokens == 0
        and provider_usage.output_tokens == 0
    ):
        input_tokens = None
        output_tokens = None

    return TechnicalUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning,
        provider_total_tokens=provider_total,
        request_units=Decimal("1"),
        native=tuple(native),
    )


def _token_marker_value(value: Decimal, unit: str) -> int | None:
    if unit != "tokens":
        return None
    integral = value.to_integral_value()
    if integral != value:
        raise ValueError("token usage marker must be an integer")
    return int(integral)


class AttemptAccountingObserver:
    """Project one completed attempt to the immutable accounting ledger."""

    def __init__(
        self,
        *,
        ledger: AccountingLedger,
        pricing_versions: PricingCatalogVersionLookup,
    ) -> None:
        self._ledger = ledger
        self._pricing_versions = pricing_versions

    async def record(self, attempt: ProviderAttempt) -> None:
        if attempt.status is not ProviderAttemptStatus.COMPLETED:
            return
        if attempt.terminal_at is None:
            raise ValueError("completed attempt requires terminal_at")

        usage = provider_usage_to_technical(attempt)
        if attempt.pricing_catalog_version_id is None:
            estimated = InternalCostSnapshot(
                basis=CostBasis.UNPRICED,
                amount=None,
                currency=None,
                unpriced_reason="PRICING_CATALOG_UNAVAILABLE_AT_DISPATCH",
            )
        else:
            version = await self._pricing_versions.get_version(
                attempt.pricing_catalog_version_id
            )
            if version is None:
                raise LookupError("frozen pricing catalog version not found")
            estimated = version.resolver().quote(
                provider_id=attempt.target.provider_id,
                model_id=attempt.target.model_id,
                reasoning_profile=attempt.target.reasoning_profile,
                usage=usage,
                occurred_at=attempt.terminal_at,
            ).cost

        observed = None
        if (
            attempt.cost is not None
            and attempt.cost.amount is not None
            and attempt.cost.currency is not None
        ):
            observed = InternalCostSnapshot(
                basis=CostBasis.PROVIDER_OBSERVED,
                amount=attempt.cost.amount,
                currency=attempt.cost.currency,
            )

        entry = AccountingEntry(
            entry_id=uuid5(
                NAMESPACE_URL,
                f"orqetia:accounting:{attempt.attempt_id}:primary",
            ),
            dimensions=AccountingDimensions(
                tenant_id=attempt.ownership.tenant_id,
                client_id=attempt.ownership.client_id,
                provider_account_id=attempt.provider_account_id,
                provider_credential_id=attempt.provider_credential_id,
                session_id=attempt.session_id,
                task_id=attempt.task_id,
                attempt_id=attempt.attempt_id,
                provider_id=attempt.target.provider_id,
                model_id=attempt.target.model_id,
                reasoning_profile=attempt.target.reasoning_profile,
            ),
            usage=usage,
            estimated_cost=estimated,
            observed_cost=observed,
            recorded_at=attempt.terminal_at,
        )
        await self._ledger.append(entry)
