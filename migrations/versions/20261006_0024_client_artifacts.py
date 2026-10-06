"""execution: add durable client-private artifacts

Revision ID: 20261006_0024
Revises: 20261005_0023
Create Date: 2026-10-06

Ownership: durable client-private artifact storage (#144).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261006_0024"
down_revision: str | None = "20261005_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_artifacts",
        sa.Column("artifact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("content_json", JSONB(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("output_kind", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('REQUEST','RESULT','PROVIDER_RESPONSE')",
            name="ck_client_artifacts_kind_known",
        ),
        sa.CheckConstraint(
            "char_length(media_type) BETWEEN 1 AND 200",
            name="ck_client_artifacts_media_type_bounded",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_client_artifacts_sha256_valid",
        ),
        sa.CheckConstraint(
            "byte_size BETWEEN 1 AND 2097152",
            name="ck_client_artifacts_byte_size_bounded",
        ),
        sa.CheckConstraint(
            "(kind IN ('REQUEST','RESULT') AND content_json IS NOT NULL "
            "AND jsonb_typeof(content_json) = 'object' AND content_text IS NULL "
            "AND output_kind IS NULL) OR "
            "(kind = 'PROVIDER_RESPONSE' AND content_json IS NULL "
            "AND content_text IS NOT NULL AND output_kind IS NOT NULL)",
            name="ck_client_artifacts_content_shape",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["execution.provider_attempts.attempt_id"],
            name="fk_client_artifacts_attempt",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_client_artifacts"),
        schema="execution",
    )
    op.create_index(
        "ix_client_artifacts_owner_task_kind",
        "client_artifacts",
        ["tenant_id", "client_id", "task_id", "kind"],
        schema="execution",
    )
    op.create_index(
        "ix_client_artifacts_owner_created",
        "client_artifacts",
        ["tenant_id", "client_id", "created_at"],
        schema="execution",
    )
    op.create_index(
        "ix_client_artifacts_attempt",
        "client_artifacts",
        ["attempt_id"],
        schema="execution",
    )

    op.create_table(
        "client_exchange_evidence",
        sa.Column("exchange_id", UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_label", sa.Text(), nullable=False),
        sa.Column("request_media_type", sa.Text(), nullable=False),
        sa.Column("request_body", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("request_truncated", sa.Boolean(), nullable=False),
        sa.Column("request_byte_size", sa.Integer(), nullable=False),
        sa.Column("response_media_type", sa.Text(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("response_sha256", sa.Text(), nullable=True),
        sa.Column("response_truncated", sa.Boolean(), nullable=True),
        sa.Column("response_byte_size", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(provider_id) BETWEEN 1 AND 100 "
            "AND char_length(provider_name) BETWEEN 1 AND 200 "
            "AND char_length(operation) BETWEEN 1 AND 100 "
            "AND char_length(status) BETWEEN 1 AND 100 "
            "AND char_length(status_label) BETWEEN 1 AND 200",
            name="ck_client_exchange_evidence_metadata_bounded",
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' "
            "AND request_byte_size BETWEEN 0 AND 262144",
            name="ck_client_exchange_evidence_request_evidence_valid",
        ),
        sa.CheckConstraint(
            "(response_body IS NULL AND response_media_type IS NULL "
            "AND response_sha256 IS NULL AND response_truncated IS NULL "
            "AND response_byte_size IS NULL) OR "
            "(response_body IS NOT NULL AND response_media_type IS NOT NULL "
            "AND response_sha256 ~ '^[0-9a-f]{64}$' "
            "AND response_truncated IS NOT NULL "
            "AND response_byte_size BETWEEN 0 AND 262144)",
            name="ck_client_exchange_evidence_response_evidence_shape",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["execution.provider_attempts.attempt_id"],
            name="fk_client_exchange_evidence_attempt",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("exchange_id", name="pk_client_exchange_evidence"),
        schema="execution",
    )
    op.create_index(
        "ix_client_exchange_evidence_owner_attempt_created",
        "client_exchange_evidence",
        ["tenant_id", "client_id", "attempt_id", "created_at", "exchange_id"],
        schema="execution",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_client_exchange_evidence_owner_attempt_created",
        table_name="client_exchange_evidence",
        schema="execution",
    )
    op.drop_table("client_exchange_evidence", schema="execution")
    op.drop_index(
        "ix_client_artifacts_attempt",
        table_name="client_artifacts",
        schema="execution",
    )
    op.drop_index(
        "ix_client_artifacts_owner_created",
        table_name="client_artifacts",
        schema="execution",
    )
    op.drop_index(
        "ix_client_artifacts_owner_task_kind",
        table_name="client_artifacts",
        schema="execution",
    )
    op.drop_table("client_artifacts", schema="execution")
