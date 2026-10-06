"""execution: freeze pricing catalog version on provider attempts

Revision ID: 20261006_0028
Revises: 20261006_0027
Create Date: 2026-10-06

Ownership: immutable provider-cost replay (#145).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20261006_0028"
down_revision: str | None = "20261006_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provider_attempts",
        sa.Column(
            "pricing_catalog_version_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        schema="execution",
    )


def downgrade() -> None:
    op.drop_column(
        "provider_attempts",
        "pricing_catalog_version_id",
        schema="execution",
    )
