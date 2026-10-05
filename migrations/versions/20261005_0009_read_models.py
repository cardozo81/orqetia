"""readmodel: add secure rebuildable projection documents

Revision ID: 20261005_0009
Revises: 20261005_0008
Create Date: 2026-10-05

Ownership: Web Read Models bounded context (#45).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0009"
down_revision: str | None = "20261005_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projection_documents",
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
            name="ck_projection_documents_audience_known",
        ),
        sa.CheckConstraint(
            "(audience = 'CLIENT' AND tenant_id IS NOT NULL AND client_id IS NOT NULL) "
            "OR audience = 'BACKOFFICE'",
            name="ck_projection_documents_client_owner_required",
        ),
        sa.CheckConstraint(
            "projection_version >= 1",
            name="ck_projection_documents_version_positive",
        ),
        schema="readmodel",
    )
    op.create_index(
        "ix_projection_documents_owner_surface",
        "projection_documents",
        ["tenant_id", "client_id", "surface", "as_of"],
        schema="readmodel",
    )
    op.create_index(
        "ix_projection_documents_backoffice_surface",
        "projection_documents",
        ["audience", "surface", "as_of"],
        schema="readmodel",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_projection_documents_backoffice_surface",
        table_name="projection_documents",
        schema="readmodel",
    )
    op.drop_index(
        "ix_projection_documents_owner_surface",
        table_name="projection_documents",
        schema="readmodel",
    )
    op.drop_table("projection_documents", schema="readmodel")
