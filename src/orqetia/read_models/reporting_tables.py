"""Typed reporting rollups for fast Backoffice/client read APIs."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

reporting_metadata = metadata_for_schema("readmodel")

report_rollups = sa.Table(
    "report_rollups",
    reporting_metadata,
    sa.Column("rollup_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("client_id", UUID(as_uuid=True), nullable=False),
    sa.Column("client_credential_id", UUID(as_uuid=True), nullable=True),
    sa.Column("provider_id", sa.Text(), nullable=False),
    sa.Column("provider_account_id", UUID(as_uuid=True), nullable=True),
    sa.Column("provider_credential_id", UUID(as_uuid=True), nullable=True),
    sa.Column("model_id", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("error_class", sa.Text(), nullable=True),
    sa.Column("attempts", sa.BigInteger(), nullable=False),
    sa.Column("input_tokens", sa.BigInteger(), nullable=False),
    sa.Column("cached_input_tokens", sa.BigInteger(), nullable=False),
    sa.Column("output_tokens", sa.BigInteger(), nullable=False),
    sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False),
    sa.Column("total_tokens", sa.BigInteger(), nullable=False),
    sa.Column("native_usage", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("latency_ms_total", sa.BigInteger(), nullable=False),
    sa.Column("cycles", sa.BigInteger(), nullable=False),
    sa.Column("retries", sa.BigInteger(), nullable=False),
    sa.Column("estimated_cost", sa.Numeric(38, 18), nullable=True),
    sa.Column("estimated_currency", sa.Text(), nullable=True),
    sa.Column("observed_cost", sa.Numeric(38, 18), nullable=True),
    sa.Column("observed_currency", sa.Text(), nullable=True),
    sa.Column("unpriced_attempts", sa.BigInteger(), nullable=False),
    sa.CheckConstraint("period_end > period_start", name="report_period_valid"),
    sa.CheckConstraint("as_of >= period_end", name="report_as_of_valid"),
    sa.CheckConstraint(
        "provider_credential_id IS NULL OR provider_account_id IS NOT NULL",
        name="report_credential_requires_account",
    ),
    sa.CheckConstraint(
        "attempts >= 0 AND input_tokens >= 0 AND cached_input_tokens >= 0 "
        "AND output_tokens >= 0 AND reasoning_tokens >= 0 AND total_tokens >= 0 "
        "AND latency_ms_total >= 0 AND cycles >= 0 AND retries >= 0 "
        "AND unpriced_attempts >= 0",
        name="report_metrics_non_negative",
    ),
    sa.CheckConstraint(
        "(estimated_cost IS NULL AND estimated_currency IS NULL) OR "
        "(estimated_cost >= 0 AND estimated_currency IS NOT NULL)",
        name="report_estimated_cost_consistent",
    ),
    sa.CheckConstraint(
        "(observed_cost IS NULL AND observed_currency IS NULL) OR "
        "(observed_cost >= 0 AND observed_currency IS NOT NULL)",
        name="report_observed_cost_consistent",
    ),
)

sa.Index(
    "ix_report_rollups_owner_period",
    report_rollups.c.tenant_id,
    report_rollups.c.client_id,
    report_rollups.c.period_start,
)
sa.Index(
    "ix_report_rollups_provider_model_period",
    report_rollups.c.provider_id,
    report_rollups.c.model_id,
    report_rollups.c.period_start,
)
sa.Index(
    "ix_report_rollups_provider_credential_period",
    report_rollups.c.provider_credential_id,
    report_rollups.c.period_start,
)
sa.Index(
    "ix_report_rollups_client_credential_period",
    report_rollups.c.client_credential_id,
    report_rollups.c.period_start,
)
