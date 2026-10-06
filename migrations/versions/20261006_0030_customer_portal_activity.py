"""audit: add Customer Portal activity events

Revision ID: 20261006_0030
Revises: 20261006_0029
Create Date: 2026-10-06

Ownership: client-visible Portal activity (#23).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261006_0030"
down_revision: str | None = "20261006_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_activity_events",
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
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
            name="ck_customer_activity_events_action_bounded",
        ),
        sa.CheckConstraint(
            "result IN ('SUCCESS','DENIED')",
            name="ck_customer_activity_events_result_known",
        ),
        sa.CheckConstraint(
            "resource_type IS NULL OR "
            "char_length(resource_type) BETWEEN 1 AND 100",
            name="ck_customer_activity_events_resource_type_bounded",
        ),
        sa.CheckConstraint(
            "resource_id IS NULL OR "
            "char_length(resource_id) BETWEEN 1 AND 500",
            name="ck_customer_activity_events_resource_id_bounded",
        ),
        sa.PrimaryKeyConstraint(
            "event_id",
            name="pk_customer_activity_events",
        ),
        schema="audit",
    )
    op.create_index(
        "ix_customer_activity_owner_time",
        "customer_activity_events",
        ["tenant_id", "client_id", "occurred_at", "event_id"],
        schema="audit",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_activity_owner_time",
        table_name="customer_activity_events",
        schema="audit",
    )
    op.drop_table("customer_activity_events", schema="audit")
