"""Control Plane tables for provider accounts and official capacity snapshots."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

provider_account_metadata = metadata_for_schema("control")

provider_accounts = sa.Table(
    "provider_accounts",
    provider_account_metadata,
    sa.Column("provider_account_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("provider_id", sa.Text(), nullable=False),
    sa.Column("display_label", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    sa.Column("commercial_mode", sa.Text(), nullable=True),
    sa.Column("commercial_tier", sa.Text(), nullable=True),
    sa.Column("region", sa.Text(), nullable=True),
    sa.Column("contract_reference", sa.Text(), nullable=True),
    sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "char_length(provider_id) BETWEEN 1 AND 100",
        name="provider_account_provider_bounded",
    ),
    sa.CheckConstraint(
        "char_length(display_label) BETWEEN 1 AND 200",
        name="provider_account_label_bounded",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','DISABLED')",
        name="provider_account_status_known",
    ),
    sa.CheckConstraint("priority >= 0", name="provider_account_priority_non_negative"),
    sa.CheckConstraint(
        "state_version >= 1",
        name="provider_account_state_version_positive",
    ),
)

provider_capacity_snapshots = sa.Table(
    "provider_capacity_snapshots",
    provider_account_metadata,
    sa.Column("snapshot_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("provider_account_id", UUID(as_uuid=True), nullable=False),
    sa.Column("provider_id", sa.Text(), nullable=False),
    sa.Column("native_unit", sa.Text(), nullable=False),
    sa.Column("source", sa.Text(), nullable=False),
    sa.Column("source_reference", sa.Text(), nullable=False),
    sa.Column("remaining", sa.Numeric(38, 12), nullable=True),
    sa.Column("limit_amount", sa.Numeric(38, 12), nullable=True),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(
        ["provider_account_id"],
        ["control.provider_accounts.provider_account_id"],
        name="provider_capacity_account",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "char_length(provider_id) BETWEEN 1 AND 100",
        name="provider_capacity_provider_bounded",
    ),
    sa.CheckConstraint(
        "char_length(native_unit) BETWEEN 1 AND 100",
        name="provider_capacity_unit_bounded",
    ),
    sa.CheckConstraint(
        "source IN ('PROVIDER_API','PROVIDER_CONSOLE')",
        name="provider_capacity_source_known",
    ),
    sa.CheckConstraint(
        "remaining IS NOT NULL OR limit_amount IS NOT NULL",
        name="provider_capacity_observed_value_required",
    ),
    sa.CheckConstraint(
        "remaining IS NULL OR remaining >= 0",
        name="provider_capacity_remaining_non_negative",
    ),
    sa.CheckConstraint(
        "limit_amount IS NULL OR limit_amount >= 0",
        name="provider_capacity_limit_non_negative",
    ),
    sa.CheckConstraint(
        "remaining IS NULL OR limit_amount IS NULL OR remaining <= limit_amount",
        name="provider_capacity_remaining_within_limit",
    ),
)

sa.Index(
    "ix_provider_accounts_provider_status_priority",
    provider_accounts.c.provider_id,
    provider_accounts.c.status,
    provider_accounts.c.priority,
)
sa.Index(
    "ix_provider_capacity_account_unit_observed",
    provider_capacity_snapshots.c.provider_account_id,
    provider_capacity_snapshots.c.native_unit,
    provider_capacity_snapshots.c.observed_at,
)
