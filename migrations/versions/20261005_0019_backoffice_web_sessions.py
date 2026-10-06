"""identity: add server-side Backoffice web sessions

Revision ID: 20261005_0019
Revises: 20261005_0018
Create Date: 2026-10-05

Ownership: Backoffice web/session boundary (#22).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0019"
down_revision: str | None = "20261005_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backoffice_web_sessions",
        sa.Column("session_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mfa_satisfied", sa.Boolean(), nullable=False),
        sa.Column("amr", JSONB(), nullable=False),
        sa.Column("acr", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_backoffice_web_sessions_token_hash",
        ),
        sa.CheckConstraint(
            "csrf_hash ~ '^[0-9a-f]{64}$'",
            name="ck_backoffice_web_sessions_csrf_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(amr) = 'array'",
            name="ck_backoffice_web_sessions_amr_array",
        ),
        sa.CheckConstraint(
            "absolute_expires_at > created_at AND last_activity_at >= created_at",
            name="ck_backoffice_web_sessions_time_order",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_backoffice_web_sessions_version_positive",
        ),
        schema="identity",
    )
    op.create_index(
        "ix_backoffice_web_sessions_identity",
        "backoffice_web_sessions",
        ["issuer", "subject"],
        schema="identity",
    )
    op.create_index(
        "ix_backoffice_web_sessions_expiry",
        "backoffice_web_sessions",
        ["absolute_expires_at"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_backoffice_web_sessions_expiry",
        table_name="backoffice_web_sessions",
        schema="identity",
    )
    op.drop_index(
        "ix_backoffice_web_sessions_identity",
        table_name="backoffice_web_sessions",
        schema="identity",
    )
    op.drop_table("backoffice_web_sessions", schema="identity")
