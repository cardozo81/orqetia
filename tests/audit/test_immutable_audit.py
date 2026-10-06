from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid7

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from orqetia.audit import (
    AuditClass,
    CustomerActivityEvent,
    DEVELOPMENT_AUDIT_RETENTION_POLICY_V1,
    PostgresCustomerActivityStore,
    PostgresImmutableAuditStore,
    build_immutable_audit_event,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings

NOW = datetime(2026, 10, 6, 22, 0, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.skipif(
    "ORQETIA_DATABASE_DSN" not in os.environ,
    reason="PostgreSQL integration DSN is not configured",
)
async def test_audit_is_append_only_and_integrity_is_reconciled() -> None:
    engine = create_engine(RuntimeSettings())
    factory = create_session_factory(engine)
    store = PostgresImmutableAuditStore(factory)
    tenant_id = uuid7()
    client_id = uuid7()
    event = build_immutable_audit_event(
        audit_class=AuditClass.SECURITY_ADMIN,
        action="ADMIN_POLICY_CHANGE",
        result="SUCCESS",
        correlation_id="corr-audit-150",
        occurred_at=NOW,
        recorded_at=NOW,
        tenant_id=tenant_id,
        client_id=client_id,
        actor_type="BACKOFFICE_USER",
        actor_id=str(uuid7()),
        resource_type="EXECUTION_POLICY",
        resource_id=str(uuid7()),
    )

    try:
        await store.record(event)
        assert await store.find_integrity_failures(limit=100) == ()

        async with factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    sa.text(
                        "UPDATE audit.audit_events SET action = 'TAMPERED' "
                        "WHERE event_id = :event_id"
                    ),
                    {"event_id": event.event_id},
                )
                await session.commit()
            await session.rollback()

        async with factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    sa.text(
                        "DELETE FROM audit.audit_events WHERE event_id = :event_id"
                    ),
                    {"event_id": event.event_id},
                )
                await session.commit()
            await session.rollback()

        async with factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(sa.text("TRUNCATE audit.audit_events"))
                await session.commit()
            await session.rollback()

        activity_store = PostgresCustomerActivityStore(factory)
        activity_id = uuid7()
        identity_id = uuid7()
        membership_id = uuid7()
        await activity_store.record(
            CustomerActivityEvent(
                event_id=activity_id,
                tenant_id=tenant_id,
                client_id=client_id,
                identity_id=identity_id,
                membership_id=membership_id,
                action="TASK_CREATE",
                result="SUCCESS",
                occurred_at=NOW,
                correlation_id="corr-client-activity",
                resource_type="task",
                resource_id=str(uuid7()),
            )
        )
        activities = await activity_store.list_owned(
            tenant_id=tenant_id,
            client_id=client_id,
            limit=100,
        )
        assert any(
            item.event_id == activity_id
            and item.identity_id == identity_id
            and item.membership_id == membership_id
            and item.correlation_id == "corr-client-activity"
            for item in activities
        )

        forged_id = uuid7()
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    """
                    INSERT INTO audit.audit_events (
                        event_id, audit_class, action, result, correlation_id,
                        actor_type, actor_id, tenant_id, client_id,
                        resource_type, resource_id, occurred_at, recorded_at,
                        retention_policy_id, retention_policy_version,
                        retain_until, event_hash
                    ) VALUES (
                        :event_id, 'CLIENT_ACTIVITY', 'TASK_CREATE', 'SUCCESS',
                        'corr-forged', 'CUSTOMER_HUMAN', 'actor',
                        :tenant_id, :client_id, 'task', 'resource',
                        :occurred_at, :recorded_at,
                        :policy_id, :policy_version,
                        :retain_until, :event_hash
                    )
                    """
                ),
                {
                    "event_id": forged_id,
                    "tenant_id": tenant_id,
                    "client_id": client_id,
                    "occurred_at": NOW,
                    "recorded_at": NOW,
                    "policy_id": DEVELOPMENT_AUDIT_RETENTION_POLICY_V1.policy_id,
                    "policy_version": DEVELOPMENT_AUDIT_RETENTION_POLICY_V1.version,
                    "retain_until": NOW
                    + DEVELOPMENT_AUDIT_RETENTION_POLICY_V1.retention_for(
                        AuditClass.CLIENT_ACTIVITY
                    ),
                    "event_hash": "0" * 64,
                },
            )

        failures = await store.find_integrity_failures(limit=100)
        assert failures == (forged_id,)
    finally:
        await engine.dispose()
