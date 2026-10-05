"""messaging: create PostgreSQL durable work and event-delivery tables

Revision ID: 20261005_0002
Revises: 20261005_0001
Create Date: 2026-10-05

Ownership: infrastructure messaging (#104).
Bounded-context outbox/inbox tables remain owned by their authoritative schemas.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20261005_0002"
down_revision: str | None = "20261005_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("work_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("queue_name", sa.Text(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=False),
        sa.Column("operation_version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=True),
        sa.Column("data_classification", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("state", sa.Text(), nullable=False, server_default="READY"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_infrastructure_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("causation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("logical_operation_id", sa.Text(), nullable=True),
        sa.CheckConstraint("operation_version >= 1", name="ck_work_items_operation_version_positive"),
        sa.CheckConstraint("priority BETWEEN -100 AND 100", name="ck_work_items_priority_bounded"),
        sa.CheckConstraint(
            "max_infrastructure_attempts BETWEEN 1 AND 100",
            name="ck_work_items_max_infrastructure_attempts_bounded",
        ),
        sa.CheckConstraint(
            "state IN ('READY','LEASED','DONE','DEAD','CANCELLED')",
            name="ck_work_items_state_known",
        ),
        sa.CheckConstraint(
            "data_classification IN ('RESTRICTED','CONFIDENTIAL','CLIENT_PRIVATE','INTERNAL','PUBLIC')",
            name="ck_work_items_classification_non_secret",
        ),
        sa.CheckConstraint(
            "data_classification <> 'CLIENT_PRIVATE' OR (tenant_id IS NOT NULL AND client_id IS NOT NULL)",
            name="ck_work_items_client_private_scoped",
        ),
        sa.CheckConstraint(
            "octet_length(payload::text) <= 65536",
            name="ck_work_items_payload_at_most_64kib",
        ),
        schema="messaging",
    )
    op.create_index(
        "ix_work_items_claim",
        "work_items",
        ["queue_name", "state", "available_at", "priority"],
        schema="messaging",
    )
    op.create_index(
        "ix_work_items_lease_until",
        "work_items",
        ["lease_until"],
        schema="messaging",
    )

    op.create_table(
        "event_deliveries",
        sa.Column("delivery_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("consumer_name", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("producer", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", UUID(as_uuid=True), nullable=True),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=True),
        sa.Column("correlation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("causation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("data_classification", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="READY"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "event_id",
            "consumer_name",
            name="uq_event_deliveries_event_consumer",
        ),
        sa.CheckConstraint("event_version >= 1", name="ck_event_deliveries_event_version_positive"),
        sa.CheckConstraint(
            "state IN ('READY','LEASED','ACKED','DEAD')",
            name="ck_event_deliveries_state_known",
        ),
        sa.CheckConstraint(
            "data_classification IN ('RESTRICTED','CONFIDENTIAL','CLIENT_PRIVATE','INTERNAL','PUBLIC')",
            name="ck_event_deliveries_classification_non_secret",
        ),
        sa.CheckConstraint(
            "data_classification <> 'CLIENT_PRIVATE' OR (tenant_id IS NOT NULL AND client_id IS NOT NULL)",
            name="ck_event_deliveries_client_private_scoped",
        ),
        sa.CheckConstraint(
            "octet_length(payload::text) <= 65536",
            name="ck_event_deliveries_payload_at_most_64kib",
        ),
        schema="messaging",
    )
    op.create_index(
        "ix_event_deliveries_claim",
        "event_deliveries",
        ["consumer_name", "state", "available_at"],
        schema="messaging",
    )
    op.create_index(
        "ix_event_deliveries_lease_until",
        "event_deliveries",
        ["lease_until"],
        schema="messaging",
    )


def downgrade() -> None:
    op.drop_index("ix_event_deliveries_lease_until", table_name="event_deliveries", schema="messaging")
    op.drop_index("ix_event_deliveries_claim", table_name="event_deliveries", schema="messaging")
    op.drop_table("event_deliveries", schema="messaging")
    op.drop_index("ix_work_items_lease_until", table_name="work_items", schema="messaging")
    op.drop_index("ix_work_items_claim", table_name="work_items", schema="messaging")
    op.drop_table("work_items", schema="messaging")
