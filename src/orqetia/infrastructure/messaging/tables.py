"""SQLAlchemy Core tables owned by the infrastructure-only messaging schema."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

messaging_metadata = metadata_for_schema("messaging")

work_items = sa.Table(
    "work_items",
    messaging_metadata,
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
    sa.CheckConstraint("operation_version >= 1", name="operation_version_positive"),
    sa.CheckConstraint("priority BETWEEN -100 AND 100", name="priority_bounded"),
    sa.CheckConstraint(
        "max_infrastructure_attempts BETWEEN 1 AND 100",
        name="max_infrastructure_attempts_bounded",
    ),
    sa.CheckConstraint("state IN ('READY','LEASED','DONE','DEAD','CANCELLED')", name="state_known"),
    sa.CheckConstraint(
        "data_classification IN ('RESTRICTED','CONFIDENTIAL','CLIENT_PRIVATE','INTERNAL','PUBLIC')",
        name="classification_non_secret",
    ),
    sa.CheckConstraint(
        "data_classification <> 'CLIENT_PRIVATE' OR (tenant_id IS NOT NULL AND client_id IS NOT NULL)",
        name="client_private_scoped",
    ),
    sa.CheckConstraint("octet_length(payload::text) <= 65536", name="payload_at_most_64kib"),
)

sa.Index(
    "ix_work_items_claim",
    work_items.c.queue_name,
    work_items.c.state,
    work_items.c.available_at,
    work_items.c.priority,
)
sa.Index("ix_work_items_lease_until", work_items.c.lease_until)

event_deliveries = sa.Table(
    "event_deliveries",
    messaging_metadata,
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
    sa.UniqueConstraint("event_id", "consumer_name", name="uq_event_deliveries_event_consumer"),
    sa.CheckConstraint("event_version >= 1", name="event_version_positive"),
    sa.CheckConstraint("state IN ('READY','LEASED','ACKED','DEAD')", name="state_known"),
    sa.CheckConstraint(
        "data_classification IN ('RESTRICTED','CONFIDENTIAL','CLIENT_PRIVATE','INTERNAL','PUBLIC')",
        name="classification_non_secret",
    ),
    sa.CheckConstraint(
        "data_classification <> 'CLIENT_PRIVATE' OR (tenant_id IS NOT NULL AND client_id IS NOT NULL)",
        name="client_private_scoped",
    ),
    sa.CheckConstraint("octet_length(payload::text) <= 65536", name="payload_at_most_64kib"),
)

sa.Index(
    "ix_event_deliveries_claim",
    event_deliveries.c.consumer_name,
    event_deliveries.c.state,
    event_deliveries.c.available_at,
)
sa.Index("ix_event_deliveries_lease_until", event_deliveries.c.lease_until)
