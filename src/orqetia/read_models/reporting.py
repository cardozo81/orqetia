"""Typed reporting read models with strict client/backoffice exposure separation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid7

from orqetia.usage_accounting import NativeUsageQuantity

from .domain import SURFACE_POLICIES, ReadSurface
from .pagination import paginate


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _non_negative(value: int | Decimal | None, field: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field} cannot be negative")


@dataclass(frozen=True)
class ReportRollup:
    rollup_id: UUID
    period_start: datetime
    period_end: datetime
    as_of: datetime
    tenant_id: UUID
    client_id: UUID
    provider_id: str
    model_id: str
    status: str
    attempts: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    native_usage: tuple[NativeUsageQuantity, ...] = ()
    client_credential_id: UUID | None = None
    provider_account_id: UUID | None = None
    provider_credential_id: UUID | None = None
    error_class: str | None = None
    latency_ms_total: int = 0
    cycles: int = 0
    retries: int = 0
    estimated_cost: Decimal | None = None
    estimated_currency: str | None = None
    observed_cost: Decimal | None = None
    observed_currency: str | None = None
    unpriced_attempts: int = 0

    def __post_init__(self) -> None:
        for field, value in (
            ("period_start", self.period_start),
            ("period_end", self.period_end),
            ("as_of", self.as_of),
        ):
            _aware(value, field)
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        if self.as_of < self.period_end:
            raise ValueError("as_of cannot precede period_end")
        for field, value in (
            ("attempts", self.attempts),
            ("input_tokens", self.input_tokens),
            ("cached_input_tokens", self.cached_input_tokens),
            ("output_tokens", self.output_tokens),
            ("reasoning_tokens", self.reasoning_tokens),
            ("total_tokens", self.total_tokens),
            ("latency_ms_total", self.latency_ms_total),
            ("cycles", self.cycles),
            ("retries", self.retries),
            ("unpriced_attempts", self.unpriced_attempts),
            ("estimated_cost", self.estimated_cost),
            ("observed_cost", self.observed_cost),
        ):
            _non_negative(value, field)
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        if self.provider_credential_id is not None and self.provider_account_id is None:
            raise ValueError("provider credential provenance requires provider account")
        for field, value, maximum in (
            ("provider_id", self.provider_id, 100),
            ("model_id", self.model_id, 200),
            ("status", self.status, 100),
            ("error_class", self.error_class, 200),
        ):
            if value is not None and (not value.strip() or len(value) > maximum):
                raise ValueError(f"{field} must contain 1..{maximum} characters")
        if (self.estimated_cost is None) != (self.estimated_currency is None):
            raise ValueError("estimated cost and currency must be both present or absent")
        if (self.observed_cost is None) != (self.observed_currency is None):
            raise ValueError("observed cost and currency must be both present or absent")

    def backoffice_payload(self) -> dict[str, object]:
        return {
            "rollup_id": str(self.rollup_id),
            "period_start": self.period_start,
            "period_end": self.period_end,
            "as_of": self.as_of,
            "tenant_id": str(self.tenant_id),
            "client_id": str(self.client_id),
            "client_credential_id": (
                None
                if self.client_credential_id is None
                else str(self.client_credential_id)
            ),
            "provider_id": self.provider_id,
            "provider_account_id": (
                None
                if self.provider_account_id is None
                else str(self.provider_account_id)
            ),
            "provider_credential_id": (
                None
                if self.provider_credential_id is None
                else str(self.provider_credential_id)
            ),
            "model_id": self.model_id,
            "status": self.status,
            "error_class": self.error_class,
            "attempts": self.attempts,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "native_usage": [
                {
                    "name": item.name,
                    "unit": item.unit,
                    "quantity": str(item.quantity),
                }
                for item in self.native_usage
            ],
            "latency_ms_total": self.latency_ms_total,
            "cycles": self.cycles,
            "retries": self.retries,
            "estimated_cost": (
                None if self.estimated_cost is None else str(self.estimated_cost)
            ),
            "estimated_currency": self.estimated_currency,
            "observed_cost": (
                None if self.observed_cost is None else str(self.observed_cost)
            ),
            "observed_currency": self.observed_currency,
            "unpriced_attempts": self.unpriced_attempts,
        }


@dataclass(frozen=True)
class ReportQuery:
    period_from: datetime | None = None
    period_to: datetime | None = None
    tenant_id: UUID | None = None
    client_id: UUID | None = None
    client_credential_id: UUID | None = None
    provider_id: str | None = None
    provider_account_id: UUID | None = None
    provider_credential_id: UUID | None = None
    model_id: str | None = None
    status: str | None = None
    error_class: str | None = None

    def __post_init__(self) -> None:
        if self.period_from is not None:
            _aware(self.period_from, "period_from")
        if self.period_to is not None:
            _aware(self.period_to, "period_to")
        if (
            self.period_from is not None
            and self.period_to is not None
            and self.period_to <= self.period_from
        ):
            raise ValueError("period_to must be after period_from")
        if self.client_id is not None and self.tenant_id is None:
            raise ValueError("client_id filter requires tenant_id")


class ReportRollupStore(Protocol):
    async def put(self, rollup: ReportRollup) -> None: ...

    async def query(
        self,
        *,
        filters: ReportQuery,
        limit: int,
    ) -> tuple[ReportRollup, ...]: ...


class InMemoryReportRollupStore:
    def __init__(self) -> None:
        self._items: dict[UUID, ReportRollup] = {}

    async def put(self, rollup: ReportRollup) -> None:
        self._items[rollup.rollup_id] = rollup

    async def query(
        self,
        *,
        filters: ReportQuery,
        limit: int,
    ) -> tuple[ReportRollup, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        matches = [
            item
            for item in self._items.values()
            if _matches(item, filters)
        ]
        matches.sort(key=lambda item: (item.period_start, str(item.rollup_id)))
        return tuple(matches[:limit])


@dataclass(frozen=True)
class ClientUsageBucket:
    period_start: datetime
    period_end: datetime
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    native_usage: tuple[NativeUsageQuantity, ...]

    def client_payload(self) -> dict[str, object]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "usage": {
                "input_tokens": self.input_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "total_tokens": self.total_tokens,
                "native_usage": [
                    {
                        "unit": item.unit,
                        "quantity": float(item.quantity),
                    }
                    for item in self.native_usage
                ],
            },
        }


@dataclass(frozen=True)
class ClientUsagePage:
    items: tuple[ClientUsageBucket, ...]
    next_cursor: str | None
    as_of: datetime | None

    def client_payload(self) -> dict[str, object]:
        return {
            "items": [item.client_payload() for item in self.items],
            "next_cursor": self.next_cursor,
            "as_of": self.as_of,
        }


class ClientUsageReportService:
    def __init__(self, store: ReportRollupStore) -> None:
        self._store = store

    async def read(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        period_from: datetime | None,
        period_to: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> ClientUsagePage:
        maximum = SURFACE_POLICIES[ReadSurface.USAGE_SUMMARY].maximum_page_size
        if limit < 1 or limit > maximum:
            raise ValueError("limit outside allowed range")
        query = ReportQuery(
            tenant_id=tenant_id,
            client_id=client_id,
            period_from=period_from,
            period_to=period_to,
        )
        rows = await self._store.query(
            filters=query,
            limit=50_000,
        )
        buckets = _client_buckets(rows)
        fingerprint = _query_fingerprint(
            {
                "tenant_id": str(tenant_id),
                "client_id": str(client_id),
                "period_from": (
                    None if period_from is None else period_from.isoformat()
                ),
                "period_to": None if period_to is None else period_to.isoformat(),
            }
        )
        page = paginate(
            buckets,
            key=lambda item: (
                f"{item.period_start.isoformat()}|{item.period_end.isoformat()}"
            ),
            page_size=limit,
            query_fingerprint=fingerprint,
            cursor=cursor,
            maximum_page_size=maximum,
        )
        as_of = max((row.as_of for row in rows), default=None)
        return ClientUsagePage(page.items, page.next_cursor, as_of)


@dataclass(frozen=True)
class BackofficeReportAccess:
    can_view_financial: bool
    can_export: bool
    allowed_tenant_ids: frozenset[UUID] | None = None


@dataclass(frozen=True)
class BackofficeReportPage:
    rows: tuple[ReportRollup, ...]
    as_of: datetime | None


@dataclass(frozen=True)
class ReportExportAuditEvent:
    event_id: UUID
    filters_fingerprint: str
    row_count: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        _aware(self.occurred_at, "occurred_at")
        _non_negative(self.row_count, "row_count")


class ReportExportAuditSink(Protocol):
    async def record(self, event: ReportExportAuditEvent) -> None: ...


class InMemoryReportExportAuditSink:
    def __init__(self) -> None:
        self.events: list[ReportExportAuditEvent] = []

    async def record(self, event: ReportExportAuditEvent) -> None:
        self.events.append(event)


class BackofficeReportingService:
    def __init__(
        self,
        *,
        store: ReportRollupStore,
        export_audit: ReportExportAuditSink,
    ) -> None:
        self._store = store
        self._export_audit = export_audit

    async def read(
        self,
        *,
        access: BackofficeReportAccess,
        filters: ReportQuery,
        limit: int = 1000,
    ) -> BackofficeReportPage:
        _authorize_filters(access, filters)
        if not access.can_view_financial:
            raise PermissionError("financial reporting permission is required")
        rows = await self._store.query(filters=filters, limit=limit)
        as_of = max((row.as_of for row in rows), default=None)
        return BackofficeReportPage(rows=rows, as_of=as_of)

    async def export(
        self,
        *,
        access: BackofficeReportAccess,
        filters: ReportQuery,
        occurred_at: datetime,
    ) -> tuple[dict[str, object], ...]:
        _authorize_filters(access, filters)
        if not access.can_view_financial or not access.can_export:
            raise PermissionError("financial export permission is required")
        maximum = SURFACE_POLICIES[
            ReadSurface.PROVIDER_COST_SUMMARY
        ].maximum_export_rows
        rows = await self._store.query(filters=filters, limit=maximum + 1)
        if len(rows) > maximum:
            raise ValueError("report export exceeds bounded row limit")
        payload = tuple(row.backoffice_payload() for row in rows)
        await self._export_audit.record(
            ReportExportAuditEvent(
                event_id=uuid7(),
                filters_fingerprint=_query_fingerprint(_query_json(filters)),
                row_count=len(payload),
                occurred_at=occurred_at,
            )
        )
        return payload


def _client_buckets(
    rows: tuple[ReportRollup, ...],
) -> tuple[ClientUsageBucket, ...]:
    grouped: dict[
        tuple[datetime, datetime],
        dict[str, object],
    ] = {}
    for row in rows:
        key = (row.period_start, row.period_end)
        bucket = grouped.setdefault(
            key,
            {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "native": {},
            },
        )
        for field in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            bucket[field] = int(bucket[field]) + getattr(row, field)
        native = bucket["native"]
        assert isinstance(native, dict)
        for item in row.native_usage:
            identity = (item.name, item.unit)
            native[identity] = native.get(identity, Decimal("0")) + item.quantity

    output: list[ClientUsageBucket] = []
    for (period_start, period_end), bucket in sorted(grouped.items()):
        native = bucket["native"]
        assert isinstance(native, dict)
        output.append(
            ClientUsageBucket(
                period_start=period_start,
                period_end=period_end,
                input_tokens=int(bucket["input_tokens"]),
                cached_input_tokens=int(bucket["cached_input_tokens"]),
                output_tokens=int(bucket["output_tokens"]),
                reasoning_tokens=int(bucket["reasoning_tokens"]),
                total_tokens=int(bucket["total_tokens"]),
                native_usage=tuple(
                    NativeUsageQuantity(name=name, unit=unit, quantity=quantity)
                    for (name, unit), quantity in sorted(native.items())
                ),
            )
        )
    return tuple(output)


def _authorize_filters(
    access: BackofficeReportAccess,
    filters: ReportQuery,
) -> None:
    allowed = access.allowed_tenant_ids
    if allowed is None:
        return
    if filters.tenant_id is None:
        raise PermissionError(
            "tenant filter is required for tenant-restricted reporting access"
        )
    if filters.tenant_id not in allowed:
        raise PermissionError("report tenant is outside authorized scope")


def _matches(item: ReportRollup, filters: ReportQuery) -> bool:
    if filters.period_from is not None and item.period_end <= filters.period_from:
        return False
    if filters.period_to is not None and item.period_start >= filters.period_to:
        return False
    for field in (
        "tenant_id",
        "client_id",
        "client_credential_id",
        "provider_id",
        "provider_account_id",
        "provider_credential_id",
        "model_id",
        "status",
        "error_class",
    ):
        expected = getattr(filters, field)
        if expected is not None and getattr(item, field) != expected:
            return False
    return True


def _query_json(filters: ReportQuery) -> dict[str, object]:
    return {
        field: (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
        for field, value in vars(filters).items()
    }


def _query_fingerprint(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
