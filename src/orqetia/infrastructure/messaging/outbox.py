"""Owner-schema outbox/inbox table factories."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def _require_owner_schema(metadata: sa.MetaData) -> str:
    schema = metadata.schema
    if schema is None or schema in {"messaging", "readmodel"}:
        raise ValueError("outbox/inbox metadata must belong to an authoritative owner schema")
    return schema


def build_outbox_table(metadata: sa.MetaData) -> sa.Table:
    _require_owner_schema(metadata)
    return sa.Table(
        "outbox_events",
        metadata,
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("producer", sa.Text(), nullable=False),
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
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("publish_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.Text(), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','CLAIMED','PUBLISHED','DEAD')", name="outbox_state_known"
        ),
    )


def build_inbox_table(metadata: sa.MetaData) -> sa.Table:
    _require_owner_schema(metadata)
    return sa.Table(
        "inbox_events",
        metadata,
        sa.Column("consumer_name", sa.Text(), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("state", sa.Text(), nullable=False, server_default="RECEIVED"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_class", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "state IN ('RECEIVED','PROCESSING','PROCESSED','DEAD')", name="inbox_state_known"
        ),
    )
