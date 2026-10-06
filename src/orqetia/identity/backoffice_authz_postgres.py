"""PostgreSQL repository for Backoffice external identity bindings."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .backoffice_authz import (
    BackofficeBindingStatus,
    BackofficeRole,
    BackofficeUserBinding,
)
from .backoffice_authz_tables import backoffice_user_bindings

SessionFactory = async_sessionmaker[AsyncSession]


def _values(binding: BackofficeUserBinding) -> dict[str, object]:
    return {
        "binding_id": binding.binding_id,
        "issuer": binding.issuer,
        "subject": binding.subject,
        "status": binding.status.value,
        "roles": [role.value for role in binding.roles],
        "role_matrix_version": binding.role_matrix_version,
        "created_at": binding.created_at,
        "updated_at": binding.updated_at,
        "version": binding.version,
    }


def _from_row(row: RowMapping) -> BackofficeUserBinding:
    roles_raw = row["roles"]
    if not isinstance(roles_raw, list):
        raise ValueError("persisted Backoffice roles must be an array")
    return BackofficeUserBinding(
        binding_id=cast(UUID, row["binding_id"]),
        issuer=cast(str, row["issuer"]),
        subject=cast(str, row["subject"]),
        status=BackofficeBindingStatus(cast(str, row["status"])),
        roles=tuple(BackofficeRole(str(value)) for value in roles_raw),
        role_matrix_version=cast(int, row["role_matrix_version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=cast(int, row["version"]),
    )


class PostgresBackofficeBindingRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def create(
        self,
        binding: BackofficeUserBinding,
    ) -> BackofficeUserBinding:
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(backoffice_user_bindings).values(**_values(binding))
            )
        return binding

    async def get_by_identity(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> BackofficeUserBinding | None:
        statement = sa.select(backoffice_user_bindings).where(
            backoffice_user_bindings.c.issuer == issuer.strip(),
            backoffice_user_bindings.c.subject == subject.strip(),
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _from_row(row)

    async def get(self, binding_id: UUID) -> BackofficeUserBinding | None:
        statement = sa.select(backoffice_user_bindings).where(
            backoffice_user_bindings.c.binding_id == binding_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _from_row(row)

    async def replace(
        self,
        binding: BackofficeUserBinding,
        *,
        expected_version: int,
    ) -> BackofficeUserBinding:
        values = _values(binding)
        values.pop("binding_id")
        values.pop("issuer")
        values.pop("subject")
        statement = (
            sa.update(backoffice_user_bindings)
            .where(
                backoffice_user_bindings.c.binding_id == binding.binding_id,
                backoffice_user_bindings.c.issuer == binding.issuer,
                backoffice_user_bindings.c.subject == binding.subject,
                backoffice_user_bindings.c.version == expected_version,
            )
            .values(**values)
            .returning(backoffice_user_bindings.c.binding_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise ValueError("Backoffice binding version conflict")
        return binding
