"""PostgreSQL store for hashed client credentials and mutation idempotency."""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid7

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .client_credential_tables import (
    client_access_credentials,
    client_credential_operations,
)
from .client_credentials import (
    ClientAccessCredential,
    ClientCredentialStatus,
    CredentialOperation,
    IdempotencyConflict,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _values(credential: ClientAccessCredential) -> dict[str, object]:
    return {
        "credential_id": credential.credential_id,
        "tenant_id": credential.tenant_id,
        "client_id": credential.client_id,
        "display_label": credential.display_label,
        "scopes": list(credential.scopes),
        "secret_salt": credential.secret_salt,
        "secret_hash": credential.secret_hash,
        "fingerprint": credential.fingerprint,
        "key_version": credential.key_version,
        "state_version": credential.state_version,
        "status": credential.status.value,
        "created_at": credential.created_at,
        "rotated_at": credential.rotated_at,
        "revoked_at": credential.revoked_at,
        "expires_at": credential.expires_at,
        "last_used_at": credential.last_used_at,
    }


def _from_row(row: RowMapping) -> ClientAccessCredential:
    scopes_raw = row["scopes"]
    if not isinstance(scopes_raw, list):
        raise ValueError("persisted client credential scopes must be an array")
    return ClientAccessCredential(
        credential_id=cast(UUID, row["credential_id"]),
        tenant_id=cast(UUID, row["tenant_id"]),
        client_id=cast(UUID, row["client_id"]),
        display_label=cast(str, row["display_label"]),
        scopes=tuple(str(value) for value in scopes_raw),
        secret_salt=cast(str, row["secret_salt"]),
        secret_hash=cast(str, row["secret_hash"]),
        fingerprint=cast(str, row["fingerprint"]),
        key_version=cast(int, row["key_version"]),
        state_version=cast(int, row["state_version"]),
        status=ClientCredentialStatus(cast(str, row["status"])),
        created_at=row["created_at"],
        rotated_at=row["rotated_at"],
        revoked_at=row["revoked_at"],
        expires_at=row["expires_at"],
        last_used_at=row["last_used_at"],
    )


class PostgresClientCredentialStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def issue(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at,
    ) -> tuple[ClientAccessCredential, bool]:
        async with self._sessions.begin() as database:
            replay_id = await _claim_operation(
                database,
                credential=credential,
                operation=CredentialOperation.ISSUE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
                occurred_at=occurred_at,
            )
            if replay_id is not None:
                return await _get_required(database, replay_id), True
            await database.execute(
                sa.insert(client_access_credentials).values(**_values(credential))
            )
        return credential, False

    async def rotate(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at,
    ) -> tuple[ClientAccessCredential, bool]:
        async with self._sessions.begin() as database:
            replay_id = await _claim_operation(
                database,
                credential=credential,
                operation=CredentialOperation.ROTATE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
                occurred_at=occurred_at,
            )
            if replay_id is not None:
                return await _get_required(database, replay_id), True
            current = await _get_owned_for_update(
                database,
                credential_id=credential.credential_id,
                tenant_id=credential.tenant_id,
                client_id=credential.client_id,
            )
            if current.status is not ClientCredentialStatus.ACTIVE:
                raise ValueError("client credential is not active")
            if current.state_version + 1 != credential.state_version:
                raise ValueError("client credential version conflict")
            await _replace(database, credential)
        return credential, False

    async def revoke(
        self,
        *,
        credential: ClientAccessCredential,
        key_hash: str,
        request_fingerprint: str,
        occurred_at,
    ) -> tuple[ClientAccessCredential, bool]:
        async with self._sessions.begin() as database:
            replay_id = await _claim_operation(
                database,
                credential=credential,
                operation=CredentialOperation.REVOKE,
                key_hash=key_hash,
                request_fingerprint=request_fingerprint,
                occurred_at=occurred_at,
            )
            if replay_id is not None:
                return await _get_required(database, replay_id), True
            current = await _get_owned_for_update(
                database,
                credential_id=credential.credential_id,
                tenant_id=credential.tenant_id,
                client_id=credential.client_id,
            )
            if current.status is ClientCredentialStatus.REVOKED:
                return current, False
            if current.state_version + 1 != credential.state_version:
                raise ValueError("client credential version conflict")
            await _replace(database, credential)
        return credential, False

    async def get(self, credential_id: UUID) -> ClientAccessCredential | None:
        statement = sa.select(client_access_credentials).where(
            client_access_credentials.c.credential_id == credential_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _from_row(row)

    async def list_owned(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> tuple[ClientAccessCredential, ...]:
        statement = (
            sa.select(client_access_credentials)
            .where(
                client_access_credentials.c.tenant_id == tenant_id,
                client_access_credentials.c.client_id == client_id,
            )
            .order_by(
                client_access_credentials.c.created_at,
                client_access_credentials.c.credential_id,
            )
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_from_row(row) for row in rows)

    async def record_last_used(
        self,
        *,
        credential_id: UUID,
        occurred_at,
    ) -> None:
        statement = (
            sa.update(client_access_credentials)
            .where(client_access_credentials.c.credential_id == credential_id)
            .values(
                last_used_at=sa.case(
                    (
                        client_access_credentials.c.last_used_at.is_(None),
                        occurred_at,
                    ),
                    (
                        client_access_credentials.c.last_used_at < occurred_at,
                        occurred_at,
                    ),
                    else_=client_access_credentials.c.last_used_at,
                )
            )
            .returning(client_access_credentials.c.credential_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise LookupError("client credential not found")


async def _claim_operation(
    database: AsyncSession,
    *,
    credential: ClientAccessCredential,
    operation: CredentialOperation,
    key_hash: str,
    request_fingerprint: str,
    occurred_at,
) -> UUID | None:
    statement = (
        insert(client_credential_operations)
        .values(
            operation_id=uuid7(),
            tenant_id=credential.tenant_id,
            client_id=credential.client_id,
            operation=operation.value,
            key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            credential_id=credential.credential_id,
            created_at=occurred_at,
        )
        .on_conflict_do_nothing(
            index_elements=[
                client_credential_operations.c.tenant_id,
                client_credential_operations.c.client_id,
                client_credential_operations.c.operation,
                client_credential_operations.c.key_hash,
            ]
        )
        .returning(client_credential_operations.c.operation_id)
    )
    inserted = (await database.execute(statement)).scalar_one_or_none()
    if inserted is not None:
        return None

    row = (
        await database.execute(
            sa.select(
                client_credential_operations.c.request_fingerprint,
                client_credential_operations.c.credential_id,
            ).where(
                client_credential_operations.c.tenant_id == credential.tenant_id,
                client_credential_operations.c.client_id == credential.client_id,
                client_credential_operations.c.operation == operation.value,
                client_credential_operations.c.key_hash == key_hash,
            )
        )
    ).mappings().one()
    if row["request_fingerprint"] != request_fingerprint:
        raise IdempotencyConflict(
            "idempotency key reused with different credential request"
        )
    return cast(UUID, row["credential_id"])


async def _get_required(
    database: AsyncSession,
    credential_id: UUID,
) -> ClientAccessCredential:
    row = (
        await database.execute(
            sa.select(client_access_credentials).where(
                client_access_credentials.c.credential_id == credential_id
            )
        )
    ).mappings().one_or_none()
    if row is None:
        raise RuntimeError("credential operation references missing credential")
    return _from_row(row)


async def _get_owned_for_update(
    database: AsyncSession,
    *,
    credential_id: UUID,
    tenant_id: UUID,
    client_id: UUID,
) -> ClientAccessCredential:
    row = (
        await database.execute(
            sa.select(client_access_credentials)
            .where(
                client_access_credentials.c.credential_id == credential_id,
                client_access_credentials.c.tenant_id == tenant_id,
                client_access_credentials.c.client_id == client_id,
            )
            .with_for_update()
        )
    ).mappings().one_or_none()
    if row is None:
        raise LookupError("owned client credential not found")
    return _from_row(row)


async def _replace(
    database: AsyncSession,
    credential: ClientAccessCredential,
) -> None:
    values = _values(credential)
    values.pop("credential_id")
    statement = (
        sa.update(client_access_credentials)
        .where(
            client_access_credentials.c.credential_id == credential.credential_id,
            client_access_credentials.c.state_version
            == credential.state_version - 1,
        )
        .values(**values)
        .returning(client_access_credentials.c.credential_id)
    )
    changed = (await database.execute(statement)).scalar_one_or_none()
    if changed is None:
        raise ValueError("client credential version conflict")
