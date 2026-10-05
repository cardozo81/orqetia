"""Aggregate-only statistical estimation persistence."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

estimation_metadata = metadata_for_schema("estimation")

benchmark_snapshots = sa.Table(
    "benchmark_snapshots",
    estimation_metadata,
    sa.Column("snapshot_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    sa.Column("client_id", UUID(as_uuid=True), nullable=True),
    sa.Column("provider_id", sa.Text(), nullable=False),
    sa.Column("model_id", sa.Text(), nullable=False),
    sa.Column("reasoning_profile", sa.Text(), nullable=False),
    sa.Column("input_size_bucket", sa.Text(), nullable=False),
    sa.Column("output_class", sa.Text(), nullable=False),
    sa.Column("schema_class", sa.Text(), nullable=False),
    sa.Column("methodology_version", sa.Text(), nullable=False),
    sa.Column("benchmark_version", sa.Text(), nullable=False, unique=True),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
    sa.Column("sample_count", sa.Integer(), nullable=False),
    sa.Column("public_sample_size", sa.Integer(), nullable=False),
    sa.Column("distinct_client_count", sa.Integer(), nullable=False),
    sa.Column("public_cohort_size", sa.Integer(), nullable=False),
    sa.Column("confidence", sa.Text(), nullable=False),
    sa.Column("median_input_tokens", sa.Numeric(38, 6), nullable=False),
    sa.Column("median_output_tokens", sa.Numeric(38, 6), nullable=False),
    sa.Column("median_cached_input_tokens", sa.Numeric(38, 6), nullable=False),
    sa.Column("median_reasoning_tokens", sa.Numeric(38, 6), nullable=False),
    sa.Column("median_total_tokens", sa.Numeric(38, 6), nullable=False),
    sa.Column("p90_output_tokens", sa.BigInteger(), nullable=False),
    sa.Column("p90_total_tokens", sa.BigInteger(), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "scope IN ('CLIENT_ONLY','GLOBAL_PUBLIC')",
        name="benchmark_scope_known",
    ),
    sa.CheckConstraint(
        "(scope = 'CLIENT_ONLY' AND tenant_id IS NOT NULL AND client_id IS NOT NULL) "
        "OR (scope = 'GLOBAL_PUBLIC' AND tenant_id IS NULL AND client_id IS NULL)",
        name="benchmark_scope_owner_consistent",
    ),
    sa.CheckConstraint("sample_count >= 1", name="benchmark_samples_positive"),
    sa.CheckConstraint(
        "distinct_client_count >= 1",
        name="benchmark_clients_positive",
    ),
    sa.CheckConstraint(
        "public_sample_size >= 0 AND public_cohort_size >= 0",
        name="benchmark_public_counts_non_negative",
    ),
)

sa.Index(
    "ix_benchmark_snapshots_lookup",
    benchmark_snapshots.c.scope,
    benchmark_snapshots.c.provider_id,
    benchmark_snapshots.c.model_id,
    benchmark_snapshots.c.reasoning_profile,
    benchmark_snapshots.c.input_size_bucket,
    benchmark_snapshots.c.output_class,
    benchmark_snapshots.c.schema_class,
    benchmark_snapshots.c.as_of,
)
sa.Index(
    "ix_benchmark_snapshots_client",
    benchmark_snapshots.c.tenant_id,
    benchmark_snapshots.c.client_id,
    benchmark_snapshots.c.as_of,
)
