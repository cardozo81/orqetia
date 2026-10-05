"""foundation: create ORQETIA logical PostgreSQL schemas

Revision ID: 20261005_0001
Revises:
Create Date: 2026-10-05

Ownership: migration infrastructure only.
This revision creates namespaces, not business tables. Future table migrations
must identify one authoritative bounded-context owner and must not introduce
cross-context foreign keys.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261005_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = (
    "identity",
    "control",
    "execution",
    "accounting",
    "estimation",
    "audit",
    "readmodel",
    "messaging",
)


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def downgrade() -> None:
    # Safe for this foundation revision because it creates no tables.
    for schema in reversed(SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}"')
