"""SQLAlchemy tables for provider credential metadata owned by Control Plane."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

provider_credential_metadata = metadata_for_schema("control")

provider_credentials = sa.Table(
    "provider_credentials",
    provider_credential_metadata,
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
        name="provider_credential_provider_bounded",
    ),
    sa.CheckConstraint(
        "secret_reference LIKE '%://%'",
        name="provider_credential_secret_reference_opaque",
    ),
    sa.CheckConstraint(
        "fingerprint ~ '^[0-9a-f]{16}$'",
        name="provider_credential_fingerprint_safe",
    ),
    sa.CheckConstraint(
        "key_version >= 1 AND state_version >= 1",
        name="provider_credential_versions_positive",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','REVOKED')",
        name="provider_credential_status_known",
    ),
    sa.CheckConstraint(
        "(status = 'ACTIVE' AND revoked_at IS NULL) "
        "OR (status = 'REVOKED' AND revoked_at IS NOT NULL)",
        name="provider_credential_revocation_consistent",
    ),
)

sa.Index(
    "ix_provider_credentials_provider_status",
    provider_credentials.c.provider_id,
    provider_credentials.c.status,
)
sa.Index(
    "ix_provider_credentials_account_status",
    provider_credentials.c.provider_account_id,
    provider_credentials.c.status,
)
