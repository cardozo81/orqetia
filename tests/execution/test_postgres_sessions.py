from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime
from uuid import uuid7

import sqlalchemy as sa

from orqetia.execution import (
    ExecutionSession,
    ExecutionTargetSnapshot,
    OwnershipScope,
    PostgresExecutionSessionStore,
    QuarantineState,
    SessionPolicySnapshot,
    SessionStatus,
    TargetHealth,
    TargetRuntimeState,
    session_target_runtime,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings


class PostgreSQLExecutionSessionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ORQETIA_DATABASE_DSN" not in os.environ:
            raise unittest.SkipTest("PostgreSQL integration DSN is not configured")

    def setUp(self) -> None:
        asyncio.run(self._truncate())

    def test_reads_are_scoped_by_tenant_and_client(self) -> None:
        asyncio.run(self._test_owned_read())

    def test_terminal_quarantine_persists_and_cannot_be_weakened(self) -> None:
        asyncio.run(self._test_terminal_quarantine())

    def test_database_rejects_child_owner_mismatch(self) -> None:
        asyncio.run(self._test_child_owner_mismatch())

    async def _resources(self):
        settings = RuntimeSettings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        return engine, factory

    async def _truncate(self) -> None:
        engine, _ = await self._resources()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text(
                        "TRUNCATE execution.session_target_runtime, "
                        "execution.execution_sessions CASCADE"
                    )
                )
        finally:
            await engine.dispose()

    async def _test_owned_read(self) -> None:
        engine, factory = await self._resources()
        store = PostgresExecutionSessionStore(factory)
        try:
            session = self._session()
            await store.create(session)

            owned = await store.get_owned(
                scope=session.ownership,
                session_id=session.session_id,
            )
            wrong_tenant = await store.get_owned(
                scope=OwnershipScope(uuid7(), session.ownership.client_id),
                session_id=session.session_id,
            )
            wrong_client = await store.get_owned(
                scope=OwnershipScope(session.ownership.tenant_id, uuid7()),
                session_id=session.session_id,
            )

            self.assertIsNotNone(owned)
            self.assertIsNone(wrong_tenant)
            self.assertIsNone(wrong_client)
            assert owned is not None
            self.assertEqual(owned.policy, session.policy)
            self.assertEqual(owned.internal_cost_reference_ids, session.internal_cost_reference_ids)
        finally:
            await engine.dispose()

    async def _test_terminal_quarantine(self) -> None:
        engine, factory = await self._resources()
        store = PostgresExecutionSessionStore(factory)
        try:
            session = self._session()
            await store.create(session)
            target = session.policy.authorized_targets[0]
            terminal = TargetRuntimeState(
                target=target,
                health=TargetHealth.UNAVAILABLE,
                quarantine=QuarantineState.TERMINAL,
                quarantine_reason_code="AUTH_FAILURE",
                updated_at=datetime.now(UTC),
            )
            self.assertTrue(
                await store.put_target_runtime_state(
                    scope=session.ownership,
                    session_id=session.session_id,
                    state=terminal,
                )
            )

            weakened = TargetRuntimeState(
                target=target,
                health=TargetHealth.HEALTHY,
                quarantine=QuarantineState.NONE,
                updated_at=datetime.now(UTC),
            )
            self.assertFalse(
                await store.put_target_runtime_state(
                    scope=session.ownership,
                    session_id=session.session_id,
                    state=weakened,
                )
            )

            reloaded = await store.get_owned(
                scope=session.ownership,
                session_id=session.session_id,
            )
            assert reloaded is not None
            self.assertEqual(len(reloaded.target_runtime), 1)
            self.assertEqual(
                reloaded.target_runtime[0].quarantine,
                QuarantineState.TERMINAL,
            )
            self.assertEqual(
                reloaded.target_runtime[0].quarantine_reason_code,
                "AUTH_FAILURE",
            )
            self.assertGreaterEqual(reloaded.version, 2)
        finally:
            await engine.dispose()

    async def _test_child_owner_mismatch(self) -> None:
        engine, factory = await self._resources()
        store = PostgresExecutionSessionStore(factory)
        try:
            session = self._session()
            await store.create(session)
            target = session.policy.authorized_targets[0]

            with self.assertRaises(sa.exc.IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(
                        sa.insert(session_target_runtime).values(
                            session_id=session.session_id,
                            tenant_id=session.ownership.tenant_id,
                            client_id=uuid7(),
                            provider_id=target.provider_id,
                            model_id=target.model_id,
                            reasoning_profile=target.reasoning_profile,
                            health_state="UNKNOWN",
                            quarantine_state="NONE",
                            updated_at=datetime.now(UTC),
                        )
                    )
        finally:
            await engine.dispose()

    @staticmethod
    def _session() -> ExecutionSession:
        now = datetime.now(UTC)
        target = ExecutionTargetSnapshot("OPENAI", "gpt-test", "standard")
        return ExecutionSession(
            session_id=uuid7(),
            ownership=OwnershipScope(uuid7(), uuid7()),
            status=SessionStatus.ACTIVE,
            policy=SessionPolicySnapshot(
                requested_policy_version_id=None,
                effective_policy_version_id=uuid7(),
                authorized_targets=(target,),
            ),
            external_reference="integration-test",
            internal_cost_reference_ids=(uuid7(),),
            created_at=now,
            updated_at=now,
        )


if __name__ == "__main__":
    unittest.main()
