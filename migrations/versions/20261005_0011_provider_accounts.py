"""provider control: accounts, external capacity and attempt/accounting provenance

Revision ID: 20261005_0011
Revises: 20261005_0010
Create Date: 2026-10-05

Ownership: Control Plane plus immutable provenance references (#59).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261005_0011"
down_revision: str | None = "20261005_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_accounts",
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
            name="ck_provider_accounts_provider_bounded",
        ),
        sa.CheckConstraint(
            "char_length(display_label) BETWEEN 1 AND 200",
            name="ck_provider_accounts_label_bounded",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED')",
            name="ck_provider_accounts_status_known",
        ),
        sa.CheckConstraint(
            "priority >= 0",
            name="ck_provider_accounts_priority_non_negative",
        ),
        sa.CheckConstraint(
            "state_version >= 1",
            name="ck_provider_accounts_state_version_positive",
        ),
        schema="control",
    )
    op.create_index(
        "ix_provider_accounts_provider_status_priority",
        "provider_accounts",
        ["provider_id", "status", "priority"],
        schema="control",
    )

    op.create_foreign_key(
        "fk_provider_credentials_account",
        "provider_credentials",
        "provider_accounts",
        ["provider_account_id"],
        ["provider_account_id"],
        source_schema="control",
        referent_schema="control",
        ondelete="RESTRICT",
    )

    op.create_table(
        "provider_capacity_snapshots",
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
            name="fk_provider_capacity_account",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "char_length(provider_id) BETWEEN 1 AND 100",
            name="ck_provider_capacity_provider_bounded",
        ),
        sa.CheckConstraint(
            "char_length(native_unit) BETWEEN 1 AND 100",
            name="ck_provider_capacity_unit_bounded",
        ),
        sa.CheckConstraint(
            "source IN ('PROVIDER_API','PROVIDER_CONSOLE')",
            name="ck_provider_capacity_source_known",
        ),
        sa.CheckConstraint(
            "remaining IS NOT NULL OR limit_amount IS NOT NULL",
            name="ck_provider_capacity_observed_value_required",
        ),
        sa.CheckConstraint(
            "remaining IS NULL OR remaining >= 0",
            name="ck_provider_capacity_remaining_non_negative",
        ),
        sa.CheckConstraint(
            "limit_amount IS NULL OR limit_amount >= 0",
            name="ck_provider_capacity_limit_non_negative",
        ),
        sa.CheckConstraint(
            "remaining IS NULL OR limit_amount IS NULL OR remaining <= limit_amount",
            name="ck_provider_capacity_remaining_within_limit",
        ),
        schema="control",
    )
    op.create_index(
        "ix_provider_capacity_account_unit_observed",
        "provider_capacity_snapshots",
        ["provider_account_id", "native_unit", "observed_at"],
        schema="control",
    )

    op.add_column(
        "provider_attempts",
        sa.Column("provider_account_id", UUID(as_uuid=True), nullable=True),
        schema="execution",
    )
    op.add_column(
        "provider_attempts",
        sa.Column("provider_credential_id", UUID(as_uuid=True), nullable=True),
        schema="execution",
    )
    op.create_index(
        "ix_provider_attempts_account_credential",
        "provider_attempts",
        ["provider_account_id", "provider_credential_id"],
        schema="execution",
    )

    op.add_column(
        "usage_ledger",
        sa.Column("provider_account_id", UUID(as_uuid=True), nullable=True),
        schema="accounting",
    )
    op.add_column(
        "usage_ledger",
        sa.Column("provider_credential_id", UUID(as_uuid=True), nullable=True),
        schema="accounting",
    )
    op.create_index(
        "ix_usage_ledger_provider_credential_recorded",
        "usage_ledger",
        ["provider_credential_id", "recorded_at"],
        schema="accounting",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_usage_ledger_provider_credential_recorded",
        table_name="usage_ledger",
        schema="accounting",
    )
    op.drop_column("usage_ledger", "provider_credential_id", schema="accounting")
    op.drop_column("usage_ledger", "provider_account_id", schema="accounting")

    op.drop_index(
        "ix_provider_attempts_account_credential",
        table_name="provider_attempts",
        schema="execution",
    )
    op.drop_column(
        "provider_attempts",
        "provider_credential_id",
        schema="execution",
    )
    op.drop_column("provider_attempts", "provider_account_id", schema="execution")

    op.drop_index(
        "ix_provider_capacity_account_unit_observed",
        table_name="provider_capacity_snapshots",
        schema="control",
    )
    op.drop_table("provider_capacity_snapshots", schema="control")
    op.drop_constraint(
        "fk_provider_credentials_account",
        "provider_credentials",
        schema="control",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_provider_accounts_provider_status_priority",
        table_name="provider_accounts",
        schema="control",
    )
    op.drop_table("provider_accounts", schema="control")
