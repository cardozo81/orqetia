"""control: add immutable administrative provider catalog versions

Revision ID: 20261005_0020
Revises: 20261005_0019
Create Date: 2026-10-05

Ownership: Provider administrative catalog (#140).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0020"
down_revision: str | None = "20261005_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_catalog_versions",
        sa.Column("catalog_version_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version_number", sa.Integer(), nullable=False, unique=True),
        sa.Column("catalog", JSONB(), nullable=False),
        sa.Column("endpoints", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_provider_catalog_versions_version_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(catalog) = 'array'",
            name="ck_provider_catalog_versions_catalog_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(endpoints) = 'array'",
            name="ck_provider_catalog_versions_endpoints_array",
        ),
        schema="control",
    )
    op.create_table(
        "provider_catalog_assignment",
        sa.Column("assignment_key", sa.Text(), primary_key=True),
        sa.Column("catalog_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_version", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_version_id"],
            ["control.provider_catalog_versions.catalog_version_id"],
            name="fk_provider_catalog_assignment_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "assignment_key = 'GLOBAL'",
            name="ck_provider_catalog_assignment_singleton",
        ),
        sa.CheckConstraint(
            "assignment_version >= 1",
            name="ck_provider_catalog_assignment_version_positive",
        ),
        schema="control",
    )


def downgrade() -> None:
    op.drop_table("provider_catalog_assignment", schema="control")
    op.drop_table("provider_catalog_versions", schema="control")
