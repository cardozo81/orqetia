"""SQLAlchemy Core tables owned by the Execution bounded context."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

execution_metadata = metadata_for_schema("execution")

execution_sessions = sa.Table(
    "execution_sessions",
    execution_metadata,
    sa.Column("session_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("client_id", UUID(as_uuid=True), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
    sa.Column("requested_policy_version_id", UUID(as_uuid=True), nullable=True),
    sa.Column("effective_policy_version_id", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "authorized_targets",
        JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.Column("external_reference", sa.Text(), nullable=True),
    sa.Column(
        "usage_reference_ids",
        JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.Column(
        "internal_cost_reference_ids",
        JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    sa.UniqueConstraint(
        "session_id",
        "tenant_id",
        "client_id",
        name="uq_execution_sessions_session_owner",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','EXPIRED','CANCELLED','CLOSED')",
        name="status_known",
    ),
    sa.CheckConstraint(
        "(status = 'ACTIVE' AND terminal_at IS NULL) OR "
        "(status <> 'ACTIVE' AND terminal_at IS NOT NULL)",
        name="terminal_timestamp_matches_status",
    ),
    sa.CheckConstraint(
        "expires_at IS NULL OR expires_at >= created_at",
        name="expiry_not_before_creation",
    ),
    sa.CheckConstraint("version >= 1", name="version_positive"),
    sa.CheckConstraint(
        "external_reference IS NULL OR char_length(external_reference) <= 200",
        name="external_reference_bounded",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(authorized_targets) = 'array'",
        name="authorized_targets_array",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(usage_reference_ids) = 'array'",
        name="usage_reference_ids_array",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(internal_cost_reference_ids) = 'array'",
        name="internal_cost_reference_ids_array",
    ),
)

sa.Index(
    "ix_execution_sessions_owner_status_created",
    execution_sessions.c.tenant_id,
    execution_sessions.c.client_id,
    execution_sessions.c.status,
    execution_sessions.c.created_at,
)
sa.Index("ix_execution_sessions_expires_at", execution_sessions.c.expires_at)

session_target_runtime = sa.Table(
    "session_target_runtime",
    execution_metadata,
    sa.Column("session_id", UUID(as_uuid=True), nullable=False),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("client_id", UUID(as_uuid=True), nullable=False),
    sa.Column("provider_id", sa.Text(), nullable=False),
    sa.Column("model_id", sa.Text(), nullable=False),
    sa.Column("reasoning_profile", sa.Text(), nullable=False),
    sa.Column("health_state", sa.Text(), nullable=False, server_default="UNKNOWN"),
    sa.Column("quarantine_state", sa.Text(), nullable=False, server_default="NONE"),
    sa.Column("quarantine_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("quarantine_reason_code", sa.Text(), nullable=True),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint(
        "session_id",
        "provider_id",
        "model_id",
        "reasoning_profile",
        name="pk_session_target_runtime",
    ),
    sa.ForeignKeyConstraint(
        ["session_id", "tenant_id", "client_id"],
        [
            "execution.execution_sessions.session_id",
            "execution.execution_sessions.tenant_id",
            "execution.execution_sessions.client_id",
        ],
        name="fk_session_target_runtime_session_owner",
        ondelete="CASCADE",
    ),
    sa.CheckConstraint(
        "char_length(provider_id) BETWEEN 1 AND 100",
        name="provider_id_bounded",
    ),
    sa.CheckConstraint(
        "char_length(model_id) BETWEEN 1 AND 200",
        name="model_id_bounded",
    ),
    sa.CheckConstraint(
        "char_length(reasoning_profile) BETWEEN 1 AND 100",
        name="reasoning_profile_bounded",
    ),
    sa.CheckConstraint(
        "health_state IN ('UNKNOWN','HEALTHY','DEGRADED','UNAVAILABLE')",
        name="health_state_known",
    ),
    sa.CheckConstraint(
        "quarantine_state IN ('NONE','TEMPORARY','TERMINAL')",
        name="quarantine_state_known",
    ),
    sa.CheckConstraint(
        "(quarantine_state = 'NONE' AND quarantine_until IS NULL) OR "
        "(quarantine_state = 'TEMPORARY' AND quarantine_until IS NOT NULL) OR "
        "(quarantine_state = 'TERMINAL' AND quarantine_until IS NULL)",
        name="quarantine_expiry_semantics",
    ),
    sa.CheckConstraint(
        "quarantine_reason_code IS NULL OR char_length(quarantine_reason_code) <= 200",
        name="quarantine_reason_bounded",
    ),
)

sa.Index(
    "ix_session_target_runtime_owner_quarantine",
    session_target_runtime.c.tenant_id,
    session_target_runtime.c.client_id,
    session_target_runtime.c.quarantine_state,
)
