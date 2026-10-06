"""credentials: add provider credential metadata without secret material

Revision ID: 20261005_0010
Revises: 20261005_0009
Create Date: 2026-10-05

Ownership: Control Plane provider credential boundary (#25).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261005_0010"
down_revision: str | None = "20261005_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("credential_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("provider_account_id", UUID(as_uuid=True), nullable=False),
        sa.Column("secret_reference", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_use_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_use_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(provider_id) BETWEEN 1 AND 100",
            name="ck_provider_credentials_provider_bounded",
        ),
        sa.CheckConstraint(
            "secret_reference LIKE '%://%'",
            name="ck_provider_credentials_secret_reference_opaque",
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{16}$'",
            name="ck_provider_credentials_fingerprint_safe",
        ),
        sa.CheckConstraint(
            "key_version >= 1 AND state_version >= 1",
            name="ck_provider_credentials_versions_positive",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','REVOKED')",
            name="ck_provider_credentials_status_known",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL) "
            "OR (status = 'REVOKED' AND revoked_at IS NOT NULL)",
            name="ck_provider_credentials_revocation_consistent",
        ),
        schema="control",
    )
    op.create_index(
        "ix_provider_credentials_provider_status",
        "provider_credentials",
        ["provider_id", "status"],
        schema="control",
    )
    op.create_index(
        "ix_provider_credentials_account_status",
        "provider_credentials",
        ["provider_account_id", "status"],
        schema="control",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_credentials_account_status",
        table_name="provider_credentials",
        schema="control",
    )
    op.drop_index(
        "ix_provider_credentials_provider_status",
        table_name="provider_credentials",
        schema="control",
    )
    op.drop_table("provider_credentials", schema="control")
