"""identity: add hashed client access credentials

Revision ID: 20261005_0013
Revises: 20261005_0012
Create Date: 2026-10-05

Ownership: Identity & Tenancy client credential lifecycle (#24).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0013"
down_revision: str | None = "20261005_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_access_credentials",
        sa.Column("credential_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("display_label", sa.Text(), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False),
        sa.Column("secret_salt", sa.String(32), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(display_label) BETWEEN 1 AND 200",
            name="ck_client_access_credentials_label_bounded",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) >= 1",
            name="ck_client_access_credentials_scopes_non_empty",
        ),
        sa.CheckConstraint(
            "secret_salt ~ '^[0-9a-f]{32}$'",
            name="ck_client_access_credentials_salt_hex",
        ),
        sa.CheckConstraint(
            "secret_hash ~ '^[0-9a-f]{64}$'",
            name="ck_client_access_credentials_hash_hex",
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{16}$'",
            name="ck_client_access_credentials_fingerprint_hex",
        ),
        sa.CheckConstraint(
            "key_version >= 1 AND state_version >= 1",
            name="ck_client_access_credentials_versions_positive",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','REVOKED')",
            name="ck_client_access_credentials_status_known",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL) "
            "OR (status = 'REVOKED' AND revoked_at IS NOT NULL)",
            name="ck_client_access_credentials_revocation_consistent",
        ),
        schema="identity",
    )
    op.create_index(
        "ix_client_access_credentials_owner_status",
        "client_access_credentials",
        ["tenant_id", "client_id", "status"],
        schema="identity",
    )
    op.create_index(
        "ix_client_access_credentials_fingerprint",
        "client_access_credentials",
        ["fingerprint"],
        schema="identity",
    )

    op.create_table(
        "client_credential_operations",
        sa.Column("operation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("credential_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "client_id",
            "operation",
            "key_hash",
            name="uq_client_credential_operation_key",
        ),
        sa.CheckConstraint(
            "operation IN ('ISSUE','ROTATE','REVOKE')",
            name="ck_client_credential_operations_operation_known",
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_client_credential_operations_key_hash",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_client_credential_operations_request_hash",
        ),
        schema="identity",
    )


def downgrade() -> None:
    op.drop_table("client_credential_operations", schema="identity")
    op.drop_index(
        "ix_client_access_credentials_fingerprint",
        table_name="client_access_credentials",
        schema="identity",
    )
    op.drop_index(
        "ix_client_access_credentials_owner_status",
        table_name="client_access_credentials",
        schema="identity",
    )
    op.drop_table("client_access_credentials", schema="identity")
