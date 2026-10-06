"""PostgreSQL repository for provider credential metadata."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .provider_credential_tables import provider_credentials
from .provider_credentials import (
    ProviderCredentialMetadata,
    ProviderCredentialStatus,
    SecretReference,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _values(metadata: ProviderCredentialMetadata) -> dict[str, object]:
    return {
        "credential_id": metadata.credential_id,
        "provider_id": metadata.provider_id,
        "provider_account_id": metadata.provider_account_id,
        "secret_reference": metadata.secret_reference.value,
        "fingerprint": metadata.fingerprint,
        "key_version": metadata.key_version,
        "state_version": metadata.state_version,
        "status": metadata.status.value,
        "created_at": metadata.created_at,
        "rotated_at": metadata.rotated_at,
        "revoked_at": metadata.revoked_at,
        "expires_at": metadata.expires_at,
        "last_successful_use_at": metadata.last_successful_use_at,
        "last_failed_use_at": metadata.last_failed_use_at,
    }


def _from_row(row: RowMapping) -> ProviderCredentialMetadata:
    return ProviderCredentialMetadata(
        credential_id=cast(UUID, row["credential_id"]),
        provider_id=cast(str, row["provider_id"]),
        provider_account_id=cast(UUID, row["provider_account_id"]),
        secret_reference=SecretReference(cast(str, row["secret_reference"])),
        fingerprint=cast(str, row["fingerprint"]),
        key_version=cast(int, row["key_version"]),
        state_version=cast(int, row["state_version"]),
        status=ProviderCredentialStatus(cast(str, row["status"])),
        created_at=row["created_at"],
        rotated_at=row["rotated_at"],
        revoked_at=row["revoked_at"],
        expires_at=row["expires_at"],
        last_successful_use_at=row["last_successful_use_at"],
        last_failed_use_at=row["last_failed_use_at"],
    )


class PostgresProviderCredentialRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def create(
        self,
        metadata: ProviderCredentialMetadata,
    ) -> ProviderCredentialMetadata:
        async with self._sessions.begin() as database:
            await database.execute(sa.insert(provider_credentials).values(**_values(metadata)))
        return metadata

    async def get(self, credential_id: UUID) -> ProviderCredentialMetadata | None:
        statement = sa.select(provider_credentials).where(
            provider_credentials.c.credential_id == credential_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _from_row(row)

    async def list_active(
        self,
        *,
        provider_account_id: UUID,
    ) -> tuple[ProviderCredentialMetadata, ...]:
        statement = (
            sa.select(provider_credentials)
            .where(
                provider_credentials.c.provider_account_id == provider_account_id,
                provider_credentials.c.status == ProviderCredentialStatus.ACTIVE.value,
            )
            .order_by(
                provider_credentials.c.created_at,
                provider_credentials.c.credential_id,
            )
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_from_row(row) for row in rows)

    async def replace(
        self,
        metadata: ProviderCredentialMetadata,
        *,
        expected_state_version: int,
    ) -> ProviderCredentialMetadata:
        values = _values(metadata)
        values.pop("credential_id")
        statement = (
            sa.update(provider_credentials)
            .where(
                provider_credentials.c.credential_id == metadata.credential_id,
                provider_credentials.c.state_version == expected_state_version,
            )
            .values(**values)
            .returning(provider_credentials.c.credential_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise ValueError("provider credential version conflict or missing row")
        return metadata
