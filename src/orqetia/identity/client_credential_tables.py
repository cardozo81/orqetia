"""Identity-owned hashed client access credentials and idempotency records."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

client_credential_metadata = metadata_for_schema("identity")

client_access_credentials = sa.Table(
    "client_access_credentials",
    client_credential_metadata,
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
        name="client_credential_label_bounded",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) >= 1",
        name="client_credential_scopes_non_empty",
    ),
    sa.CheckConstraint(
        "secret_salt ~ '^[0-9a-f]{32}$'",
        name="client_credential_salt_hex",
    ),
    sa.CheckConstraint(
        "secret_hash ~ '^[0-9a-f]{64}$'",
        name="client_credential_hash_hex",
    ),
    sa.CheckConstraint(
        "fingerprint ~ '^[0-9a-f]{16}$'",
        name="client_credential_fingerprint_hex",
    ),
    sa.CheckConstraint(
        "key_version >= 1 AND state_version >= 1",
        name="client_credential_versions_positive",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','REVOKED')",
        name="client_credential_status_known",
    ),
    sa.CheckConstraint(
        "(status = 'ACTIVE' AND revoked_at IS NULL) "
        "OR (status = 'REVOKED' AND revoked_at IS NOT NULL)",
        name="client_credential_revocation_consistent",
    ),
)

sa.Index(
    "ix_client_access_credentials_owner_status",
    client_access_credentials.c.tenant_id,
    client_access_credentials.c.client_id,
    client_access_credentials.c.status,
)
sa.Index(
    "ix_client_access_credentials_fingerprint",
    client_access_credentials.c.fingerprint,
)

client_credential_operations = sa.Table(
    "client_credential_operations",
    client_credential_metadata,
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
        name="client_credential_operation_key",
    ),
    sa.CheckConstraint(
        "operation IN ('ISSUE','ROTATE','REVOKE')",
        name="client_credential_operation_known",
    ),
    sa.CheckConstraint(
        "key_hash ~ '^[0-9a-f]{64}$'",
        name="client_credential_operation_key_hash",
    ),
    sa.CheckConstraint(
        "request_fingerprint ~ '^[0-9a-f]{64}$'",
        name="client_credential_operation_request_hash",
    ),
)
