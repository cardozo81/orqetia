"""Control Plane tables for immutable execution-policy versions."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

execution_policy_metadata = metadata_for_schema("control")

execution_policy_versions = sa.Table(
    "execution_policy_versions",
    execution_policy_metadata,
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
        name="owner_version",
    ),
    sa.CheckConstraint("version_number >= 1", name="version_number_positive"),
    sa.CheckConstraint("max_cycles >= 1", name="max_cycles_positive"),
    sa.CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
    sa.CheckConstraint(
        "cycle_delay_seconds >= 0",
        name="cycle_delay_non_negative",
    ),
    sa.CheckConstraint(
        "retry_after_cap_seconds BETWEEN 0 AND 300",
        name="retry_after_cap_bounded",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(authorized_targets) = 'array' "
        "AND jsonb_array_length(authorized_targets) >= 1",
        name="authorized_targets_non_empty",
    ),
)

client_policy_assignments = sa.Table(
    "client_policy_assignments",
    execution_policy_metadata,
    sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("client_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("policy_version_id", UUID(as_uuid=True), nullable=False),
    sa.Column("assignment_version", sa.Integer(), nullable=False),
    sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["policy_version_id"],
        ["control.execution_policy_versions.policy_version_id"],
        name="assignment_policy_version",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "assignment_version >= 1",
        name="assignment_version_positive",
    ),
)

sa.Index(
    "ix_execution_policy_versions_owner_created",
    execution_policy_versions.c.tenant_id,
    execution_policy_versions.c.client_id,
    execution_policy_versions.c.created_at,
)
