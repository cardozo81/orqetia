"""Audit-owned Customer Portal activity table."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

customer_activity_metadata = metadata_for_schema("audit")

customer_activity_events = sa.Table(
    "customer_activity_events",
    customer_activity_metadata,
    sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("client_id", UUID(as_uuid=True), nullable=False),
    sa.Column("identity_id", UUID(as_uuid=True), nullable=False),
    sa.Column("membership_id", UUID(as_uuid=True), nullable=False),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("result", sa.Text(), nullable=False),
    sa.Column("resource_type", sa.Text(), nullable=True),
    sa.Column("resource_id", sa.Text(), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "char_length(action) BETWEEN 1 AND 100",
        name="customer_activity_action_bounded",
    ),
    sa.CheckConstraint(
        "result IN ('SUCCESS','DENIED')",
        name="customer_activity_result_known",
    ),
    sa.CheckConstraint(
        "resource_type IS NULL OR char_length(resource_type) BETWEEN 1 AND 100",
        name="customer_activity_resource_type_bounded",
    ),
    sa.CheckConstraint(
        "resource_id IS NULL OR char_length(resource_id) BETWEEN 1 AND 500",
        name="customer_activity_resource_id_bounded",
    ),
)

sa.Index(
    "ix_customer_activity_owner_time",
    customer_activity_events.c.tenant_id,
    customer_activity_events.c.client_id,
    customer_activity_events.c.occurred_at,
    customer_activity_events.c.event_id,
)
