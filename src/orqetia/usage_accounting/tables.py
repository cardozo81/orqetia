"""SQLAlchemy Core tables owned by the Usage & Accounting bounded context."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

accounting_metadata = metadata_for_schema("accounting")

accounting_ledger = sa.Table(
    "usage_ledger",
    accounting_metadata,
    sa.Column("entry_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("client_id", UUID(as_uuid=True), nullable=False),
    sa.Column("client_credential_id", UUID(as_uuid=True), nullable=True),
    sa.Column("provider_account_id", UUID(as_uuid=True), nullable=True),
    sa.Column("provider_credential_id", UUID(as_uuid=True), nullable=True),
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
    sa.UniqueConstraint("attempt_id", "fragment_id", name="attempt_fragment"),
    sa.CheckConstraint(
        "provider_credential_id IS NULL OR provider_account_id IS NOT NULL",
        name="credential_requires_provider_account",
    ),
    sa.CheckConstraint("char_length(fragment_id) BETWEEN 1 AND 100", name="fragment_bounded"),
    sa.CheckConstraint("char_length(provider_id) BETWEEN 1 AND 100", name="provider_bounded"),
    sa.CheckConstraint("char_length(model_id) BETWEEN 1 AND 200", name="model_bounded"),
    sa.CheckConstraint(
        "char_length(reasoning_profile) BETWEEN 1 AND 100",
        name="reasoning_profile_bounded",
    ),
    sa.CheckConstraint(
        "input_tokens IS NULL OR input_tokens >= 0",
        name="input_tokens_non_negative",
    ),
    sa.CheckConstraint(
        "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
        name="cached_tokens_non_negative",
    ),
    sa.CheckConstraint(
        "cached_input_tokens IS NULL OR input_tokens IS NULL "
        "OR cached_input_tokens <= input_tokens",
        name="cached_not_above_input",
    ),
    sa.CheckConstraint(
        "output_tokens IS NULL OR output_tokens >= 0",
        name="output_tokens_non_negative",
    ),
    sa.CheckConstraint(
        "reasoning_tokens IS NULL OR reasoning_tokens >= 0",
        name="reasoning_tokens_non_negative",
    ),
    sa.CheckConstraint(
        "provider_total_tokens IS NULL OR provider_total_tokens >= 0",
        name="provider_total_non_negative",
    ),
    sa.CheckConstraint(
        "canonical_total_tokens IS NULL OR canonical_total_tokens >= 0",
        name="canonical_total_non_negative",
    ),
    sa.CheckConstraint(
        "request_units IS NULL OR request_units >= 0",
        name="request_units_non_negative",
    ),
    sa.CheckConstraint("jsonb_typeof(native_usage) = 'array'", name="native_usage_array"),
    sa.CheckConstraint(
        "(estimated_cost IS NULL AND estimated_currency IS NULL AND estimated_basis = 'UNPRICED') "
        "OR (estimated_cost >= 0 AND estimated_currency IS NOT NULL "
        "AND estimated_basis = 'USAGE_DERIVED_ESTIMATE')",
        name="estimated_cost_semantics",
    ),
    sa.CheckConstraint(
        "(observed_cost IS NULL AND observed_currency IS NULL) "
        "OR (observed_cost >= 0 AND observed_currency IS NOT NULL)",
        name="observed_cost_semantics",
    ),
)

sa.Index(
    "ix_usage_ledger_owner_recorded",
    accounting_ledger.c.tenant_id,
    accounting_ledger.c.client_id,
    accounting_ledger.c.recorded_at,
)
sa.Index(
    "ix_usage_ledger_provider_model_recorded",
    accounting_ledger.c.provider_id,
    accounting_ledger.c.model_id,
    accounting_ledger.c.recorded_at,
)
