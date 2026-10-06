"""execution: persist orchestration cycle delay

Revision ID: 20261006_0025
Revises: 20261006_0024
Create Date: 2026-10-06

Ownership: durable task orchestration recovery (#145).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0025"
down_revision: str | None = "20261006_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_cycle_decisions",
        sa.Column("delay_seconds", sa.Integer(), nullable=False, server_default="0"),
        schema="execution",
    )
    op.create_check_constraint(
        "ck_task_cycle_decisions_delay_seconds_non_negative",
        "task_cycle_decisions",
        "delay_seconds >= 0",
        schema="execution",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_task_cycle_decisions_delay_seconds_non_negative",
        "task_cycle_decisions",
        schema="execution",
        type_="check",
    )
    op.drop_column("task_cycle_decisions", "delay_seconds", schema="execution")
