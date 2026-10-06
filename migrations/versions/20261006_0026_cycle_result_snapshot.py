"""execution: preserve partial result reference across cycles

Revision ID: 20261006_0026
Revises: 20261006_0025
Create Date: 2026-10-06

Ownership: durable orchestration progress (#145).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0026"
down_revision: str | None = "20261006_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_cycle_decisions",
        sa.Column("result_reference_snapshot", sa.Text(), nullable=True),
        schema="execution",
    )
    op.create_check_constraint(
        "ck_task_cycle_decisions_result_reference_snapshot_bounded",
        "task_cycle_decisions",
        "result_reference_snapshot IS NULL "
        "OR char_length(result_reference_snapshot) <= 500",
        schema="execution",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_task_cycle_decisions_result_reference_snapshot_bounded",
        "task_cycle_decisions",
        schema="execution",
        type_="check",
    )
    op.drop_column(
        "task_cycle_decisions",
        "result_reference_snapshot",
        schema="execution",
    )
