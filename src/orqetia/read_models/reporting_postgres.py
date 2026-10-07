"""PostgreSQL reporting-rollup read store."""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from orqetia.usage_accounting import NativeUsageQuantity

from .reporting import REPORT_STORE_MAX_ROWS, ReportQuery, ReportRollup
from .reporting_tables import report_rollups

SessionFactory = async_sessionmaker[AsyncSession]


def _native_json(rollup: ReportRollup) -> list[dict[str, str]]:
    return [
        {
            "name": item.name,
            "unit": item.unit,
            "quantity": str(item.quantity),
        }
        for item in rollup.native_usage
    ]


def _native_from_json(value: object) -> tuple[NativeUsageQuantity, ...]:
    if not isinstance(value, list):
        raise ValueError("persisted report native_usage must be an array")
    output: list[NativeUsageQuantity] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("persisted report native usage item must be an object")
        output.append(
            NativeUsageQuantity(
                name=str(item["name"]),
                unit=str(item["unit"]),
                quantity=Decimal(str(item["quantity"])),
            )
        )
    return tuple(output)


def _values(rollup: ReportRollup) -> dict[str, object]:
    return {
        "rollup_id": rollup.rollup_id,
        "period_start": rollup.period_start,
        "period_end": rollup.period_end,
        "as_of": rollup.as_of,
        "tenant_id": rollup.tenant_id,
        "client_id": rollup.client_id,
        "client_credential_id": rollup.client_credential_id,
        "provider_id": rollup.provider_id,
        "provider_account_id": rollup.provider_account_id,
        "provider_credential_id": rollup.provider_credential_id,
        "session_id": rollup.session_id,
        "task_id": rollup.task_id,
        "attempt_id": rollup.attempt_id,
        "policy_version_id": rollup.policy_version_id,
        "model_id": rollup.model_id,
        "status": rollup.status,
        "error_class": rollup.error_class,
        "requests": rollup.requests,
        "tasks": rollup.tasks,
        "attempts": rollup.attempts,
        "peak_concurrent": rollup.peak_concurrent,
        "quota_utilization": rollup.quota_utilization,
        "input_tokens": rollup.input_tokens,
        "cached_input_tokens": rollup.cached_input_tokens,
        "output_tokens": rollup.output_tokens,
        "reasoning_tokens": rollup.reasoning_tokens,
        "total_tokens": rollup.total_tokens,
        "native_usage": _native_json(rollup),
        "latency_ms_total": rollup.latency_ms_total,
        "cycles": rollup.cycles,
        "retries": rollup.retries,
        "fallbacks": rollup.fallbacks,
        "health_events": rollup.health_events,
        "quarantine_events": rollup.quarantine_events,
        "estimated_cost": rollup.estimated_cost,
        "estimated_currency": rollup.estimated_currency,
        "observed_cost": rollup.observed_cost,
        "observed_currency": rollup.observed_currency,
        "unpriced_attempts": rollup.unpriced_attempts,
    }


def _from_row(row: RowMapping) -> ReportRollup:
    return ReportRollup(
        rollup_id=cast(UUID, row["rollup_id"]),
        period_start=row["period_start"],
        period_end=row["period_end"],
        as_of=row["as_of"],
        tenant_id=cast(UUID, row["tenant_id"]),
        client_id=cast(UUID, row["client_id"]),
        client_credential_id=cast(UUID | None, row["client_credential_id"]),
        provider_id=cast(str, row["provider_id"]),
        provider_account_id=cast(UUID | None, row["provider_account_id"]),
        provider_credential_id=cast(UUID | None, row["provider_credential_id"]),
        session_id=cast(UUID | None, row["session_id"]),
        task_id=cast(UUID | None, row["task_id"]),
        attempt_id=cast(UUID | None, row["attempt_id"]),
        policy_version_id=cast(UUID | None, row["policy_version_id"]),
        model_id=cast(str, row["model_id"]),
        status=cast(str, row["status"]),
        error_class=cast(str | None, row["error_class"]),
        requests=cast(int, row["requests"]),
        tasks=cast(int, row["tasks"]),
        attempts=cast(int, row["attempts"]),
        peak_concurrent=cast(int, row["peak_concurrent"]),
        quota_utilization=cast(Decimal | None, row["quota_utilization"]),
        input_tokens=cast(int, row["input_tokens"]),
        cached_input_tokens=cast(int, row["cached_input_tokens"]),
        output_tokens=cast(int, row["output_tokens"]),
        reasoning_tokens=cast(int, row["reasoning_tokens"]),
        total_tokens=cast(int, row["total_tokens"]),
        native_usage=_native_from_json(row["native_usage"]),
        latency_ms_total=cast(int, row["latency_ms_total"]),
        cycles=cast(int, row["cycles"]),
        retries=cast(int, row["retries"]),
        fallbacks=cast(int, row["fallbacks"]),
        health_events=cast(int, row["health_events"]),
        quarantine_events=cast(int, row["quarantine_events"]),
        estimated_cost=cast(Decimal | None, row["estimated_cost"]),
        estimated_currency=cast(str | None, row["estimated_currency"]),
        observed_cost=cast(Decimal | None, row["observed_cost"]),
        observed_currency=cast(str | None, row["observed_currency"]),
        unpriced_attempts=cast(int, row["unpriced_attempts"]),
    )


class PostgresReportRollupStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def put(self, rollup: ReportRollup) -> None:
        values = _values(rollup)
        statement = insert(report_rollups).values(**values).on_conflict_do_update(
            index_elements=[report_rollups.c.rollup_id],
            set_={
                key: value
                for key, value in values.items()
                if key != "rollup_id"
            },
        )
        async with self._sessions.begin() as database:
            await database.execute(statement)

    async def query(
        self,
        *,
        filters: ReportQuery,
        limit: int,
    ) -> tuple[ReportRollup, ...]:
        if limit < 1 or limit > REPORT_STORE_MAX_ROWS:
            raise ValueError("report query limit outside allowed range")
        conditions: list[ColumnElement[bool]] = []
        if filters.period_from is not None:
            conditions.append(report_rollups.c.period_end > filters.period_from)
        if filters.period_to is not None:
            conditions.append(report_rollups.c.period_start < filters.period_to)
        for field in (
            "tenant_id",
            "client_id",
            "client_credential_id",
            "provider_id",
            "provider_account_id",
            "provider_credential_id",
            "session_id",
            "task_id",
            "attempt_id",
            "policy_version_id",
            "model_id",
            "status",
            "error_class",
        ):
            value = getattr(filters, field)
            if value is not None:
                conditions.append(getattr(report_rollups.c, field) == value)

        statement = (
            sa.select(report_rollups)
            .where(*conditions)
            .order_by(report_rollups.c.period_start, report_rollups.c.rollup_id)
            .limit(limit)
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_from_row(row) for row in rows)
