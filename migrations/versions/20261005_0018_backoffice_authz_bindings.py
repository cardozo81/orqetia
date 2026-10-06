"""identity: add Backoffice external identity authorization bindings

Revision ID: 20261005_0018
Revises: 20261005_0017
Create Date: 2026-10-05

Ownership: Identity authorization bindings (#138).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0018"
down_revision: str | None = "20261005_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backoffice_user_bindings",
        sa.Column("binding_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("roles", JSONB(), nullable=False),
        sa.Column("role_matrix_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_backoffice_external_identity",
        ),
        sa.CheckConstraint(
            "issuer LIKE 'https://%' AND char_length(issuer) <= 500",
            name="ck_backoffice_user_bindings_issuer_https",
        ),
        sa.CheckConstraint(
            "char_length(subject) BETWEEN 1 AND 500",
            name="ck_backoffice_user_bindings_subject_bounded",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED')",
            name="ck_backoffice_user_bindings_status_known",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(roles) = 'array' AND jsonb_array_length(roles) >= 1",
            name="ck_backoffice_user_bindings_roles_non_empty",
        ),
        sa.CheckConstraint(
            "role_matrix_version >= 1 AND version >= 1",
            name="ck_backoffice_user_bindings_versions_positive",
        ),
        schema="identity",
    )
    op.create_index(
        "ix_backoffice_user_bindings_status",
        "backoffice_user_bindings",
        ["status"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_backoffice_user_bindings_status",
        table_name="backoffice_user_bindings",
        schema="identity",
    )
    op.drop_table("backoffice_user_bindings", schema="identity")
