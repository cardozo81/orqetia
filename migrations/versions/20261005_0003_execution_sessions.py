"""execution: create durable execution sessions

Revision ID: 20261005_0003
Revises: 20261005_0002
Create Date: 2026-10-05

Ownership: Execution bounded context (#6).
No cross-context foreign keys are introduced.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0003"
down_revision: str | None = "20261005_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_sessions",
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
            name="ck_execution_sessions_status_known",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND terminal_at IS NULL) OR "
            "(status <> 'ACTIVE' AND terminal_at IS NOT NULL)",
            name="ck_execution_sessions_terminal_timestamp_matches_status",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at >= created_at",
            name="ck_execution_sessions_expiry_not_before_creation",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_execution_sessions_version_positive",
        ),
        sa.CheckConstraint(
            "external_reference IS NULL OR char_length(external_reference) <= 200",
            name="ck_execution_sessions_external_reference_bounded",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authorized_targets) = 'array'",
            name="ck_execution_sessions_authorized_targets_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(usage_reference_ids) = 'array'",
            name="ck_execution_sessions_usage_reference_ids_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(internal_cost_reference_ids) = 'array'",
            name="ck_execution_sessions_internal_cost_reference_ids_array",
        ),
        schema="execution",
    )
    op.create_index(
        "ix_execution_sessions_owner_status_created",
        "execution_sessions",
        ["tenant_id", "client_id", "status", "created_at"],
        schema="execution",
    )
    op.create_index(
        "ix_execution_sessions_expires_at",
        "execution_sessions",
        ["expires_at"],
        schema="execution",
    )

    op.create_table(
        "session_target_runtime",
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
            name="ck_session_target_runtime_provider_id_bounded",
        ),
        sa.CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 200",
            name="ck_session_target_runtime_model_id_bounded",
        ),
        sa.CheckConstraint(
            "char_length(reasoning_profile) BETWEEN 1 AND 100",
            name="ck_session_target_runtime_reasoning_profile_bounded",
        ),
        sa.CheckConstraint(
            "health_state IN ('UNKNOWN','HEALTHY','DEGRADED','UNAVAILABLE')",
            name="ck_session_target_runtime_health_state_known",
        ),
        sa.CheckConstraint(
            "quarantine_state IN ('NONE','TEMPORARY','TERMINAL')",
            name="ck_session_target_runtime_quarantine_state_known",
        ),
        sa.CheckConstraint(
            "(quarantine_state = 'NONE' AND quarantine_until IS NULL) OR "
            "(quarantine_state = 'TEMPORARY' AND quarantine_until IS NOT NULL) OR "
            "(quarantine_state = 'TERMINAL' AND quarantine_until IS NULL)",
            name="ck_session_target_runtime_quarantine_expiry_semantics",
        ),
        sa.CheckConstraint(
            "quarantine_reason_code IS NULL OR char_length(quarantine_reason_code) <= 200",
            name="ck_session_target_runtime_quarantine_reason_bounded",
        ),
        schema="execution",
    )
    op.create_index(
        "ix_session_target_runtime_owner_quarantine",
        "session_target_runtime",
        ["tenant_id", "client_id", "quarantine_state"],
        schema="execution",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_session_target_runtime_owner_quarantine",
        table_name="session_target_runtime",
        schema="execution",
    )
    op.drop_table("session_target_runtime", schema="execution")
    op.drop_index(
        "ix_execution_sessions_expires_at",
        table_name="execution_sessions",
        schema="execution",
    )
    op.drop_index(
        "ix_execution_sessions_owner_status_created",
        table_name="execution_sessions",
        schema="execution",
    )
    op.drop_table("execution_sessions", schema="execution")
