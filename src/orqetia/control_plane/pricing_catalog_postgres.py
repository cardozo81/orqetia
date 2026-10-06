"""PostgreSQL repository for immutable administrative provider pricing catalogs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.usage_accounting import (
    ContextTier,
    PricingModel,
    PricingRule,
    ReasoningBillingMode,
    TimeWindow,
    TokenRates,
)

from .pricing_catalog_tables import (
    provider_pricing_assignment,
    provider_pricing_catalog_versions,
)
from .pricing_catalogs import (
    EffectiveProviderPricingCatalog,
    ProviderPricingAssignment,
    ProviderPricingCatalogRepository,
    ProviderPricingCatalogVersion,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _rates_json(rates: TokenRates | None) -> dict[str, object] | None:
    if rates is None:
        return None
    return {
        "input_per_million": str(rates.input_per_million),
        "output_per_million": str(rates.output_per_million),
        "cached_input_per_million": _decimal(rates.cached_input_per_million),
        "reasoning_per_million": _decimal(rates.reasoning_per_million),
        "reasoning_billing_mode": rates.reasoning_billing_mode.value,
    }


def _rates_from_json(value: object) -> TokenRates | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("persisted token rates must be an object")
    cached = value.get("cached_input_per_million")
    reasoning = value.get("reasoning_per_million")
    return TokenRates(
        input_per_million=Decimal(str(value["input_per_million"])),
        output_per_million=Decimal(str(value["output_per_million"])),
        cached_input_per_million=(
            None if cached is None else Decimal(str(cached))
        ),
        reasoning_per_million=(
            None if reasoning is None else Decimal(str(reasoning))
        ),
        reasoning_billing_mode=ReasoningBillingMode(
            str(value["reasoning_billing_mode"])
        ),
    )


def _rules_json(rules: tuple[PricingRule, ...]) -> list[dict[str, object]]:
    return [
        {
            "rule_id": rule.rule_id,
            "version": rule.version,
            "provider_id": rule.provider_id,
            "model_id": rule.model_id,
            "pricing_model": rule.pricing_model.value,
            "currency": rule.currency,
            "effective_from": rule.effective_from.isoformat(),
            "effective_to": (
                None if rule.effective_to is None else rule.effective_to.isoformat()
            ),
            "reasoning_profile": rule.reasoning_profile,
            "token_rates": _rates_json(rule.token_rates),
            "context_tiers": [
                {
                    "max_input_tokens": tier.max_input_tokens,
                    "rates": _rates_json(tier.rates),
                }
                for tier in rule.context_tiers
            ],
            "time_windows": [
                {
                    "start_hour_utc": window.start_hour_utc,
                    "end_hour_utc": window.end_hour_utc,
                    "rates": _rates_json(window.rates),
                }
                for window in rule.time_windows
            ],
            "request_rate": _decimal(rule.request_rate),
            "native_unit": rule.native_unit,
            "native_rate": _decimal(rule.native_rate),
            "source_reference": rule.source_reference,
        }
        for rule in rules
    ]


def _rules_from_json(value: object) -> tuple[PricingRule, ...]:
    if not isinstance(value, list):
        raise ValueError("persisted pricing rules must be an array")
    output: list[PricingRule] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("persisted pricing rule must be an object")
        tiers_raw = raw.get("context_tiers")
        windows_raw = raw.get("time_windows")
        if not isinstance(tiers_raw, list) or not isinstance(windows_raw, list):
            raise ValueError("persisted pricing tiers/windows must be arrays")
        tiers: list[ContextTier] = []
        for item in tiers_raw:
            if not isinstance(item, dict):
                raise ValueError("persisted context tier must be an object")
            rates = _rates_from_json(item.get("rates"))
            if rates is None:
                raise ValueError("persisted context tier requires rates")
            tiers.append(
                ContextTier(
                    max_input_tokens=cast(int | None, item.get("max_input_tokens")),
                    rates=rates,
                )
            )
        windows: list[TimeWindow] = []
        for item in windows_raw:
            if not isinstance(item, dict):
                raise ValueError("persisted time window must be an object")
            rates = _rates_from_json(item.get("rates"))
            if rates is None:
                raise ValueError("persisted time window requires rates")
            windows.append(
                TimeWindow(
                    start_hour_utc=int(item["start_hour_utc"]),
                    end_hour_utc=int(item["end_hour_utc"]),
                    rates=rates,
                )
            )
        effective_to_raw = raw.get("effective_to")
        request_rate_raw = raw.get("request_rate")
        native_rate_raw = raw.get("native_rate")
        output.append(
            PricingRule(
                rule_id=str(raw["rule_id"]),
                version=int(raw["version"]),
                provider_id=str(raw["provider_id"]),
                model_id=str(raw["model_id"]),
                pricing_model=PricingModel(str(raw["pricing_model"])),
                currency=str(raw["currency"]),
                effective_from=datetime.fromisoformat(str(raw["effective_from"])),
                effective_to=(
                    None
                    if effective_to_raw is None
                    else datetime.fromisoformat(str(effective_to_raw))
                ),
                reasoning_profile=cast(str | None, raw.get("reasoning_profile")),
                token_rates=_rates_from_json(raw.get("token_rates")),
                context_tiers=tuple(tiers),
                time_windows=tuple(windows),
                request_rate=(
                    None
                    if request_rate_raw is None
                    else Decimal(str(request_rate_raw))
                ),
                native_unit=cast(str | None, raw.get("native_unit")),
                native_rate=(
                    None
                    if native_rate_raw is None
                    else Decimal(str(native_rate_raw))
                ),
                source_reference=cast(str | None, raw.get("source_reference")),
            )
        )
    return tuple(output)


def _version_from_row(row: RowMapping) -> ProviderPricingCatalogVersion:
    return ProviderPricingCatalogVersion(
        catalog_version_id=cast(UUID, row["catalog_version_id"]),
        version_number=cast(int, row["version_number"]),
        rules=_rules_from_json(row["rules"]),
        created_at=row["created_at"],
    )


class PostgresProviderPricingCatalogRepository(ProviderPricingCatalogRepository):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def list_versions(self) -> tuple[ProviderPricingCatalogVersion, ...]:
        statement = sa.select(provider_pricing_catalog_versions).order_by(
            provider_pricing_catalog_versions.c.version_number
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_version_from_row(row) for row in rows)

    async def get_effective(self) -> EffectiveProviderPricingCatalog | None:
        statement = (
            sa.select(
                provider_pricing_assignment,
                provider_pricing_catalog_versions,
            )
            .join(
                provider_pricing_catalog_versions,
                provider_pricing_catalog_versions.c.catalog_version_id
                == provider_pricing_assignment.c.catalog_version_id,
            )
            .where(provider_pricing_assignment.c.assignment_key == "GLOBAL")
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        return EffectiveProviderPricingCatalog(
            version=_version_from_row(row),
            assignment=ProviderPricingAssignment(
                catalog_version_id=cast(UUID, row["catalog_version_id"]),
                assignment_version=cast(int, row["assignment_version"]),
                assigned_at=row["assigned_at"],
            ),
        )

    async def publish_and_activate(
        self,
        *,
        version: ProviderPricingCatalogVersion,
        assignment: ProviderPricingAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveProviderPricingCatalog:
        async with self._sessions.begin() as database:
            current = (
                await database.execute(
                    sa.select(provider_pricing_assignment)
                    .where(provider_pricing_assignment.c.assignment_key == "GLOBAL")
                    .with_for_update()
                )
            ).mappings().one_or_none()

            if current is None:
                if expected_assignment_version is not None:
                    raise ValueError("pricing assignment version conflict")
                if assignment.assignment_version != 1:
                    raise ValueError("initial pricing assignment_version must be 1")
            else:
                current_version = cast(int, current["assignment_version"])
                if current_version != expected_assignment_version:
                    raise ValueError("pricing assignment version conflict")
                if assignment.assignment_version != current_version + 1:
                    raise ValueError("pricing assignment_version must increment by one")

            await database.execute(
                sa.insert(provider_pricing_catalog_versions).values(
                    catalog_version_id=version.catalog_version_id,
                    version_number=version.version_number,
                    rules=_rules_json(version.rules),
                    created_at=version.created_at,
                )
            )
            values = {
                "assignment_key": "GLOBAL",
                "catalog_version_id": assignment.catalog_version_id,
                "assignment_version": assignment.assignment_version,
                "assigned_at": assignment.assigned_at,
            }
            if current is None:
                await database.execute(
                    sa.insert(provider_pricing_assignment).values(**values)
                )
            else:
                await database.execute(
                    sa.update(provider_pricing_assignment)
                    .where(provider_pricing_assignment.c.assignment_key == "GLOBAL")
                    .values(**values)
                )

        return EffectiveProviderPricingCatalog(
            version=version,
            assignment=assignment,
        )



def decode_pricing_rules(value: object) -> tuple[PricingRule, ...]:
    """Decode the canonical administrative provider-pricing JSON shape."""
    return _rules_from_json(value)
