"""PostgreSQL stores for provider accounts and external capacity snapshots."""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .provider_account_tables import provider_accounts, provider_capacity_snapshots
from .provider_accounts import (
    ExternalCapacitySnapshot,
    ExternalCapacitySource,
    ProviderAccount,
    ProviderAccountStatus,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _account_values(account: ProviderAccount) -> dict[str, object]:
    return {
        "provider_account_id": account.provider_account_id,
        "provider_id": account.provider_id,
        "display_label": account.display_label,
        "status": account.status.value,
        "priority": account.priority,
        "commercial_mode": account.commercial_mode,
        "commercial_tier": account.commercial_tier,
        "region": account.region,
        "contract_reference": account.contract_reference,
        "state_version": account.state_version,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


def _account_from_row(row: RowMapping) -> ProviderAccount:
    return ProviderAccount(
        provider_account_id=cast(UUID, row["provider_account_id"]),
        provider_id=cast(str, row["provider_id"]),
        display_label=cast(str, row["display_label"]),
        status=ProviderAccountStatus(cast(str, row["status"])),
        priority=cast(int, row["priority"]),
        commercial_mode=cast(str | None, row["commercial_mode"]),
        commercial_tier=cast(str | None, row["commercial_tier"]),
        region=cast(str | None, row["region"]),
        contract_reference=cast(str | None, row["contract_reference"]),
        state_version=cast(int, row["state_version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _capacity_from_row(row: RowMapping) -> ExternalCapacitySnapshot:
    return ExternalCapacitySnapshot(
        snapshot_id=cast(UUID, row["snapshot_id"]),
        provider_account_id=cast(UUID, row["provider_account_id"]),
        provider_id=cast(str, row["provider_id"]),
        native_unit=cast(str, row["native_unit"]),
        source=ExternalCapacitySource(cast(str, row["source"])),
        source_reference=cast(str, row["source_reference"]),
        remaining=cast(Decimal | None, row["remaining"]),
        limit=cast(Decimal | None, row["limit_amount"]),
        observed_at=row["observed_at"],
        reset_at=row["reset_at"],
    )


class PostgresProviderAccountRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def create(self, account: ProviderAccount) -> ProviderAccount:
        async with self._sessions.begin() as database:
            await database.execute(sa.insert(provider_accounts).values(**_account_values(account)))
        return account

    async def get(self, provider_account_id: UUID) -> ProviderAccount | None:
        statement = sa.select(provider_accounts).where(
            provider_accounts.c.provider_account_id == provider_account_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _account_from_row(row)

    async def replace(
        self,
        account: ProviderAccount,
        *,
        expected_state_version: int,
    ) -> ProviderAccount:
        values = _account_values(account)
        values.pop("provider_account_id")
        statement = (
            sa.update(provider_accounts)
            .where(
                provider_accounts.c.provider_account_id == account.provider_account_id,
                provider_accounts.c.state_version == expected_state_version,
            )
            .values(**values)
            .returning(provider_accounts.c.provider_account_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise ValueError("provider account version conflict or missing row")
        return account

    async def list_for_provider(self, provider_id: str) -> tuple[ProviderAccount, ...]:
        statement = (
            sa.select(provider_accounts)
            .where(provider_accounts.c.provider_id == provider_id)
            .order_by(
                provider_accounts.c.priority,
                provider_accounts.c.created_at,
                provider_accounts.c.provider_account_id,
            )
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_account_from_row(row) for row in rows)


class PostgresExternalCapacityRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def append(
        self,
        snapshot: ExternalCapacitySnapshot,
    ) -> ExternalCapacitySnapshot:
        statement = sa.insert(provider_capacity_snapshots).values(
            snapshot_id=snapshot.snapshot_id,
            provider_account_id=snapshot.provider_account_id,
            provider_id=snapshot.provider_id,
            native_unit=snapshot.native_unit,
            source=snapshot.source.value,
            source_reference=snapshot.source_reference,
            remaining=snapshot.remaining,
            limit_amount=snapshot.limit,
            observed_at=snapshot.observed_at,
            reset_at=snapshot.reset_at,
        )
        async with self._sessions.begin() as database:
            await database.execute(statement)
        return snapshot

    async def latest(
        self,
        *,
        provider_account_id: UUID,
        native_unit: str,
    ) -> ExternalCapacitySnapshot | None:
        statement = (
            sa.select(provider_capacity_snapshots)
            .where(
                provider_capacity_snapshots.c.provider_account_id == provider_account_id,
                provider_capacity_snapshots.c.native_unit == native_unit,
            )
            .order_by(
                provider_capacity_snapshots.c.observed_at.desc(),
                provider_capacity_snapshots.c.snapshot_id.desc(),
            )
            .limit(1)
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _capacity_from_row(row)
