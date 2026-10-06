"""control: add immutable administrative provider pricing catalogs

Revision ID: 20261005_0021
Revises: 20261005_0020
Create Date: 2026-10-06

Ownership: Provider pricing administration (#141).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0021"
down_revision: str | None = "20261005_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_pricing_catalog_versions",
        sa.Column("catalog_version_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version_number", sa.Integer(), nullable=False, unique=True),
        sa.Column("rules", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_provider_pricing_catalog_versions_version_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rules) = 'array'",
            name="ck_provider_pricing_catalog_versions_rules_array",
        ),
        schema="control",
    )
    op.create_table(
        "provider_pricing_assignment",
        sa.Column("assignment_key", sa.Text(), primary_key=True),
        sa.Column("catalog_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_version", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_version_id"],
            ["control.provider_pricing_catalog_versions.catalog_version_id"],
            name="fk_provider_pricing_assignment_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "assignment_key = 'GLOBAL'",
            name="ck_provider_pricing_assignment_singleton",
        ),
        sa.CheckConstraint(
            "assignment_version >= 1",
            name="ck_provider_pricing_assignment_version_positive",
        ),
        schema="control",
    )


def downgrade() -> None:
    op.drop_table("provider_pricing_assignment", schema="control")
    op.drop_table("provider_pricing_catalog_versions", schema="control")
