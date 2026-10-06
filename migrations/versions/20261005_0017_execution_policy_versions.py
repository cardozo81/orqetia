"""control: add immutable execution policy versions and assignments

Revision ID: 20261005_0017
Revises: 20261005_0016
Create Date: 2026-10-05

Ownership: Control Plane execution policy administration (#137).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0017"
down_revision: str | None = "20261005_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_policy_versions",
        sa.Column("policy_version_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("max_cycles", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cycle_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_after_cap_seconds", sa.Integer(), nullable=False),
        sa.Column("authorized_targets", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "client_id",
            "version_number",
            name="uq_execution_policy_owner_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_execution_policy_versions_version_positive",
        ),
        sa.CheckConstraint(
            "max_cycles >= 1",
            name="ck_execution_policy_versions_max_cycles_positive",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_execution_policy_versions_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "cycle_delay_seconds >= 0",
            name="ck_execution_policy_versions_cycle_delay_non_negative",
        ),
        sa.CheckConstraint(
            "retry_after_cap_seconds BETWEEN 0 AND 300",
            name="ck_execution_policy_versions_retry_after_bounded",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authorized_targets) = 'array' "
            "AND jsonb_array_length(authorized_targets) >= 1",
            name="ck_execution_policy_versions_targets_non_empty",
        ),
        schema="control",
    )
    op.create_index(
        "ix_execution_policy_versions_owner_created",
        "execution_policy_versions",
        ["tenant_id", "client_id", "created_at"],
        schema="control",
    )

    op.create_table(
        "client_policy_assignments",
        sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_version", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["control.execution_policy_versions.policy_version_id"],
            name="fk_client_policy_assignment_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "assignment_version >= 1",
            name="ck_client_policy_assignments_version_positive",
        ),
        schema="control",
    )


def downgrade() -> None:
    op.drop_table("client_policy_assignments", schema="control")
    op.drop_index(
        "ix_execution_policy_versions_owner_created",
        table_name="execution_policy_versions",
        schema="control",
    )
    op.drop_table("execution_policy_versions", schema="control")
