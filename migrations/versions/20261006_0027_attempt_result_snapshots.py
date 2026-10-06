"""execution: persist normalized provider result snapshots

Revision ID: 20261006_0027
Revises: 20261006_0026
Create Date: 2026-10-06

Ownership: crash-safe orchestration/accounting handoff (#145).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261006_0027"
down_revision: str | None = "20261006_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provider_attempts",
        sa.Column("usage_snapshot", JSONB(), nullable=True),
        schema="execution",
    )
    op.add_column(
        "provider_attempts",
        sa.Column("cost_snapshot", JSONB(), nullable=True),
        schema="execution",
    )


def downgrade() -> None:
    op.drop_column("provider_attempts", "cost_snapshot", schema="execution")
    op.drop_column("provider_attempts", "usage_snapshot", schema="execution")
