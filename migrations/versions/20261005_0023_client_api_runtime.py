"""execution: add client API idempotency journal

Revision ID: 20261005_0023
Revises: 20261005_0022
Create Date: 2026-10-06

Ownership: client API runtime (#143).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261005_0023"
down_revision: str | None = "20261005_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_api_idempotency",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "client_id",
            "operation",
            "key_hash",
            name="pk_client_api_idempotency",
        ),
        sa.CheckConstraint(
            "char_length(operation) BETWEEN 1 AND 100",
            name="ck_client_api_idempotency_operation_bounded",
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_client_api_idempotency_key_hash_sha256",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_client_api_idempotency_request_fingerprint_sha256",
        ),
        schema="execution",
    )
    op.create_index(
        "ix_client_api_idempotency_resource",
        "client_api_idempotency",
        ["tenant_id", "client_id", "resource_id"],
        schema="execution",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_client_api_idempotency_resource",
        table_name="client_api_idempotency",
        schema="execution",
    )
    op.drop_table("client_api_idempotency", schema="execution")
