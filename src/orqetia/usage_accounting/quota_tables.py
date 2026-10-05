"""Durable quota consumption tables owned by Usage & Accounting."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

quota_accounting_metadata = metadata_for_schema("accounting")

quota_windows = sa.Table(
    "quota_windows",
    quota_accounting_metadata,
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
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("policy_version >= 1", name="quota_window_version_positive"),
    sa.CheckConstraint("consumed_amount >= 0", name="quota_window_consumed_non_negative"),
)

quota_reservations = sa.Table(
    "quota_reservations",
    quota_accounting_metadata,
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
        name="quota_reservation_window",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint("policy_version >= 1", name="quota_reservation_version_positive"),
    sa.CheckConstraint("reserved_amount >= 0", name="quota_reserved_non_negative"),
    sa.CheckConstraint(
        "actual_amount IS NULL OR actual_amount >= 0",
        name="quota_actual_non_negative",
    ),
    sa.CheckConstraint(
        "status IN ('RESERVED','REJECTED','RECONCILED','RELEASED','EXPIRED')",
        name="quota_reservation_status_known",
    ),
    sa.CheckConstraint(
        "enforcement IN ('HARD','SOFT')",
        name="quota_reservation_enforcement_known",
    ),
)

sa.Index(
    "ix_quota_reservations_window_status",
    quota_reservations.c.window_key,
    quota_reservations.c.status,
    quota_reservations.c.expires_at,
)
sa.Index(
    "ix_quota_reservations_owner_created",
    quota_reservations.c.tenant_id,
    quota_reservations.c.client_id,
    quota_reservations.c.created_at,
)
