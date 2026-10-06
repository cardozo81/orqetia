"""Execution-owned persistence table for client API idempotency."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

client_api_metadata = metadata_for_schema("execution")

client_api_idempotency = sa.Table(
    "client_api_idempotency",
    client_api_metadata,
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
        name="client_api_idempotency_operation_bounded",
    ),
    sa.CheckConstraint(
        "key_hash ~ '^[0-9a-f]{64}$'",
        name="client_api_idempotency_key_hash_sha256",
    ),
    sa.CheckConstraint(
        "request_fingerprint ~ '^[0-9a-f]{64}$'",
        name="client_api_idempotency_request_fingerprint_sha256",
    ),
)

sa.Index(
    "ix_client_api_idempotency_resource",
    client_api_idempotency.c.tenant_id,
    client_api_idempotency.c.client_id,
    client_api_idempotency.c.resource_id,
)
