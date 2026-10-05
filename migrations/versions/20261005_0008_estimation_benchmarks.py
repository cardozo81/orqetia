"""estimation: add privacy-governed benchmark snapshots

Revision ID: 20261005_0008
Revises: 20261005_0007
Create Date: 2026-10-05

Ownership: Statistical Estimation bounded context (#11).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261005_0008"
down_revision: str | None = "20261005_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "benchmark_snapshots",
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
            name="ck_benchmark_snapshots_scope_known",
        ),
        sa.CheckConstraint(
            "(scope = 'CLIENT_ONLY' AND tenant_id IS NOT NULL "
            "AND client_id IS NOT NULL) OR "
            "(scope = 'GLOBAL_PUBLIC' AND tenant_id IS NULL AND client_id IS NULL)",
            name="ck_benchmark_snapshots_scope_owner_consistent",
        ),
        sa.CheckConstraint(
            "sample_count >= 1",
            name="ck_benchmark_snapshots_samples_positive",
        ),
        sa.CheckConstraint(
            "distinct_client_count >= 1",
            name="ck_benchmark_snapshots_clients_positive",
        ),
        sa.CheckConstraint(
            "public_sample_size >= 0 AND public_cohort_size >= 0",
            name="ck_benchmark_snapshots_public_counts_non_negative",
        ),
        schema="estimation",
    )
    op.create_index(
        "ix_benchmark_snapshots_lookup",
        "benchmark_snapshots",
        [
            "scope",
            "provider_id",
            "model_id",
            "reasoning_profile",
            "input_size_bucket",
            "output_class",
            "schema_class",
            "as_of",
        ],
        schema="estimation",
    )
    op.create_index(
        "ix_benchmark_snapshots_client",
        "benchmark_snapshots",
        ["tenant_id", "client_id", "as_of"],
        schema="estimation",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_benchmark_snapshots_client",
        table_name="benchmark_snapshots",
        schema="estimation",
    )
    op.drop_index(
        "ix_benchmark_snapshots_lookup",
        table_name="benchmark_snapshots",
        schema="estimation",
    )
    op.drop_table("benchmark_snapshots", schema="estimation")
