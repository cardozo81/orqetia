"""SQLAlchemy table for the immutable audit ledger."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

audit_metadata = metadata_for_schema("audit")

audit_events = sa.Table(
    "audit_events",
    audit_metadata,
    sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("audit_class", sa.Text(), nullable=False),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("result", sa.Text(), nullable=False),
    sa.Column("correlation_id", sa.Text(), nullable=False),
    sa.Column("actor_type", sa.Text(), nullable=True),
    sa.Column("actor_id", sa.Text(), nullable=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    sa.Column("client_id", UUID(as_uuid=True), nullable=True),
    sa.Column("resource_type", sa.Text(), nullable=True),
    sa.Column("resource_id", sa.Text(), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retention_policy_id", sa.Text(), nullable=False),
    sa.Column("retention_policy_version", sa.Integer(), nullable=False),
    sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
    sa.Column("event_hash", sa.String(64), nullable=False),
    sa.CheckConstraint(
        "audit_class IN ('SECURITY_ADMIN','CLIENT_ACTIVITY')",
        name="audit_event_class_known",
    ),
    sa.CheckConstraint(
        "char_length(action) BETWEEN 1 AND 100",
        name="audit_event_action_bounded",
    ),
    sa.CheckConstraint(
        "char_length(result) BETWEEN 1 AND 40",
        name="audit_event_result_bounded",
    ),
    sa.CheckConstraint(
        "char_length(correlation_id) BETWEEN 1 AND 200",
        name="audit_event_correlation_bounded",
    ),
    sa.CheckConstraint(
        "retention_policy_version >= 1",
        name="audit_event_retention_version_positive",
    ),
    sa.CheckConstraint(
        "recorded_at >= occurred_at AND retain_until > occurred_at",
        name="audit_event_time_order",
    ),
    sa.CheckConstraint(
        "event_hash ~ '^[0-9a-f]{64}$'",
        name="audit_event_hash_sha256_shape",
    ),
)

sa.Index(
    "ix_audit_events_class_time",
    audit_events.c.audit_class,
    audit_events.c.occurred_at,
    audit_events.c.event_id,
)
sa.Index(
    "ix_audit_events_owner_time",
    audit_events.c.tenant_id,
    audit_events.c.client_id,
    audit_events.c.occurred_at,
    audit_events.c.event_id,
)
sa.Index("ix_audit_events_retain_until", audit_events.c.retain_until)
