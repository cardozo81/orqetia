"""audit: add immutable audit ledger

Revision ID: 20261006_0031
Revises: 20261006_0030
Create Date: 2026-10-06

Ownership: audit retention/integrity/immutability (#150).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261006_0031"
down_revision: str | None = "20261006_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("audit_class", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_policy_id", sa.Text(), nullable=False),
        sa.Column("retention_policy_version", sa.Integer(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "audit_class IN ('SECURITY_ADMIN','CLIENT_ACTIVITY')",
            name="ck_audit_events_class_known",
        ),
        sa.CheckConstraint(
            "char_length(action) BETWEEN 1 AND 100",
            name="ck_audit_events_action_bounded",
        ),
        sa.CheckConstraint(
            "char_length(result) BETWEEN 1 AND 40",
            name="ck_audit_events_result_bounded",
        ),
        sa.CheckConstraint(
            "char_length(correlation_id) BETWEEN 1 AND 200",
            name="ck_audit_events_correlation_bounded",
        ),
        sa.CheckConstraint(
            "retention_policy_version >= 1",
            name="ck_audit_events_retention_version_positive",
        ),
        sa.CheckConstraint(
            "recorded_at >= occurred_at AND retain_until > occurred_at",
            name="ck_audit_events_time_order",
        ),
        sa.CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_audit_events_hash_shape",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_events"),
        schema="audit",
    )
    op.create_index(
        "ix_audit_events_class_time",
        "audit_events",
        ["audit_class", "occurred_at", "event_id"],
        schema="audit",
    )
    op.create_index(
        "ix_audit_events_owner_time",
        "audit_events",
        ["tenant_id", "client_id", "occurred_at", "event_id"],
        schema="audit",
    )
    op.create_index(
        "ix_audit_events_retain_until",
        "audit_events",
        ["retain_until"],
        schema="audit",
    )

    op.execute(
        """
        CREATE FUNCTION audit.reject_immutable_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'immutable audit rows cannot be updated or deleted'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit.audit_events
        FOR EACH ROW EXECUTE FUNCTION audit.reject_immutable_audit_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_customer_activity_append_only
        BEFORE UPDATE OR DELETE ON audit.customer_activity_events
        FOR EACH ROW EXECUTE FUNCTION audit.reject_immutable_audit_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_customer_activity_append_only "
        "ON audit.customer_activity_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit.audit_events"
    )
    op.execute("DROP FUNCTION IF EXISTS audit.reject_immutable_audit_mutation()")
    op.drop_index(
        "ix_audit_events_retain_until",
        table_name="audit_events",
        schema="audit",
    )
    op.drop_index(
        "ix_audit_events_owner_time",
        table_name="audit_events",
        schema="audit",
    )
    op.drop_index(
        "ix_audit_events_class_time",
        table_name="audit_events",
        schema="audit",
    )
    op.drop_table("audit_events", schema="audit")
