"""PostgreSQL repository for administrative quota-policy versions."""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .quota_tables import quota_policies
from .quotas import (
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicySnapshot,
    QuotaScope,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _from_row(row: RowMapping) -> QuotaPolicySnapshot:
    return QuotaPolicySnapshot(
        policy_id=cast(UUID, row["policy_id"]),
        version=cast(int, row["version"]),
        scope=QuotaScope(cast(str, row["scope"])),
        tenant_id=cast(UUID, row["tenant_id"]),
        client_id=cast(UUID | None, row["client_id"]),
        metric=QuotaMetric(cast(str, row["metric"])),
        limit=Decimal(row["limit_amount"]),
        enforcement=QuotaEnforcementMode(cast(str, row["enforcement"])),
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        period_seconds=cast(int | None, row["period_seconds"]),
        burst=Decimal(row["burst_amount"]),
        reservation_ttl_seconds=cast(int, row["reservation_ttl_seconds"]),
        native_unit=cast(str | None, row["native_unit"]),
        provider_id=cast(str | None, row["provider_id"]),
    )


class PostgresQuotaPolicyRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def list_series(
        self,
        *,
        policy_id: UUID,
    ) -> tuple[QuotaPolicySnapshot, ...]:
        statement = (
            sa.select(quota_policies)
            .where(quota_policies.c.policy_id == policy_id)
            .order_by(quota_policies.c.version)
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_from_row(row) for row in rows)

    async def list_for_subject(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID | None,
    ) -> tuple[QuotaPolicySnapshot, ...]:
        condition = (
            quota_policies.c.client_id.is_(None)
            if client_id is None
            else quota_policies.c.client_id == client_id
        )
        statement = (
            sa.select(quota_policies)
            .where(
                quota_policies.c.tenant_id == tenant_id,
                condition,
            )
            .order_by(
                quota_policies.c.effective_from,
                quota_policies.c.policy_id,
                quota_policies.c.version,
            )
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_from_row(row) for row in rows)

    async def append(self, policy: QuotaPolicySnapshot) -> QuotaPolicySnapshot:
        statement = sa.insert(quota_policies).values(
            policy_id=policy.policy_id,
            version=policy.version,
            scope=policy.scope.value,
            tenant_id=policy.tenant_id,
            client_id=policy.client_id,
            metric=policy.metric.value,
            limit_amount=policy.limit,
            enforcement=policy.enforcement.value,
            period_seconds=policy.period_seconds,
            burst_amount=policy.burst,
            reservation_ttl_seconds=policy.reservation_ttl_seconds,
            native_unit=policy.native_unit,
            provider_id=policy.provider_id,
            effective_from=policy.effective_from,
            effective_to=policy.effective_to,
        )
        async with self._sessions.begin() as database:
            if policy.version > 1:
                prior = (
                    await database.execute(
                        sa.select(quota_policies.c.version)
                        .where(quota_policies.c.policy_id == policy.policy_id)
                        .order_by(quota_policies.c.version.desc())
                        .limit(1)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if prior != policy.version - 1:
                    raise ValueError("quota policy version conflict")
            await database.execute(statement)
        return policy
