"""accounting: add immutable technical usage/provider-cost ledger

Revision ID: 20261005_0006
Revises: 20261005_0005
Create Date: 2026-10-05

Ownership: Usage & Accounting bounded context (#16).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0006"
down_revision: str | None = "20261005_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_ledger",
        sa.Column("entry_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_credential_id", UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_id", UUID(as_uuid=True), nullable=False),
        sa.Column("fragment_id", sa.Text(), nullable=False, server_default="primary"),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("reasoning_profile", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("provider_total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("canonical_total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("request_units", sa.Numeric(38, 12), nullable=True),
        sa.Column("native_usage", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("estimated_basis", sa.Text(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(38, 18), nullable=True),
        sa.Column("estimated_currency", sa.Text(), nullable=True),
        sa.Column("pricing_model", sa.Text(), nullable=True),
        sa.Column("pricing_rule_id", sa.Text(), nullable=True),
        sa.Column("pricing_rule_version", sa.Integer(), nullable=True),
        sa.Column("pricing_reference", sa.Text(), nullable=True),
        sa.Column("pricing_source_reference", sa.Text(), nullable=True),
        sa.Column("unpriced_reason", sa.Text(), nullable=True),
        sa.Column("observed_cost", sa.Numeric(38, 18), nullable=True),
        sa.Column("observed_currency", sa.Text(), nullable=True),
        sa.Column("observed_source_reference", sa.Text(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("attempt_id", "fragment_id", name="uq_usage_ledger_attempt_fragment"),
        sa.CheckConstraint(
            "char_length(fragment_id) BETWEEN 1 AND 100",
            name="ck_usage_ledger_fragment_bounded",
        ),
        sa.CheckConstraint(
            "char_length(provider_id) BETWEEN 1 AND 100",
            name="ck_usage_ledger_provider_bounded",
        ),
        sa.CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 200",
            name="ck_usage_ledger_model_bounded",
        ),
        sa.CheckConstraint(
            "char_length(reasoning_profile) BETWEEN 1 AND 100",
            name="ck_usage_ledger_reasoning_profile_bounded",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_usage_ledger_input_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
            name="ck_usage_ledger_cached_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "cached_input_tokens IS NULL OR input_tokens IS NULL "
            "OR cached_input_tokens <= input_tokens",
            name="ck_usage_ledger_cached_not_above_input",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_usage_ledger_output_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "reasoning_tokens IS NULL OR reasoning_tokens >= 0",
            name="ck_usage_ledger_reasoning_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "provider_total_tokens IS NULL OR provider_total_tokens >= 0",
            name="ck_usage_ledger_provider_total_non_negative",
        ),
        sa.CheckConstraint(
            "canonical_total_tokens IS NULL OR canonical_total_tokens >= 0",
            name="ck_usage_ledger_canonical_total_non_negative",
        ),
        sa.CheckConstraint(
            "request_units IS NULL OR request_units >= 0",
            name="ck_usage_ledger_request_units_non_negative",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(native_usage) = 'array'",
            name="ck_usage_ledger_native_usage_array",
        ),
        sa.CheckConstraint(
            "(estimated_cost IS NULL AND estimated_currency IS NULL "
            "AND estimated_basis = 'UNPRICED') OR "
            "(estimated_cost >= 0 AND estimated_currency IS NOT NULL "
            "AND estimated_basis = 'USAGE_DERIVED_ESTIMATE')",
            name="ck_usage_ledger_estimated_cost_semantics",
        ),
        sa.CheckConstraint(
            "(observed_cost IS NULL AND observed_currency IS NULL) OR "
            "(observed_cost >= 0 AND observed_currency IS NOT NULL)",
            name="ck_usage_ledger_observed_cost_semantics",
        ),
        schema="accounting",
    )
    op.create_index(
        "ix_usage_ledger_owner_recorded",
        "usage_ledger",
        ["tenant_id", "client_id", "recorded_at"],
        schema="accounting",
    )
    op.create_index(
        "ix_usage_ledger_provider_model_recorded",
        "usage_ledger",
        ["provider_id", "model_id", "recorded_at"],
        schema="accounting",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_usage_ledger_provider_model_recorded",
        table_name="usage_ledger",
        schema="accounting",
    )
    op.drop_index(
        "ix_usage_ledger_owner_recorded",
        table_name="usage_ledger",
        schema="accounting",
    )
    op.drop_table("usage_ledger", schema="accounting")
