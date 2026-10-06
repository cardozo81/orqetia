"""readmodel: extend reporting rollups for operational intelligence

Revision ID: 20261005_0015
Revises: 20261005_0014
Create Date: 2026-10-05

Ownership: Backoffice operational & financial intelligence (#60).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261005_0015"
down_revision: str | None = "20261005_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "report_rollups",
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column("attempt_id", UUID(as_uuid=True), nullable=True),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column("policy_version_id", UUID(as_uuid=True), nullable=True),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column(
            "requests",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column(
            "tasks",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column(
            "peak_concurrent",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column("quota_utilization", sa.Numeric(20, 12), nullable=True),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column(
            "fallbacks",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column(
            "health_events",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        schema="readmodel",
    )
    op.add_column(
        "report_rollups",
        sa.Column(
            "quarantine_events",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        schema="readmodel",
    )

    op.create_check_constraint(
        "ck_report_rollups_operational_metrics_non_negative",
        "report_rollups",
        "requests >= 0 AND tasks >= 0 AND peak_concurrent >= 0 "
        "AND fallbacks >= 0 AND health_events >= 0 AND quarantine_events >= 0",
        schema="readmodel",
    )
    op.create_check_constraint(
        "ck_report_rollups_quota_utilization_range",
        "report_rollups",
        "quota_utilization IS NULL OR "
        "(quota_utilization >= 0 AND quota_utilization <= 1)",
        schema="readmodel",
    )
    op.create_index(
        "ix_report_rollups_session_task_attempt",
        "report_rollups",
        ["session_id", "task_id", "attempt_id"],
        schema="readmodel",
    )
    op.create_index(
        "ix_report_rollups_policy_period",
        "report_rollups",
        ["policy_version_id", "period_start"],
        schema="readmodel",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_rollups_policy_period",
        table_name="report_rollups",
        schema="readmodel",
    )
    op.drop_index(
        "ix_report_rollups_session_task_attempt",
        table_name="report_rollups",
        schema="readmodel",
    )
    op.drop_constraint(
        "ck_report_rollups_quota_utilization_range",
        "report_rollups",
        schema="readmodel",
        type_="check",
    )
    op.drop_constraint(
        "ck_report_rollups_operational_metrics_non_negative",
        "report_rollups",
        schema="readmodel",
        type_="check",
    )
    for column in (
        "quarantine_events",
        "health_events",
        "fallbacks",
        "quota_utilization",
        "peak_concurrent",
        "tasks",
        "requests",
        "policy_version_id",
        "attempt_id",
        "task_id",
        "session_id",
    ):
        op.drop_column("report_rollups", column, schema="readmodel")
