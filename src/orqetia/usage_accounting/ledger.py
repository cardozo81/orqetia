"""Immutable accounting ledger service and deterministic in-memory test adapter."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid7

from .domain import (
    AccountingDimensions,
    AccountingEntry,
    CostBasis,
    InternalCostSnapshot,
    MonetaryAmount,
    TechnicalUsage,
)
from .pricing import PricingResolver


class AccountingLedger(Protocol):
    async def append(self, entry: AccountingEntry) -> AccountingEntry:
        """Append one immutable fact, idempotent by attempt_id + fragment_id."""
        ...

    async def get(
        self,
        *,
        attempt_id: UUID,
        fragment_id: str = "primary",
    ) -> AccountingEntry | None:
        """Return one immutable attempt-fragment accounting fact."""
        ...

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[AccountingEntry, ...]:
        """Return facts visible inside one tenant/client ownership envelope."""
        ...


class InMemoryAccountingLedger:
    """Deterministic fake used by ordinary zero-cost contract tests."""

    def __init__(self) -> None:
        self._entries: dict[tuple[UUID, str], AccountingEntry] = {}

    async def append(self, entry: AccountingEntry) -> AccountingEntry:
        key = (entry.dimensions.attempt_id, entry.dimensions.fragment_id)
        existing = self._entries.get(key)
        if existing is None:
            self._entries[key] = entry
            return entry
        if not existing.same_fact(entry):
            raise ValueError("attempt fragment accounting fact is immutable")
        return existing

    async def get(
        self,
        *,
        attempt_id: UUID,
        fragment_id: str = "primary",
    ) -> AccountingEntry | None:
        return self._entries.get((attempt_id, fragment_id))

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[AccountingEntry, ...]:
        return tuple(
            sorted(
                (
                    entry
                    for entry in self._entries.values()
                    if entry.dimensions.tenant_id == tenant_id
                    and entry.dimensions.client_id == client_id
                ),
                key=lambda entry: (entry.recorded_at, str(entry.entry_id)),
            )
        )


class AccountingService:
    def __init__(self, *, pricing: PricingResolver, ledger: AccountingLedger) -> None:
        self._pricing = pricing
        self._ledger = ledger

    async def record(
        self,
        *,
        dimensions: AccountingDimensions,
        usage: TechnicalUsage,
        recorded_at: object,
        observed_cost: MonetaryAmount | None = None,
        observed_source_reference: str | None = None,
    ) -> AccountingEntry:
        from datetime import datetime

        if not isinstance(recorded_at, datetime):
            raise TypeError("recorded_at must be datetime")
        quote = self._pricing.quote(
            provider_id=dimensions.provider_id,
            model_id=dimensions.model_id,
            reasoning_profile=dimensions.reasoning_profile,
            usage=usage,
            occurred_at=recorded_at,
        )
        observed = None
        if observed_cost is not None:
            observed = InternalCostSnapshot(
                basis=CostBasis.PROVIDER_OBSERVED,
                amount=observed_cost.amount,
                currency=observed_cost.currency,
                source_reference=observed_source_reference,
            )
        entry = AccountingEntry(
            entry_id=uuid7(),
            dimensions=dimensions,
            usage=usage,
            estimated_cost=quote.cost,
            observed_cost=observed,
            recorded_at=recorded_at,
        )
        return await self._ledger.append(entry)
