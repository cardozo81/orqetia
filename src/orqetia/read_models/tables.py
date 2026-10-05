"""Generic rebuildable projection registry with typed isolation dimensions."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

readmodel_metadata = metadata_for_schema("readmodel")

projection_documents = sa.Table(
    "projection_documents",
    readmodel_metadata,
    sa.Column("projection_key", sa.Text(), primary_key=True),
    sa.Column("projection_id", UUID(as_uuid=True), nullable=False, unique=True),
    sa.Column("surface", sa.Text(), nullable=False),
    sa.Column("audience", sa.Text(), nullable=False),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    sa.Column("client_id", UUID(as_uuid=True), nullable=True),
    sa.Column("role_scope_key", sa.Text(), nullable=False),
    sa.Column("query_fingerprint", sa.Text(), nullable=False),
    sa.Column("projection_version", sa.BigInteger(), nullable=False),
    sa.Column("classification", sa.Text(), nullable=False),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source_watermark", sa.Text(), nullable=False),
    sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("payload", JSONB(), nullable=False),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "audience IN ('CLIENT','BACKOFFICE')",
        name="projection_audience_known",
    ),
    sa.CheckConstraint(
        "(audience = 'CLIENT' AND tenant_id IS NOT NULL AND client_id IS NOT NULL) "
        "OR audience = 'BACKOFFICE'",
        name="projection_client_owner_required",
    ),
    sa.CheckConstraint(
        "projection_version >= 1",
        name="projection_version_positive",
    ),
)

sa.Index(
    "ix_projection_documents_owner_surface",
    projection_documents.c.tenant_id,
    projection_documents.c.client_id,
    projection_documents.c.surface,
    projection_documents.c.as_of,
)
sa.Index(
    "ix_projection_documents_backoffice_surface",
    projection_documents.c.audience,
    projection_documents.c.surface,
    projection_documents.c.as_of,
)
