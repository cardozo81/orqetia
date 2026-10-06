"""identity: add tenant/client lifecycle source of truth

Revision ID: 20261005_0016
Revises: 20261005_0015
Create Date: 2026-10-05

Ownership: Identity & Tenancy administrative lifecycle (#136).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261005_0016"
down_revision: str | None = "20261005_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 200",
            name="ck_tenants_display_name_bounded",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED')",
            name="ck_tenants_status_known",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_tenants_version_positive",
        ),
        schema="identity",
    )

    op.create_table(
        "service_clients",
        sa.Column("client_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["identity.tenants.tenant_id"],
            name="fk_service_clients_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 200",
            name="ck_service_clients_display_name_bounded",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED')",
            name="ck_service_clients_status_known",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_service_clients_version_positive",
        ),
        schema="identity",
    )
    op.create_index(
        "ix_service_clients_tenant_status",
        "service_clients",
        ["tenant_id", "status"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_service_clients_tenant_status",
        table_name="service_clients",
        schema="identity",
    )
    op.drop_table("service_clients", schema="identity")
    op.drop_table("tenants", schema="identity")
