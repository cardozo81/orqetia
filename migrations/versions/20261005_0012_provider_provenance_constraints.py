"""provider control: enforce provider account/credential provenance pairing

Revision ID: 20261005_0012
Revises: 20261005_0011
Create Date: 2026-10-05

Ownership: Control Plane provider account control (#59).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261005_0012"
down_revision: str | None = "20261005_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_provider_attempts_credential_requires_account",
        "provider_attempts",
        "provider_credential_id IS NULL OR provider_account_id IS NOT NULL",
        schema="execution",
    )
    op.create_check_constraint(
        "ck_usage_ledger_credential_requires_account",
        "usage_ledger",
        "provider_credential_id IS NULL OR provider_account_id IS NOT NULL",
        schema="accounting",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_usage_ledger_credential_requires_account",
        "usage_ledger",
        schema="accounting",
        type_="check",
    )
    op.drop_constraint(
        "ck_provider_attempts_credential_requires_account",
        "provider_attempts",
        schema="execution",
        type_="check",
    )
