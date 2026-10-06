"""identity: add customer human identities and client memberships

Revision ID: 20261005_0022
Revises: 20261005_0021
Create Date: 2026-10-06

Ownership: Customer human authorization (#142).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0022"
down_revision: str | None = "20261005_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_identities",
        sa.Column("identity_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_customer_external_identity",
        ),
        sa.CheckConstraint(
            "issuer LIKE 'https://%' AND char_length(issuer) <= 500",
            name="ck_customer_identities_issuer_https",
        ),
        sa.CheckConstraint(
            "char_length(subject) BETWEEN 1 AND 500",
            name="ck_customer_identities_subject_bounded",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED')",
            name="ck_customer_identities_status_known",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_customer_identities_version_positive",
        ),
        schema="identity",
    )
    op.create_table(
        "customer_memberships",
        sa.Column("membership_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("roles", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("role_matrix_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identity.customer_identities.identity_id"],
            name="fk_customer_memberships_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["identity.tenants.tenant_id"],
            name="fk_customer_memberships_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["identity.service_clients.client_id"],
            name="fk_customer_memberships_client",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "identity_id",
            "tenant_id",
            "client_id",
            name="uq_customer_membership_owner",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(roles) = 'array' AND jsonb_array_length(roles) >= 1",
            name="ck_customer_memberships_roles_non_empty",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED')",
            name="ck_customer_memberships_status_known",
        ),
        sa.CheckConstraint(
            "role_matrix_version >= 1 AND version >= 1",
            name="ck_customer_memberships_versions_positive",
        ),
        schema="identity",
    )
    op.create_index(
        "ix_customer_memberships_identity_status",
        "customer_memberships",
        ["identity_id", "status"],
        schema="identity",
    )
    op.create_index(
        "ix_customer_memberships_owner",
        "customer_memberships",
        ["tenant_id", "client_id"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_memberships_owner",
        table_name="customer_memberships",
        schema="identity",
    )
    op.drop_index(
        "ix_customer_memberships_identity_status",
        table_name="customer_memberships",
        schema="identity",
    )
    op.drop_table("customer_memberships", schema="identity")
    op.drop_table("customer_identities", schema="identity")
