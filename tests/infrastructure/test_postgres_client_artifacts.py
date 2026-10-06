from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import sqlalchemy as sa

from orqetia.execution import (
    ClientExchangeEvidence,
    OwnershipScope,
    SanitizedEvidenceRecord,
    provider_attempts,
)
from orqetia.infrastructure.artifacts import ArtifactConflict, ArtifactTooLarge, PostgresClientArtifactStore
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.providers import OutputKind, ProviderTarget
from orqetia.settings import RuntimeSettings


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PostgreSQLClientArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ORQETIA_DATABASE_DSN" not in os.environ:
            raise unittest.SkipTest("PostgreSQL integration DSN is not configured")

    def setUp(self) -> None:
        asyncio.run(self._truncate())

    def test_owned_round_trip_limits_and_retention(self) -> None:
        asyncio.run(self._test_owned_round_trip_limits_and_retention())

    async def _resources(self):
        settings = RuntimeSettings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        return engine, PostgresClientArtifactStore(factory)

    async def _truncate(self) -> None:
        engine, _ = await self._resources()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text(
                        "TRUNCATE execution.client_exchange_evidence, "
                        "execution.client_artifacts, execution.provider_attempts CASCADE"
                    )
                )
        finally:
            await engine.dispose()

    async def _test_owned_round_trip_limits_and_retention(self) -> None:
        engine, store = await self._resources()
        scope = OwnershipScope(tenant_id=uuid7(), client_id=uuid7())
        other = OwnershipScope(tenant_id=uuid7(), client_id=scope.client_id)
        task_id = uuid7()
        attempt_id = uuid7()
        now = datetime.now(UTC)
        payload = {"input_text": "hello", "instructions": "be concise"}

        try:
            refs = await store.store_request(
                scope=scope,
                task_id=task_id,
                payload=payload,
                occurred_at=now,
            )
            self.assertEqual(refs.input_fingerprint, _fingerprint(payload))
            replay = await store.store_request(
                scope=scope,
                task_id=task_id,
                payload=payload,
                occurred_at=now,
            )
            self.assertEqual(replay, refs)

            materialized = await store.load(
                request_reference=refs.input_reference,
                request_fingerprint=refs.input_fingerprint,
            )
            self.assertEqual(materialized.input_text, "hello")
            self.assertEqual(materialized.instructions, "be concise")
            with self.assertRaises(ArtifactConflict):
                await store.load(
                    request_reference=refs.input_reference,
                    request_fingerprint="0" * 64,
                )

            with self.assertRaises(ArtifactTooLarge):
                await store.store_request(
                    scope=scope,
                    task_id=uuid7(),
                    payload={"input_text": "x" * 1_048_577},
                    occurred_at=now,
                )

            async with engine.begin() as connection:
                await connection.execute(
                    sa.insert(provider_attempts).values(
                        attempt_id=attempt_id,
                        task_id=None,
                        session_id=None,
                        tenant_id=scope.tenant_id,
                        client_id=scope.client_id,
                        operation="TASK_EXECUTION",
                        provider_id="alpha",
                        model_id="model-a",
                        reasoning_profile="default",
                        cycle=1,
                        attempt_index=1,
                        request_reference=refs.input_reference,
                        request_fingerprint=refs.input_fingerprint,
                        status="PREPARED",
                    )
                )

            response_reference = await store.store(
                attempt_id=attempt_id,
                target=ProviderTarget(
                    provider_id="alpha",
                    model_id="model-a",
                    reasoning_profile="default",
                ),
                output_kind=OutputKind.TEXT,
                content="safe output",
            )
            self.assertEqual(
                await store.read_result(
                    scope=scope,
                    result_reference=response_reference,
                ),
                {"output_text": "safe output"},
            )
            self.assertIsNone(
                await store.read_result(
                    scope=other,
                    result_reference=response_reference,
                )
            )

            raw = '{"safe":true}'
            evidence_record = SanitizedEvidenceRecord(
                media_type="application/json",
                sanitized_raw_body=raw,
                sanitized_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            )
            evidence = ClientExchangeEvidence(
                exchange_id=uuid7(),
                attempt_id=attempt_id,
                provider_id="alpha",
                provider_name="Alpha Provider",
                operation="TASK_EXECUTION",
                status="SUCCEEDED",
                status_label="Succeeded",
                request_evidence=evidence_record,
            )
            await store.store_exchange(
                scope=scope,
                evidence=evidence,
                occurred_at=now,
            )
            self.assertEqual(
                await store.list_for_attempt(scope=scope, attempt_id=attempt_id),
                (evidence,),
            )
            self.assertEqual(
                await store.list_for_attempt(scope=other, attempt_id=attempt_id),
                (),
            )

            deleted = await store.delete_owned_before(
                scope=scope,
                older_than=now + timedelta(minutes=1),
            )
            self.assertGreaterEqual(deleted, 3)
            self.assertIsNone(
                await store.read_result(
                    scope=scope,
                    result_reference=response_reference,
                )
            )
            self.assertEqual(
                await store.list_for_attempt(scope=scope, attempt_id=attempt_id),
                (),
            )
        finally:
            await engine.dispose()
