"""SQLAlchemy tables for Control Plane-owned quota definitions."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

control_quota_metadata = metadata_for_schema("control")

quota_policies = sa.Table(
    "quota_policies",
    control_quota_metadata,
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
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.PrimaryKeyConstraint("policy_id", "version", name="quota_policy_version"),
    sa.CheckConstraint("version >= 1", name="quota_version_positive"),
    sa.CheckConstraint("limit_amount >= 0", name="quota_limit_non_negative"),
    sa.CheckConstraint("burst_amount >= 0", name="quota_burst_non_negative"),
    sa.CheckConstraint(
        "reservation_ttl_seconds >= 1",
        name="quota_reservation_ttl_positive",
    ),
    sa.CheckConstraint(
        "scope IN ('TENANT','CLIENT')",
        name="quota_scope_known",
    ),
    sa.CheckConstraint(
        "enforcement IN ('HARD','SOFT')",
        name="quota_enforcement_known",
    ),
    sa.CheckConstraint(
        "(scope = 'TENANT' AND client_id IS NULL) OR "
        "(scope = 'CLIENT' AND client_id IS NOT NULL)",
        name="quota_scope_subject_consistent",
    ),
)

sa.Index(
    "ix_quota_policies_subject_effective",
    quota_policies.c.tenant_id,
    quota_policies.c.client_id,
    quota_policies.c.effective_from,
)
