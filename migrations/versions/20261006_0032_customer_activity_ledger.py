"""audit: migrate client activity to immutable ledger

Revision ID: 20261006_0032
Revises: 20261006_0031
Create Date: 2026-10-06

Ownership: audit client-activity convergence (#150).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0032"
down_revision: str | None = "20261006_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_ID = "ORQETIA_AUDIT_DEVELOPMENT"
_POLICY_VERSION = 1
_CLIENT_ACTIVITY_DAYS = 365
_ACTOR_TYPE = "CUSTOMER_HUMAN_MEMBERSHIP"


def _hash(values: dict[str, object]) -> str:
    payload = {
        "event_id": str(values["event_id"]),
        "audit_class": "CLIENT_ACTIVITY",
        "action": values["action"],
        "result": values["result"],
        "correlation_id": values["correlation_id"],
        "occurred_at": values["occurred_at"].isoformat(),
        "recorded_at": values["recorded_at"].isoformat(),
        "retention_policy_id": _POLICY_ID,
        "retention_policy_version": _POLICY_VERSION,
        "retain_until": values["retain_until"].isoformat(),
        "actor_type": _ACTOR_TYPE,
        "actor_id": values["actor_id"],
        "tenant_id": str(values["tenant_id"]),
        "client_id": str(values["client_id"]),
        "resource_type": values["resource_type"],
        "resource_id": values["resource_id"],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT event_id, tenant_id, client_id, identity_id, membership_id,
                   action, result, resource_type, resource_id, occurred_at
            FROM audit.customer_activity_events
            ORDER BY occurred_at, event_id
            """
        )
    ).mappings().all()

    for row in rows:
        occurred_at = row["occurred_at"]
        values: dict[str, object] = {
            "event_id": row["event_id"],
            "tenant_id": row["tenant_id"],
            "client_id": row["client_id"],
            "action": row["action"],
            "result": row["result"],
            "correlation_id": str(row["event_id"]),
            "occurred_at": occurred_at,
            "recorded_at": occurred_at,
            "retain_until": occurred_at + timedelta(days=_CLIENT_ACTIVITY_DAYS),
            "actor_id": f"{row['identity_id']}:{row['membership_id']}",
            "resource_type": row["resource_type"],
            "resource_id": row["resource_id"],
        }
        connection.execute(
            sa.text(
                """
                INSERT INTO audit.audit_events (
                    event_id, audit_class, action, result, correlation_id,
                    actor_type, actor_id, tenant_id, client_id,
                    resource_type, resource_id, occurred_at, recorded_at,
                    retention_policy_id, retention_policy_version,
                    retain_until, event_hash
                ) VALUES (
                    :event_id, 'CLIENT_ACTIVITY', :action, :result, :correlation_id,
                    :actor_type, :actor_id, :tenant_id, :client_id,
                    :resource_type, :resource_id, :occurred_at, :recorded_at,
                    :policy_id, :policy_version, :retain_until, :event_hash
                )
                ON CONFLICT (event_id) DO NOTHING
                """
            ),
            {
                **values,
                "actor_type": _ACTOR_TYPE,
                "policy_id": _POLICY_ID,
                "policy_version": _POLICY_VERSION,
                "event_hash": _hash(values),
            },
        )

    connection.execute(sa.text("TRUNCATE audit.customer_activity_events"))

    op.execute(
        """
        CREATE TRIGGER trg_audit_events_no_truncate
        BEFORE TRUNCATE ON audit.audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_immutable_audit_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_customer_activity_no_truncate
        BEFORE TRUNCATE ON audit.customer_activity_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_immutable_audit_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_customer_activity_no_truncate "
        "ON audit.customer_activity_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_events_no_truncate ON audit.audit_events"
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT event_id, tenant_id, client_id, actor_id, action, result,
                   resource_type, resource_id, occurred_at
            FROM audit.audit_events
            WHERE audit_class = 'CLIENT_ACTIVITY'
              AND actor_type = :actor_type
            ORDER BY occurred_at, event_id
            """
        ),
        {"actor_type": _ACTOR_TYPE},
    ).mappings().all()
    for row in rows:
        identity_raw, membership_raw = str(row["actor_id"]).split(":", 1)
        connection.execute(
            sa.text(
                """
                INSERT INTO audit.customer_activity_events (
                    event_id, tenant_id, client_id, identity_id, membership_id,
                    action, result, resource_type, resource_id, occurred_at
                ) VALUES (
                    :event_id, :tenant_id, :client_id, :identity_id, :membership_id,
                    :action, :result, :resource_type, :resource_id, :occurred_at
                )
                ON CONFLICT (event_id) DO NOTHING
                """
            ),
            {
                "event_id": row["event_id"],
                "tenant_id": row["tenant_id"],
                "client_id": row["client_id"],
                "identity_id": UUID(identity_raw),
                "membership_id": UUID(membership_raw),
                "action": row["action"],
                "result": row["result"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "occurred_at": row["occurred_at"],
            },
        )
