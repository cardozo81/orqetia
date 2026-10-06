"""PostgreSQL adapter for immutable technical usage/accounting facts."""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .domain import (
    AccountingDimensions,
    AccountingEntry,
    CostBasis,
    InternalCostSnapshot,
    NativeUsageQuantity,
    PricingModel,
    TechnicalUsage,
)
from .tables import accounting_ledger

SessionFactory = async_sessionmaker[AsyncSession]


def _native_json(usage: TechnicalUsage) -> list[dict[str, str]]:
    return [
        {"name": item.name, "quantity": str(item.quantity), "unit": item.unit}
        for item in usage.native
    ]


def _native_from_json(value: object) -> tuple[NativeUsageQuantity, ...]:
    if not isinstance(value, list):
        raise ValueError("persisted native_usage must be an array")
    output: list[NativeUsageQuantity] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("persisted native_usage entries must be objects")
        output.append(
            NativeUsageQuantity(
                name=str(item["name"]),
                quantity=Decimal(str(item["quantity"])),
                unit=str(item["unit"]),
            )
        )
    return tuple(output)


def _optional_pricing_model(value: object) -> PricingModel | None:
    return None if value is None else PricingModel(str(value))


def _entry_from_row(row: RowMapping) -> AccountingEntry:
    estimated = InternalCostSnapshot(
        basis=CostBasis(str(row["estimated_basis"])),
        amount=cast(Decimal | None, row["estimated_cost"]),
        currency=cast(str | None, row["estimated_currency"]),
        pricing_model=_optional_pricing_model(row["pricing_model"]),
        pricing_rule_id=cast(str | None, row["pricing_rule_id"]),
        pricing_rule_version=cast(int | None, row["pricing_rule_version"]),
        pricing_reference=cast(str | None, row["pricing_reference"]),
        source_reference=cast(str | None, row["pricing_source_reference"]),
        unpriced_reason=cast(str | None, row["unpriced_reason"]),
    )
    observed_amount = cast(Decimal | None, row["observed_cost"])
    observed = None
    if observed_amount is not None:
        observed = InternalCostSnapshot(
            basis=CostBasis.PROVIDER_OBSERVED,
            amount=observed_amount,
            currency=cast(str, row["observed_currency"]),
            source_reference=cast(str | None, row["observed_source_reference"]),
        )
    return AccountingEntry(
        entry_id=cast(UUID, row["entry_id"]),
        dimensions=AccountingDimensions(
            tenant_id=cast(UUID, row["tenant_id"]),
            client_id=cast(UUID, row["client_id"]),
            client_credential_id=cast(UUID | None, row["client_credential_id"]),
            provider_account_id=cast(UUID | None, row["provider_account_id"]),
            provider_credential_id=cast(UUID | None, row["provider_credential_id"]),
            session_id=cast(UUID | None, row["session_id"]),
            task_id=cast(UUID | None, row["task_id"]),
            attempt_id=cast(UUID, row["attempt_id"]),
            fragment_id=cast(str, row["fragment_id"]),
            provider_id=cast(str, row["provider_id"]),
            model_id=cast(str, row["model_id"]),
            reasoning_profile=cast(str, row["reasoning_profile"]),
        ),
        usage=TechnicalUsage(
            input_tokens=cast(int | None, row["input_tokens"]),
            cached_input_tokens=cast(int | None, row["cached_input_tokens"]),
            output_tokens=cast(int | None, row["output_tokens"]),
            reasoning_tokens=cast(int | None, row["reasoning_tokens"]),
            provider_total_tokens=cast(int | None, row["provider_total_tokens"]),
            request_units=cast(Decimal | None, row["request_units"]),
            native=_native_from_json(row["native_usage"]),
        ),
        estimated_cost=estimated,
        observed_cost=observed,
        recorded_at=row["recorded_at"],
    )


def _values(entry: AccountingEntry) -> dict[str, object]:
    estimated = entry.estimated_cost
    observed = entry.observed_cost
    dimensions = entry.dimensions
    usage = entry.usage
    return {
        "entry_id": entry.entry_id,
        "tenant_id": dimensions.tenant_id,
        "client_id": dimensions.client_id,
        "client_credential_id": dimensions.client_credential_id,
        "provider_account_id": dimensions.provider_account_id,
        "provider_credential_id": dimensions.provider_credential_id,
        "session_id": dimensions.session_id,
        "task_id": dimensions.task_id,
        "attempt_id": dimensions.attempt_id,
        "fragment_id": dimensions.fragment_id,
        "provider_id": dimensions.provider_id,
        "model_id": dimensions.model_id,
        "reasoning_profile": dimensions.reasoning_profile,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "provider_total_tokens": usage.provider_total_tokens,
        "canonical_total_tokens": usage.total_tokens,
        "request_units": usage.request_units,
        "native_usage": _native_json(usage),
        "estimated_basis": estimated.basis.value,
        "estimated_cost": estimated.amount,
        "estimated_currency": estimated.currency,
        "pricing_model": None if estimated.pricing_model is None else estimated.pricing_model.value,
        "pricing_rule_id": estimated.pricing_rule_id,
        "pricing_rule_version": estimated.pricing_rule_version,
        "pricing_reference": estimated.pricing_reference,
        "pricing_source_reference": estimated.source_reference,
        "unpriced_reason": estimated.unpriced_reason,
        "observed_cost": None if observed is None else observed.amount,
        "observed_currency": None if observed is None else observed.currency,
        "observed_source_reference": None if observed is None else observed.source_reference,
        "recorded_at": entry.recorded_at,
    }


class PostgresAccountingLedger:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def append(self, entry: AccountingEntry) -> AccountingEntry:
        statement = (
            insert(accounting_ledger)
            .values(**_values(entry))
            .on_conflict_do_nothing(
                index_elements=[
                    accounting_ledger.c.attempt_id,
                    accounting_ledger.c.fragment_id,
                ]
            )
            .returning(accounting_ledger.c.entry_id)
        )
        async with self._sessions.begin() as database:
            inserted = (await database.execute(statement)).scalar_one_or_none()
            if inserted is not None:
                return entry
            row = (
                await database.execute(
                    sa.select(accounting_ledger).where(
                        accounting_ledger.c.attempt_id == entry.dimensions.attempt_id,
                        accounting_ledger.c.fragment_id == entry.dimensions.fragment_id,
                    )
                )
            ).mappings().one()
            existing = _entry_from_row(row)
            if not existing.same_fact(entry):
                raise ValueError("attempt fragment accounting fact is immutable")
            return existing

    async def get(
        self,
        *,
        attempt_id: UUID,
        fragment_id: str = "primary",
    ) -> AccountingEntry | None:
        statement = sa.select(accounting_ledger).where(
            accounting_ledger.c.attempt_id == attempt_id,
            accounting_ledger.c.fragment_id == fragment_id,
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _entry_from_row(row)

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[AccountingEntry, ...]:
        statement = (
            sa.select(accounting_ledger)
            .where(
                accounting_ledger.c.tenant_id == tenant_id,
                accounting_ledger.c.client_id == client_id,
            )
            .order_by(accounting_ledger.c.recorded_at, accounting_ledger.c.entry_id)
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_entry_from_row(row) for row in rows)
