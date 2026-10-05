"""execution: add durable provider attempt recovery journal

Revision ID: 20261005_0005
Revises: 20261005_0004
Create Date: 2026-10-05

Ownership: Execution bounded context (#15).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0005"
down_revision: str | None = "20261005_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_tasks_task_session_owner",
        "tasks",
        ["task_id", "session_id", "tenant_id", "client_id"],
        schema="execution",
    )

    op.create_table(
        "provider_attempts",
        sa.Column("attempt_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("reasoning_profile", sa.Text(), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("request_reference", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PREPARED"),
        sa.Column("dispatch_work_id", UUID(as_uuid=True), nullable=True),
        sa.Column("provider_outcome", sa.Text(), nullable=True),
        sa.Column(
            "accepted_requirements",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "missing_requirements",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("response_reference", sa.Text(), nullable=True),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
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
            ["session_id", "tenant_id", "client_id"],
            [
                "execution.execution_sessions.session_id",
                "execution.execution_sessions.tenant_id",
                "execution.execution_sessions.client_id",
            ],
            name="fk_provider_attempts_session_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "session_id", "tenant_id", "client_id"],
            [
                "execution.tasks.task_id",
                "execution.tasks.session_id",
                "execution.tasks.tenant_id",
                "execution.tasks.client_id",
            ],
            name="fk_provider_attempts_task_session_owner",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "task_id",
            "attempt_index",
            name="uq_provider_attempts_task_index",
        ),
        sa.CheckConstraint(
            "task_id IS NULL OR session_id IS NOT NULL",
            name="ck_provider_attempts_task_requires_session",
        ),
        sa.CheckConstraint(
            "cycle >= 1",
            name="ck_provider_attempts_cycle_positive",
        ),
        sa.CheckConstraint(
            "attempt_index >= 1",
            name="ck_provider_attempts_attempt_index_positive",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_provider_attempts_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('PREPARED','DISPATCHING','COMPLETED','AMBIGUOUS','CANCELLED')",
            name="ck_provider_attempts_status_known",
        ),
        sa.CheckConstraint(
            "(status = 'PREPARED' AND dispatch_work_id IS NULL "
            "AND dispatch_started_at IS NULL) OR status <> 'PREPARED'",
            name="ck_provider_attempts_prepared_has_no_dispatch_owner",
        ),
        sa.CheckConstraint(
            "status <> 'DISPATCHING' OR "
            "(dispatch_work_id IS NOT NULL AND dispatch_started_at IS NOT NULL)",
            name="ck_provider_attempts_dispatching_has_owner",
        ),
        sa.CheckConstraint(
            "(status IN ('COMPLETED','AMBIGUOUS','CANCELLED') "
            "AND terminal_at IS NOT NULL) OR "
            "(status NOT IN ('COMPLETED','AMBIGUOUS','CANCELLED') "
            "AND terminal_at IS NULL)",
            name="ck_provider_attempts_terminal_timestamp_matches_status",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETED' OR provider_outcome IS NOT NULL",
            name="ck_provider_attempts_completed_has_outcome",
        ),
        sa.CheckConstraint(
            "char_length(operation) BETWEEN 1 AND 100",
            name="ck_provider_attempts_operation_bounded",
        ),
        sa.CheckConstraint(
            "char_length(provider_id) BETWEEN 1 AND 100",
            name="ck_provider_attempts_provider_id_bounded",
        ),
        sa.CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 200",
            name="ck_provider_attempts_model_id_bounded",
        ),
        sa.CheckConstraint(
            "char_length(reasoning_profile) BETWEEN 1 AND 100",
            name="ck_provider_attempts_reasoning_profile_bounded",
        ),
        sa.CheckConstraint(
            "char_length(request_reference) BETWEEN 1 AND 500",
            name="ck_provider_attempts_request_reference_bounded",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_provider_attempts_request_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(accepted_requirements) = 'array'",
            name="ck_provider_attempts_accepted_requirements_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(missing_requirements) = 'array'",
            name="ck_provider_attempts_missing_requirements_array",
        ),
        sa.CheckConstraint(
            "response_reference IS NULL OR char_length(response_reference) <= 500",
            name="ck_provider_attempts_response_reference_bounded",
        ),
        sa.CheckConstraint(
            "error_class IS NULL OR char_length(error_class) <= 200",
            name="ck_provider_attempts_error_class_bounded",
        ),
        sa.CheckConstraint(
            "retry_after_seconds IS NULL OR retry_after_seconds >= 0",
            name="ck_provider_attempts_retry_after_non_negative",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_provider_attempts_latency_non_negative",
        ),
        schema="execution",
    )
    op.create_index(
        "ix_provider_attempts_owner_status_created",
        "provider_attempts",
        ["tenant_id", "client_id", "status", "created_at"],
        schema="execution",
    )
    op.create_index(
        "ix_provider_attempts_task_cycle_index",
        "provider_attempts",
        ["task_id", "cycle", "attempt_index"],
        schema="execution",
    )
    op.create_index(
        "ix_provider_attempts_dispatch_work",
        "provider_attempts",
        ["dispatch_work_id"],
        schema="execution",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_attempts_dispatch_work",
        table_name="provider_attempts",
        schema="execution",
    )
    op.drop_index(
        "ix_provider_attempts_task_cycle_index",
        table_name="provider_attempts",
        schema="execution",
    )
    op.drop_index(
        "ix_provider_attempts_owner_status_created",
        table_name="provider_attempts",
        schema="execution",
    )
    op.drop_table("provider_attempts", schema="execution")
    op.drop_constraint(
        "uq_tasks_task_session_owner",
        "tasks",
        schema="execution",
        type_="unique",
    )
