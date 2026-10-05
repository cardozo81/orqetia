"""execution: create durable task state machine

Revision ID: 20261005_0004
Revises: 20261005_0003
Create Date: 2026-10-05

Ownership: Execution bounded context (#7).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0004"
down_revision: str | None = "20261005_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASK_STATES = (
    "'CREATED','QUEUED','RUNNING','PARTIAL','COMPLETE',"
    "'UNAVAILABLE','FAILED','CANCELLING','CANCELLED'"
)


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("task_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="CREATED"),
        sa.Column("effective_policy_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requested_execution_mode", sa.Text(), nullable=False),
        sa.Column("requested_target", JSONB(none_as_null=True), nullable=True),
        sa.Column("effective_target", JSONB(none_as_null=True), nullable=True),
        sa.Column("requirements", JSONB(), nullable=False),
        sa.Column(
            "accepted_requirements",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("missing_requirements", JSONB(), nullable=False),
        sa.Column("input_reference", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("context_reference", sa.Text(), nullable=True),
        sa.Column("schema_reference", sa.Text(), nullable=True),
        sa.Column("external_reference", sa.Text(), nullable=True),
        sa.Column(
            "attempt_reference_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
            "provenance_reference_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("current_cycle", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_reference", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("error_reference_id", UUID(as_uuid=True), nullable=True),
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
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "task_id",
            "tenant_id",
            "client_id",
            name="uq_tasks_task_owner",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id", "client_id"],
            [
                "execution.execution_sessions.session_id",
                "execution.execution_sessions.tenant_id",
                "execution.execution_sessions.client_id",
            ],
            name="fk_tasks_session_owner",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"status IN ({_TASK_STATES})",
            name="ck_tasks_status_known",
        ),
        sa.CheckConstraint(
            "requested_execution_mode IN ('AUTO','EXPLICIT_TARGET')",
            name="ck_tasks_execution_mode_known",
        ),
        sa.CheckConstraint(
            "(requested_execution_mode = 'AUTO' "
            "AND requested_target IS NULL AND effective_target IS NULL) OR "
            "(requested_execution_mode = 'EXPLICIT_TARGET' "
            "AND requested_target IS NOT NULL AND effective_target IS NOT NULL)",
            name="ck_tasks_execution_target_semantics",
        ),
        sa.CheckConstraint(
            "requested_target IS NULL OR jsonb_typeof(requested_target) = 'object'",
            name="ck_tasks_requested_target_object",
        ),
        sa.CheckConstraint(
            "effective_target IS NULL OR jsonb_typeof(effective_target) = 'object'",
            name="ck_tasks_effective_target_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(requirements) = 'array'",
            name="ck_tasks_requirements_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(accepted_requirements) = 'array'",
            name="ck_tasks_accepted_requirements_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(missing_requirements) = 'array'",
            name="ck_tasks_missing_requirements_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attempt_reference_ids) = 'array'",
            name="ck_tasks_attempt_reference_ids_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(usage_reference_ids) = 'array'",
            name="ck_tasks_usage_reference_ids_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(internal_cost_reference_ids) = 'array'",
            name="ck_tasks_internal_cost_reference_ids_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provenance_reference_ids) = 'array'",
            name="ck_tasks_provenance_reference_ids_array",
        ),
        sa.CheckConstraint(
            "operation ~ '^[A-Z][A-Z0-9_]*$'",
            name="ck_tasks_operation_format",
        ),
        sa.CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_tasks_input_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            "current_cycle >= 0",
            name="ck_tasks_current_cycle_non_negative",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_tasks_version_positive",
        ),
        sa.CheckConstraint(
            "(status IN ('PARTIAL','COMPLETE','UNAVAILABLE','FAILED','CANCELLED') "
            "AND terminal_at IS NOT NULL) OR "
            "(status NOT IN ('PARTIAL','COMPLETE','UNAVAILABLE','FAILED','CANCELLED') "
            "AND terminal_at IS NULL)",
            name="ck_tasks_terminal_timestamp_matches_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('COMPLETE','PARTIAL') OR result_reference IS NOT NULL",
            name="ck_tasks_successful_terminal_has_result",
        ),
        sa.CheckConstraint(
            "char_length(input_reference) BETWEEN 1 AND 500",
            name="ck_tasks_input_reference_bounded",
        ),
        sa.CheckConstraint(
            "context_reference IS NULL OR char_length(context_reference) <= 500",
            name="ck_tasks_context_reference_bounded",
        ),
        sa.CheckConstraint(
            "schema_reference IS NULL OR char_length(schema_reference) <= 500",
            name="ck_tasks_schema_reference_bounded",
        ),
        sa.CheckConstraint(
            "result_reference IS NULL OR char_length(result_reference) <= 500",
            name="ck_tasks_result_reference_bounded",
        ),
        sa.CheckConstraint(
            "external_reference IS NULL OR char_length(external_reference) <= 200",
            name="ck_tasks_external_reference_bounded",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR char_length(reason_code) <= 200",
            name="ck_tasks_reason_code_bounded",
        ),
        sa.CheckConstraint(
            "error_class IS NULL OR char_length(error_class) <= 200",
            name="ck_tasks_error_class_bounded",
        ),
        schema="execution",
    )
    op.create_index(
        "ix_tasks_owner_status_created",
        "tasks",
        ["tenant_id", "client_id", "status", "created_at"],
        schema="execution",
    )
    op.create_index(
        "ix_tasks_session_created",
        "tasks",
        ["session_id", "created_at"],
        schema="execution",
    )
    op.create_index(
        "ix_tasks_queue_status_updated",
        "tasks",
        ["status", "updated_at"],
        schema="execution",
    )

    op.create_table(
        "task_transitions",
        sa.Column("transition_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("error_reference_id", UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "task_id",
            "task_version",
            name="uq_task_transitions_task_version",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "tenant_id", "client_id"],
            [
                "execution.tasks.task_id",
                "execution.tasks.tenant_id",
                "execution.tasks.client_id",
            ],
            name="fk_task_transitions_task_owner",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_TASK_STATES})",
            name="ck_task_transitions_from_status_known",
        ),
        sa.CheckConstraint(
            f"to_status IN ({_TASK_STATES})",
            name="ck_task_transitions_to_status_known",
        ),
        sa.CheckConstraint(
            "task_version >= 1",
            name="ck_task_transitions_task_version_positive",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR char_length(reason_code) <= 200",
            name="ck_task_transitions_reason_code_bounded",
        ),
        sa.CheckConstraint(
            "error_class IS NULL OR char_length(error_class) <= 200",
            name="ck_task_transitions_error_class_bounded",
        ),
        schema="execution",
    )
    op.create_index(
        "ix_task_transitions_owner_task_time",
        "task_transitions",
        ["tenant_id", "client_id", "task_id", "occurred_at"],
        schema="execution",
    )

    op.create_table(
        "task_cycle_decisions",
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("cycle_index", sa.Integer(), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("candidate_order", JSONB(), nullable=False),
        sa.Column("escalation_reason_code", sa.Text(), nullable=True),
        sa.Column("accepted_snapshot", JSONB(), nullable=False),
        sa.Column("missing_snapshot", JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "task_id",
            "cycle_index",
            name="pk_task_cycle_decisions",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "tenant_id", "client_id"],
            [
                "execution.tasks.task_id",
                "execution.tasks.tenant_id",
                "execution.tasks.client_id",
            ],
            name="fk_task_cycle_decisions_task_owner",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "cycle_index >= 1",
            name="ck_task_cycle_decisions_cycle_index_positive",
        ),
        sa.CheckConstraint(
            "task_version >= 1",
            name="ck_task_cycle_decisions_task_version_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(candidate_order) = 'array'",
            name="ck_task_cycle_decisions_candidate_order_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(accepted_snapshot) = 'array'",
            name="ck_task_cycle_decisions_accepted_snapshot_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(missing_snapshot) = 'array'",
            name="ck_task_cycle_decisions_missing_snapshot_array",
        ),
        sa.CheckConstraint(
            "escalation_reason_code IS NULL "
            "OR char_length(escalation_reason_code) <= 200",
            name="ck_task_cycle_decisions_escalation_reason_bounded",
        ),
        schema="execution",
    )
    op.create_index(
        "ix_task_cycle_decisions_owner_task_version",
        "task_cycle_decisions",
        ["tenant_id", "client_id", "task_id", "task_version"],
        schema="execution",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_cycle_decisions_owner_task_version",
        table_name="task_cycle_decisions",
        schema="execution",
    )
    op.drop_table("task_cycle_decisions", schema="execution")
    op.drop_index(
        "ix_task_transitions_owner_task_time",
        table_name="task_transitions",
        schema="execution",
    )
    op.drop_table("task_transitions", schema="execution")
    op.drop_index(
        "ix_tasks_queue_status_updated",
        table_name="tasks",
        schema="execution",
    )
    op.drop_index(
        "ix_tasks_session_created",
        table_name="tasks",
        schema="execution",
    )
    op.drop_index(
        "ix_tasks_owner_status_created",
        table_name="tasks",
        schema="execution",
    )
    op.drop_table("tasks", schema="execution")
