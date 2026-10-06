"""identity: add Customer Portal web sessions

Revision ID: 20261006_0029
Revises: 20261006_0028
Create Date: 2026-10-06

Ownership: Customer Portal browser-session security boundary (#23).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261006_0029"
down_revision: str | None = "20261006_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_portal_web_sessions",
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mfa_satisfied", sa.Boolean(), nullable=False),
        sa.Column("amr", JSONB(), nullable=False),
        sa.Column("acr", sa.Text(), nullable=True),
        sa.Column("membership_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_customer_portal_web_sessions_token_hash",
        ),
        sa.CheckConstraint(
            "csrf_hash ~ '^[0-9a-f]{64}$'",
            name="ck_customer_portal_web_sessions_csrf_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(amr) = 'array'",
            name="ck_customer_portal_web_sessions_amr_array",
        ),
        sa.CheckConstraint(
            "(membership_id IS NULL AND tenant_id IS NULL AND client_id IS NULL) OR "
            "(membership_id IS NOT NULL AND tenant_id IS NOT NULL AND client_id IS NOT NULL)",
            name="ck_customer_portal_web_sessions_membership_shape",
        ),
        sa.CheckConstraint(
            "absolute_expires_at > created_at AND last_activity_at >= created_at",
            name="ck_customer_portal_web_sessions_time_order",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_customer_portal_web_sessions_version_positive",
        ),
        sa.PrimaryKeyConstraint(
            "session_id",
            name="pk_customer_portal_web_sessions",
        ),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_customer_portal_web_sessions_token_hash",
        ),
        schema="identity",
    )
    op.create_index(
        "ix_customer_portal_web_sessions_identity",
        "customer_portal_web_sessions",
        ["issuer", "subject"],
        schema="identity",
    )
    op.create_index(
        "ix_customer_portal_web_sessions_owner",
        "customer_portal_web_sessions",
        ["tenant_id", "client_id"],
        schema="identity",
    )
    op.create_index(
        "ix_customer_portal_web_sessions_expiry",
        "customer_portal_web_sessions",
        ["absolute_expires_at"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_portal_web_sessions_expiry",
        table_name="customer_portal_web_sessions",
        schema="identity",
    )
    op.drop_index(
        "ix_customer_portal_web_sessions_owner",
        table_name="customer_portal_web_sessions",
        schema="identity",
    )
    op.drop_index(
        "ix_customer_portal_web_sessions_identity",
        table_name="customer_portal_web_sessions",
        schema="identity",
    )
    op.drop_table("customer_portal_web_sessions", schema="identity")
