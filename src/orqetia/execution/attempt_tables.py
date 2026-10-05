"""Execution-owned provider-attempt persistence."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

attempt_metadata = metadata_for_schema("execution")

provider_attempts = sa.Table(
    "provider_attempts",
    attempt_metadata,
    sa.Column("attempt_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("work_id", UUID(as_uuid=True), nullable=False, unique=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    sa.Column("client_id", UUID(as_uuid=True), nullable=True),
    sa.Column("session_id", UUID(as_uuid=True), nullable=True),
    sa.Column("task_id", UUID(as_uuid=True), nullable=True),
    sa.Column("operation", sa.Text(), nullable=False),
    sa.Column("provider_id", sa.Text(), nullable=False),
    sa.Column("model_id", sa.Text(), nullable=False),
    sa.Column("reasoning_profile", sa.Text(), nullable=False),
    sa.Column("cycle", sa.Integer(), nullable=False),
    sa.Column("attempt_index", sa.Integer(), nullable=False),
    sa.Column("request_reference", sa.Text(), nullable=False),
    sa.Column("request_fingerprint", sa.Text(), nullable=False),
    sa.Column("missing_requirements", JSONB(), nullable=False),
    sa.Column("retry_of_attempt_id", UUID(as_uuid=True), nullable=True),
    sa.Column("fallback_from_attempt_id", UUID(as_uuid=True), nullable=True),
    sa.Column("status", sa.Text(), nullable=False, server_default="READY"),
    sa.Column("outcome", sa.Text(), nullable=True),
    sa.Column("output_kind", sa.Text(), nullable=True),
    sa.Column("accepted_requirements", JSONB(), nullable=True),
    sa.Column("remaining_requirements", JSONB(), nullable=True),
    sa.Column("response_reference", sa.Text(), nullable=True),
    sa.Column("error_class", sa.Text(), nullable=True),
    sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
    sa.Column("simulated_latency_ms", sa.Integer(), nullable=True),
    sa.Column("usage", JSONB(), nullable=True),
    sa.Column("cost_metadata", JSONB(), nullable=True),
    sa.Column("ambiguity_error_class", sa.Text(), nullable=True),
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
    sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    sa.ForeignKeyConstraint(
        ["task_id", "tenant_id", "client_id"],
        [
            "execution.tasks.task_id",
            "execution.tasks.tenant_id",
            "execution.tasks.client_id",
        ],
        name="fk_provider_attempts_task_owner",
        ondelete="CASCADE",
    ),
    sa.CheckConstraint(
        "(task_id IS NULL) OR "
        "(session_id IS NOT NULL AND tenant_id IS NOT NULL AND client_id IS NOT NULL)",
        name="task_scope_complete",
    ),
    sa.CheckConstraint(
        "status IN ('READY','DISPATCHING','COMPLETED','AMBIGUOUS')",
        name="status_known",
    ),
    sa.CheckConstraint(
        "operation ~ '^[A-Z][A-Z0-9_]*$'",
        name="operation_format",
    ),
    sa.CheckConstraint("cycle >= 1", name="cycle_positive"),
    sa.CheckConstraint("attempt_index >= 1", name="attempt_index_positive"),
    sa.CheckConstraint(
        "request_fingerprint ~ '^[0-9a-f]{64}$'",
        name="request_fingerprint_sha256",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(missing_requirements) = 'array'",
        name="missing_requirements_array",
    ),
    sa.CheckConstraint(
        "retry_after_seconds IS NULL OR retry_after_seconds >= 0",
        name="retry_after_non_negative",
    ),
    sa.CheckConstraint(
        "simulated_latency_ms IS NULL OR simulated_latency_ms >= 0",
        name="latency_non_negative",
    ),
    sa.CheckConstraint("version >= 1", name="version_positive"),
    sa.CheckConstraint(
        "(status = 'READY' AND dispatch_started_at IS NULL "
        "AND terminal_at IS NULL AND outcome IS NULL "
        "AND ambiguity_error_class IS NULL) OR "
        "(status = 'DISPATCHING' AND dispatch_started_at IS NOT NULL "
        "AND terminal_at IS NULL AND outcome IS NULL "
        "AND ambiguity_error_class IS NULL) OR "
        "(status = 'COMPLETED' AND dispatch_started_at IS NOT NULL "
        "AND terminal_at IS NOT NULL AND outcome IS NOT NULL "
        "AND ambiguity_error_class IS NULL) OR "
        "(status = 'AMBIGUOUS' AND dispatch_started_at IS NOT NULL "
        "AND terminal_at IS NOT NULL AND outcome IS NULL "
        "AND ambiguity_error_class IS NOT NULL)",
        name="status_timestamp_outcome_consistency",
    ),
)

sa.Index(
    "ix_provider_attempts_owner_task",
    provider_attempts.c.tenant_id,
    provider_attempts.c.client_id,
    provider_attempts.c.task_id,
)
sa.Index(
    "ix_provider_attempts_status_updated",
    provider_attempts.c.status,
    provider_attempts.c.updated_at,
)
