"""quota: add client quota definitions and consumption persistence

Revision ID: 20261005_0007
Revises: 20261005_0006
Create Date: 2026-10-05

Ownership: Control Plane policy + Usage & Accounting consumption (#17).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261005_0007"
down_revision: str | None = "20261005_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quota_policies",
        sa.Column("policy_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=True),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("limit_amount", sa.Numeric(38, 12), nullable=False),
        sa.Column("enforcement", sa.Text(), nullable=False),
        sa.Column("period_seconds", sa.Integer(), nullable=True),
        sa.Column("burst_amount", sa.Numeric(38, 12), nullable=False, server_default="0"),
        sa.Column("reservation_ttl_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("native_unit", sa.Text(), nullable=True),
        sa.Column("provider_id", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("policy_id", "version", name="pk_quota_policies"),
        sa.CheckConstraint("version >= 1", name="ck_quota_policies_version_positive"),
        sa.CheckConstraint("limit_amount >= 0", name="ck_quota_policies_limit_non_negative"),
        sa.CheckConstraint("burst_amount >= 0", name="ck_quota_policies_burst_non_negative"),
        sa.CheckConstraint(
            "reservation_ttl_seconds >= 1",
            name="ck_quota_policies_reservation_ttl_positive",
        ),
        sa.CheckConstraint("scope IN ('TENANT','CLIENT')", name="ck_quota_policies_scope_known"),
        sa.CheckConstraint(
            "enforcement IN ('HARD','SOFT')",
            name="ck_quota_policies_enforcement_known",
        ),
        sa.CheckConstraint(
            "(scope = 'TENANT' AND client_id IS NULL) OR "
            "(scope = 'CLIENT' AND client_id IS NOT NULL)",
            name="ck_quota_policies_scope_subject_consistent",
        ),
        schema="control",
    )
    op.create_index(
        "ix_quota_policies_subject_effective",
        "quota_policies",
        ["tenant_id", "client_id", "effective_from"],
        schema="control",
    )

    op.create_table(
        "quota_windows",
        sa.Column("window_key", sa.Text(), primary_key=True),
        sa.Column("policy_id", UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=True),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=True),
        sa.Column("native_unit", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_amount", sa.Numeric(38, 12), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "policy_version >= 1",
            name="ck_quota_windows_version_positive",
        ),
        sa.CheckConstraint(
            "consumed_amount >= 0",
            name="ck_quota_windows_consumed_non_negative",
        ),
        schema="accounting",
    )
    op.create_table(
        "quota_reservations",
        sa.Column("reservation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("window_key", sa.Text(), nullable=False),
        sa.Column("idempotency_scope_key", sa.Text(), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("policy_id", UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=True),
        sa.Column("native_unit", sa.Text(), nullable=True),
        sa.Column("reserved_amount", sa.Numeric(38, 12), nullable=False),
        sa.Column("actual_amount", sa.Numeric(38, 12), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("enforcement", sa.Text(), nullable=False),
        sa.Column("limit_snapshot", sa.Numeric(38, 12), nullable=False),
        sa.Column("burst_snapshot", sa.Numeric(38, 12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["window_key"],
            ["accounting.quota_windows.window_key"],
            name="fk_quota_reservations_window",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "policy_version >= 1",
            name="ck_quota_reservations_version_positive",
        ),
        sa.CheckConstraint(
            "reserved_amount >= 0",
            name="ck_quota_reservations_reserved_non_negative",
        ),
        sa.CheckConstraint(
            "actual_amount IS NULL OR actual_amount >= 0",
            name="ck_quota_reservations_actual_non_negative",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED','REJECTED','RECONCILED','RELEASED','EXPIRED')",
            name="ck_quota_reservations_status_known",
        ),
        sa.CheckConstraint(
            "enforcement IN ('HARD','SOFT')",
            name="ck_quota_reservations_enforcement_known",
        ),
        schema="accounting",
    )
    op.create_index(
        "ix_quota_reservations_window_status",
        "quota_reservations",
        ["window_key", "status", "expires_at"],
        schema="accounting",
    )
    op.create_index(
        "ix_quota_reservations_owner_created",
        "quota_reservations",
        ["tenant_id", "client_id", "created_at"],
        schema="accounting",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quota_reservations_owner_created",
        table_name="quota_reservations",
        schema="accounting",
    )
    op.drop_index(
        "ix_quota_reservations_window_status",
        table_name="quota_reservations",
        schema="accounting",
    )
    op.drop_table("quota_reservations", schema="accounting")
    op.drop_table("quota_windows", schema="accounting")
    op.drop_index(
        "ix_quota_policies_subject_effective",
        table_name="quota_policies",
        schema="control",
    )
    op.drop_table("quota_policies", schema="control")
