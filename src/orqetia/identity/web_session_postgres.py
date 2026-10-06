"""PostgreSQL store for server-side Backoffice web sessions."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .web_session_tables import backoffice_web_sessions
from .web_sessions import BackofficeWebSession

SessionFactory = async_sessionmaker[AsyncSession]


def _values(session: BackofficeWebSession) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "token_hash": session.token_hash,
        "csrf_hash": session.csrf_hash,
        "issuer": session.issuer,
        "subject": session.subject,
        "authenticated_at": session.authenticated_at,
        "mfa_satisfied": session.mfa_satisfied,
        "amr": list(session.amr),
        "acr": session.acr,
        "created_at": session.created_at,
        "last_activity_at": session.last_activity_at,
        "absolute_expires_at": session.absolute_expires_at,
        "revoked_at": session.revoked_at,
        "version": session.version,
    }


def _from_row(row: RowMapping) -> BackofficeWebSession:
    amr_raw = row["amr"]
    if not isinstance(amr_raw, list):
        raise ValueError("persisted session AMR must be an array")
    return BackofficeWebSession(
        session_id=cast(UUID, row["session_id"]),
        token_hash=cast(str, row["token_hash"]),
        csrf_hash=cast(str, row["csrf_hash"]),
        issuer=cast(str, row["issuer"]),
        subject=cast(str, row["subject"]),
        authenticated_at=row["authenticated_at"],
        mfa_satisfied=cast(bool, row["mfa_satisfied"]),
        amr=tuple(str(value) for value in amr_raw),
        acr=cast(str | None, row["acr"]),
        created_at=row["created_at"],
        last_activity_at=row["last_activity_at"],
        absolute_expires_at=row["absolute_expires_at"],
        revoked_at=row["revoked_at"],
        version=cast(int, row["version"]),
    )


class PostgresBackofficeWebSessionStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def create(self, session: BackofficeWebSession) -> BackofficeWebSession:
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(backoffice_web_sessions).values(**_values(session))
            )
        return session

    async def get_by_token_hash(
        self,
        token_hash: str,
    ) -> BackofficeWebSession | None:
        statement = sa.select(backoffice_web_sessions).where(
            backoffice_web_sessions.c.token_hash == token_hash
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _from_row(row)

    async def replace(
        self,
        session: BackofficeWebSession,
        *,
        expected_version: int,
    ) -> BackofficeWebSession:
        values = _values(session)
        values.pop("session_id")
        values.pop("token_hash")
        statement = (
            sa.update(backoffice_web_sessions)
            .where(
                backoffice_web_sessions.c.session_id == session.session_id,
                backoffice_web_sessions.c.token_hash == session.token_hash,
                backoffice_web_sessions.c.version == expected_version,
            )
            .values(**values)
            .returning(backoffice_web_sessions.c.session_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise ValueError("web session version conflict")
        return session
