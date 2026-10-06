"""Operational and financial intelligence over typed reporting rollups."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from orqetia.usage_accounting import CurrencyTotal, NativeUsageQuantity

from .reporting import (
    BackofficeReportAccess,
    ReportQuery,
    ReportRollup,
    ReportRollupStore,
)


class IntelligenceDimension(StrEnum):
    TENANT = "TENANT"
    CLIENT = "CLIENT"
    CLIENT_CREDENTIAL = "CLIENT_CREDENTIAL"
    PROVIDER = "PROVIDER"
    PROVIDER_ACCOUNT = "PROVIDER_ACCOUNT"
    PROVIDER_CREDENTIAL = "PROVIDER_CREDENTIAL"
    MODEL = "MODEL"
    STATUS = "STATUS"
    ERROR_CLASS = "ERROR_CLASS"
    PERIOD = "PERIOD"


@dataclass(frozen=True)
class IntelligenceQuery:
    filters: ReportQuery
    group_by: tuple[IntelligenceDimension, ...]
    timezone: str = "UTC"
    maximum_source_rows: int = 50_000

    def __post_init__(self) -> None:
        normalized = tuple(dict.fromkeys(self.group_by))
        if not normalized:
            raise ValueError("intelligence query requires at least one dimension")
        object.__setattr__(self, "group_by", normalized)
        if self.maximum_source_rows < 1 or self.maximum_source_rows > 50_000:
            raise ValueError("maximum_source_rows must be between 1 and 50000")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("unknown intelligence timezone") from error


@dataclass(frozen=True)
class IntelligenceDimensionValue:
    dimension: IntelligenceDimension
    value: str


@dataclass(frozen=True)
class IntelligenceMetrics:
    attempts: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    native_usage: tuple[NativeUsageQuantity, ...]
    successes: int
    partials: int
    failures: int
    average_latency_ms: Decimal | None
    cycles: int
    retries: int
    peak_attempts_per_bucket: int
    failure_rate: Decimal
    retry_rate: Decimal
    estimated_costs: tuple[CurrencyTotal, ...]
    observed_costs: tuple[CurrencyTotal, ...]
    unpriced_attempts: int


@dataclass(frozen=True)
class IntelligenceRow:
    dimensions: tuple[IntelligenceDimensionValue, ...]
    metrics: IntelligenceMetrics


@dataclass(frozen=True)
class ExternalCapacityIndicator:
    provider_id: str
    provider_account_id: UUID
    native_unit: str
    observed_at: datetime
    source: str
    remaining: Decimal | None = None
    limit: Decimal | None = None
    reset_at: datetime | None = None


@dataclass(frozen=True)
class QuotaUtilizationIndicator:
    tenant_id: UUID
    client_id: UUID | None
    metric: str
    consumed: Decimal
    reserved: Decimal
    limit: Decimal
    burst: Decimal
    window_key: str


class ExternalCapacityIndicatorSource(Protocol):
    async def read(
        self,
        *,
        filters: ReportQuery,
    ) -> tuple[ExternalCapacityIndicator, ...]: ...


class QuotaUtilizationIndicatorSource(Protocol):
    async def read(
        self,
        *,
        filters: ReportQuery,
    ) -> tuple[QuotaUtilizationIndicator, ...]: ...


@dataclass(frozen=True)
class OperationalIntelligenceResult:
    rows: tuple[IntelligenceRow, ...]
    external_capacity: tuple[ExternalCapacityIndicator, ...]
    quota_utilization: tuple[QuotaUtilizationIndicator, ...]
    as_of: datetime | None
    timezone: str
    filters_fingerprint: str
    source_row_count: int


class OperationalFinancialIntelligenceService:
    def __init__(
        self,
        *,
        rollups: ReportRollupStore,
        capacity: ExternalCapacityIndicatorSource | None = None,
        quotas: QuotaUtilizationIndicatorSource | None = None,
    ) -> None:
        self._rollups = rollups
        self._capacity = capacity
        self._quotas = quotas

    async def analyze(
        self,
        *,
        access: BackofficeReportAccess,
        query: IntelligenceQuery,
    ) -> OperationalIntelligenceResult:
        _authorize(access, query.filters)
        if not access.can_view_financial:
            raise PermissionError("financial intelligence permission is required")

        rows = await self._rollups.query(
            filters=query.filters,
            limit=query.maximum_source_rows + 1,
        )
        if len(rows) > query.maximum_source_rows:
            raise ValueError("intelligence source row budget exceeded")

        zone = ZoneInfo(query.timezone)
        grouped = _aggregate(rows, query.group_by, zone)
        capacity = (
            ()
            if self._capacity is None
            else await self._capacity.read(filters=query.filters)
        )
        quotas = (
            ()
            if self._quotas is None
            else await self._quotas.read(filters=query.filters)
        )
        return OperationalIntelligenceResult(
            rows=grouped,
            external_capacity=capacity,
            quota_utilization=quotas,
            as_of=max((row.as_of for row in rows), default=None),
            timezone=query.timezone,
            filters_fingerprint=_filters_fingerprint(query.filters),
            source_row_count=len(rows),
        )


def _aggregate(
    rows: tuple[ReportRollup, ...],
    dimensions: tuple[IntelligenceDimension, ...],
    zone: ZoneInfo,
) -> tuple[IntelligenceRow, ...]:
    state: dict[tuple[str, ...], dict[str, object]] = {}
    for row in rows:
        key = tuple(_dimension_value(row, dimension, zone) for dimension in dimensions)
        item = state.setdefault(
            key,
            {
                "attempts": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "native": {},
                "successes": 0,
                "partials": 0,
                "failures": 0,
                "latency_ms_total": 0,
                "cycles": 0,
                "retries": 0,
                "period_attempts": {},
                "estimated": {},
                "observed": {},
                "unpriced": 0,
            },
        )
        attempts = int(item["attempts"]) + row.attempts
        item["attempts"] = attempts
        for field in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "latency_ms_total",
            "cycles",
            "retries",
        ):
            item[field] = int(item[field]) + getattr(row, field)

        status = row.status.upper()
        if status == "SUCCESS":
            item["successes"] = int(item["successes"]) + row.attempts
        elif status == "PARTIAL":
            item["partials"] = int(item["partials"]) + row.attempts
        else:
            item["failures"] = int(item["failures"]) + row.attempts

        native = item["native"]
        assert isinstance(native, dict)
        for usage in row.native_usage:
            identity = (usage.name, usage.unit)
            native[identity] = native.get(identity, Decimal("0")) + usage.quantity

        period_attempts = item["period_attempts"]
        assert isinstance(period_attempts, dict)
        period_key = row.period_start.astimezone(zone).isoformat()
        period_attempts[period_key] = (
            int(period_attempts.get(period_key, 0)) + row.attempts
        )

        _add_currency(
            item["estimated"],
            row.estimated_currency,
            row.estimated_cost,
        )
        _add_currency(
            item["observed"],
            row.observed_currency,
            row.observed_cost,
        )
        item["unpriced"] = int(item["unpriced"]) + row.unpriced_attempts

    output: list[IntelligenceRow] = []
    for key, item in state.items():
        attempts = int(item["attempts"])
        failures = int(item["failures"])
        retries = int(item["retries"])
        latency_total = int(item["latency_ms_total"])
        native = item["native"]
        period_attempts = item["period_attempts"]
        estimated = item["estimated"]
        observed = item["observed"]
        assert isinstance(native, dict)
        assert isinstance(period_attempts, dict)
        assert isinstance(estimated, dict)
        assert isinstance(observed, dict)

        output.append(
            IntelligenceRow(
                dimensions=tuple(
                    IntelligenceDimensionValue(dimension=dimension, value=value)
                    for dimension, value in zip(dimensions, key, strict=True)
                ),
                metrics=IntelligenceMetrics(
                    attempts=attempts,
                    input_tokens=int(item["input_tokens"]),
                    cached_input_tokens=int(item["cached_input_tokens"]),
                    output_tokens=int(item["output_tokens"]),
                    reasoning_tokens=int(item["reasoning_tokens"]),
                    total_tokens=int(item["total_tokens"]),
                    native_usage=tuple(
                        NativeUsageQuantity(
                            name=name,
                            unit=unit,
                            quantity=quantity,
                        )
                        for (name, unit), quantity in sorted(native.items())
                    ),
                    successes=int(item["successes"]),
                    partials=int(item["partials"]),
                    failures=failures,
                    average_latency_ms=(
                        None
                        if attempts == 0
                        else Decimal(latency_total) / Decimal(attempts)
                    ),
                    cycles=int(item["cycles"]),
                    retries=retries,
                    peak_attempts_per_bucket=max(
                        (int(value) for value in period_attempts.values()),
                        default=0,
                    ),
                    failure_rate=_ratio(failures, attempts),
                    retry_rate=_ratio(retries, attempts),
                    estimated_costs=_currency_totals(estimated),
                    observed_costs=_currency_totals(observed),
                    unpriced_attempts=int(item["unpriced"]),
                ),
            )
        )
    output.sort(
        key=lambda row: (
            -row.metrics.attempts,
            tuple(value.value for value in row.dimensions),
        )
    )
    return tuple(output)


def _dimension_value(
    row: ReportRollup,
    dimension: IntelligenceDimension,
    zone: ZoneInfo,
) -> str:
    mapping = {
        IntelligenceDimension.TENANT: str(row.tenant_id),
        IntelligenceDimension.CLIENT: str(row.client_id),
        IntelligenceDimension.CLIENT_CREDENTIAL: (
            "-" if row.client_credential_id is None else str(row.client_credential_id)
        ),
        IntelligenceDimension.PROVIDER: row.provider_id,
        IntelligenceDimension.PROVIDER_ACCOUNT: (
            "-" if row.provider_account_id is None else str(row.provider_account_id)
        ),
        IntelligenceDimension.PROVIDER_CREDENTIAL: (
            "-"
            if row.provider_credential_id is None
            else str(row.provider_credential_id)
        ),
        IntelligenceDimension.MODEL: row.model_id,
        IntelligenceDimension.STATUS: row.status,
        IntelligenceDimension.ERROR_CLASS: row.error_class or "-",
        IntelligenceDimension.PERIOD: row.period_start.astimezone(zone).isoformat(),
    }
    return mapping[dimension]


def _add_currency(
    raw: object,
    currency: str | None,
    amount: Decimal | None,
) -> None:
    if currency is None or amount is None:
        return
    assert isinstance(raw, dict)
    raw[currency] = raw.get(currency, Decimal("0")) + amount


def _currency_totals(raw: dict[object, object]) -> tuple[CurrencyTotal, ...]:
    return tuple(
        CurrencyTotal(currency=str(currency), amount=Decimal(amount))
        for currency, amount in sorted(raw.items())
    )


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return Decimal(numerator) / Decimal(denominator)


def _authorize(
    access: BackofficeReportAccess,
    filters: ReportQuery,
) -> None:
    allowed = access.allowed_tenant_ids
    if allowed is None:
        return
    if filters.tenant_id is None:
        raise PermissionError(
            "tenant filter is required for tenant-restricted intelligence"
        )
    if filters.tenant_id not in allowed:
        raise PermissionError("intelligence tenant is outside authorized scope")


def _filters_fingerprint(filters: ReportQuery) -> str:
    value = {
        key: (
            raw.isoformat()
            if isinstance(raw, datetime)
            else str(raw)
            if isinstance(raw, UUID)
            else raw
        )
        for key, raw in vars(filters).items()
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
