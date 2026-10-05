"""PostgreSQL persistence for rebuildable read-model documents."""

from __future__ import annotations

from typing import Protocol, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .domain import (
    ProjectionFreshness,
    ProjectionIdentity,
    ReadAudience,
    ReadClassification,
    ReadModelDocument,
    ReadSurface,
)
from .tables import projection_documents

SessionFactory = async_sessionmaker[AsyncSession]


class ReadModelStore(Protocol):
    async def put(self, document: ReadModelDocument) -> ReadModelDocument: ...

    async def get(self, identity: ProjectionIdentity) -> ReadModelDocument | None: ...


class PostgresReadModelStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def put(self, document: ReadModelDocument) -> ReadModelDocument:
        identity = document.identity
        values = {
            "projection_key": identity.cache_partition_key,
            "projection_id": document.projection_id,
            "surface": identity.surface.value,
            "audience": identity.audience.value,
            "tenant_id": identity.tenant_id,
            "client_id": identity.client_id,
            "role_scope_key": ",".join(identity.role_scope),
            "query_fingerprint": identity.query_fingerprint,
            "projection_version": document.version,
            "classification": document.classification.value,
            "as_of": document.freshness.as_of,
            "source_watermark": document.freshness.source_watermark,
            "refreshed_at": document.freshness.refreshed_at,
            "payload": dict(document.payload),
        }
        statement = (
            insert(projection_documents)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[projection_documents.c.projection_key],
                set_={key: value for key, value in values.items() if key != "projection_key"},
                where=(
                    projection_documents.c.projection_version
                    < document.version
                ),
            )
            .returning(projection_documents.c.projection_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
            if changed is not None:
                return document
            row = (
                await database.execute(
                    sa.select(projection_documents).where(
                        projection_documents.c.projection_key
                        == identity.cache_partition_key
                    )
                )
            ).mappings().one()
            existing = _from_row(row)
            if existing == document:
                return existing
            if existing.version >= document.version:
                raise ValueError("projection version must advance for changed content")
            raise RuntimeError("projection update failed unexpectedly")

    async def get(self, identity: ProjectionIdentity) -> ReadModelDocument | None:
        statement = sa.select(projection_documents).where(
            projection_documents.c.projection_key == identity.cache_partition_key
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _from_row(row)


def _from_row(row: sa.RowMapping) -> ReadModelDocument:
    roles = tuple(filter(None, cast(str, row["role_scope_key"]).split(",")))
    identity = ProjectionIdentity(
        surface=ReadSurface(cast(str, row["surface"])),
        audience=ReadAudience(cast(str, row["audience"])),
        tenant_id=row["tenant_id"],
        client_id=row["client_id"],
        role_scope=roles,
        query_fingerprint=cast(str, row["query_fingerprint"]),
    )
    return ReadModelDocument(
        projection_id=row["projection_id"],
        identity=identity,
        version=cast(int, row["projection_version"]),
        classification=ReadClassification(cast(str, row["classification"])),
        freshness=ProjectionFreshness(
            as_of=row["as_of"],
            source_watermark=cast(str, row["source_watermark"]),
            refreshed_at=row["refreshed_at"],
        ),
        payload=cast(dict[str, object], row["payload"]),
    )
