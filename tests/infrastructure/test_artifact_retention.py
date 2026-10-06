from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
import sqlalchemy as sa

from orqetia.execution import (
    ClientArtifactRetentionCoordinator,
    ClientArtifactRetentionPolicy,
    OwnershipScope,
    RetentionHold,
)
from orqetia.infrastructure.artifacts import (
    ArtifactNotFound,
    PostgresClientArtifactStore,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings

NOW = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.skipif(
    "ORQETIA_DATABASE_DSN" not in os.environ,
    reason="PostgreSQL integration DSN is not configured",
)
async def test_retention_is_selective_owned_idempotent_and_hold_aware() -> None:
    settings = RuntimeSettings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    store = PostgresClientArtifactStore(factory)
    coordinator = ClientArtifactRetentionCoordinator(store)
    scope = OwnershipScope(uuid7(), uuid7())
    other = OwnershipScope(uuid7(), uuid7())
    old_request_task = uuid7()
    old_result_task = uuid7()
    other_task = uuid7()
    policy = ClientArtifactRetentionPolicy(
        version=1,
        request_days=30,
        result_days=60,
        provider_response_days=14,
        evidence_days=14,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "TRUNCATE execution.client_exchange_evidence, "
                    "execution.client_artifacts CASCADE"
                )
            )

        old_request = await store.store_request(
            scope=scope,
            task_id=old_request_task,
            payload={"input_text": "old request"},
            occurred_at=NOW - timedelta(days=31),
        )
        old_result = await store.store_result(
            scope=scope,
            task_id=old_result_task,
            result={"output_text": "retained result"},
            occurred_at=NOW - timedelta(days=31),
        )
        other_request = await store.store_request(
            scope=other,
            task_id=other_task,
            payload={"input_text": "other owner"},
            occurred_at=NOW - timedelta(days=90),
        )

        purged = await coordinator.purge_due(
            scope=scope,
            policy=policy,
            occurred_at=NOW,
            hold=None,
        )
        assert purged.deleted_count == 1
        assert not purged.skipped_due_to_hold

        with pytest.raises(ArtifactNotFound):
            await store.load(
                request_reference=old_request.input_reference,
                request_fingerprint=old_request.input_fingerprint,
            )
        assert await store.read_result(
            scope=scope,
            result_reference=old_result,
        ) == {"output_text": "retained result"}
        assert (
            await store.load(
                request_reference=other_request.input_reference,
                request_fingerprint=other_request.input_fingerprint,
            )
        ).input_text == "other owner"

        replay = await coordinator.purge_due(
            scope=scope,
            policy=policy,
            occurred_at=NOW,
            hold=None,
        )
        assert replay.deleted_count == 0

        held = await coordinator.purge_due(
            scope=scope,
            policy=ClientArtifactRetentionPolicy(
                version=2,
                request_days=1,
                result_days=1,
                provider_response_days=1,
                evidence_days=1,
            ),
            occurred_at=NOW,
            hold=RetentionHold(
                reason_code="SECURITY_INVESTIGATION",
                created_at=NOW - timedelta(hours=1),
            ),
        )
        assert held.skipped_due_to_hold
        assert await store.read_result(
            scope=scope,
            result_reference=old_result,
        ) == {"output_text": "retained result"}

        offboarded = await coordinator.purge_offboarded_owner(
            scope=scope,
            occurred_at=NOW,
            hold=None,
        )
        assert offboarded.deleted_count == 1
        assert await store.read_result(
            scope=scope,
            result_reference=old_result,
        ) is None
        assert (
            await store.load(
                request_reference=other_request.input_reference,
                request_fingerprint=other_request.input_fingerprint,
            )
        ).input_text == "other owner"
    finally:
        await engine.dispose()
