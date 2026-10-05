from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime
from uuid import uuid7

import sqlalchemy as sa

from orqetia.execution import (
    ExecutionMode,
    ExecutionSession,
    ExecutionTargetSnapshot,
    ExecutionTask,
    OwnershipScope,
    PostgresExecutionSessionStore,
    PostgresExecutionTaskStore,
    RequestedTargetSnapshot,
    SessionPolicySnapshot,
    SessionStatus,
    TaskCycleDecision,
    TaskPayloadReferences,
    TaskStatus,
    task_cycle_decisions,
    task_transitions,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings


class PostgreSQLTaskStateMachineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ORQETIA_DATABASE_DSN" not in os.environ:
            raise unittest.SkipTest("PostgreSQL integration DSN is not configured")

    def setUp(self) -> None:
        asyncio.run(self._truncate())

    def test_reads_are_strictly_tenant_client_scoped(self) -> None:
        asyncio.run(self._test_owned_read())

    def test_task_policy_must_match_session_snapshot(self) -> None:
        asyncio.run(self._test_policy_match())

    def test_completion_vs_cancellation_has_one_cas_winner(self) -> None:
        asyncio.run(self._test_completion_cancellation_race())

    def test_cycle_decisions_are_sequential_and_audited(self) -> None:
        asyncio.run(self._test_cycle_decisions())

    def test_explicit_target_cycle_cannot_cross_target(self) -> None:
        asyncio.run(self._test_explicit_target_cycle())

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
                        "TRUNCATE execution.task_cycle_decisions, "
                        "execution.task_transitions, execution.tasks, "
                        "execution.session_target_runtime, "
                        "execution.execution_sessions CASCADE"
                    )
                )
        finally:
            await engine.dispose()

    async def _session_and_stores(self):
        engine, factory = await self._resources()
        session_store = PostgresExecutionSessionStore(factory)
        task_store = PostgresExecutionTaskStore(factory)
        session = self._session()
        await session_store.create(session)
        return engine, task_store, session

    async def _test_owned_read(self) -> None:
        engine, store, session = await self._session_and_stores()
        try:
            task = self._task(session)
            await store.create(task)

            owned = await store.get_owned(
                scope=task.ownership,
                task_id=task.task_id,
            )
            wrong_tenant = await store.get_owned(
                scope=OwnershipScope(uuid7(), task.ownership.client_id),
                task_id=task.task_id,
            )
            wrong_client = await store.get_owned(
                scope=OwnershipScope(task.ownership.tenant_id, uuid7()),
                task_id=task.task_id,
            )

            self.assertIsNotNone(owned)
            self.assertIsNone(wrong_tenant)
            self.assertIsNone(wrong_client)
            assert owned is not None
            self.assertEqual(owned.requested_execution_mode, ExecutionMode.AUTO)
            self.assertIsNone(owned.effective_target)
        finally:
            await engine.dispose()

    async def _test_policy_match(self) -> None:
        engine, store, session = await self._session_and_stores()
        try:
            task = self._task(session, effective_policy_version_id=uuid7())
            with self.assertRaisesRegex(ValueError, "policy version"):
                await store.create(task)
        finally:
            await engine.dispose()

    async def _test_completion_cancellation_race(self) -> None:
        engine, store, session = await self._session_and_stores()
        try:
            task = self._task(session)
            await store.create(task)
            now = datetime.now(UTC)
            self.assertTrue(
                await store.transition(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=1,
                    target_status=TaskStatus.QUEUED,
                    occurred_at=now,
                )
            )
            self.assertTrue(
                await store.transition(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=2,
                    target_status=TaskStatus.RUNNING,
                    occurred_at=datetime.now(UTC),
                )
            )

            complete, cancelling = await asyncio.gather(
                store.transition(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=3,
                    target_status=TaskStatus.COMPLETE,
                    occurred_at=datetime.now(UTC),
                    accepted_requirements=task.requirements,
                    missing_requirements=(),
                    result_reference="payload://result/complete",
                ),
                store.transition(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=3,
                    target_status=TaskStatus.CANCELLING,
                    occurred_at=datetime.now(UTC),
                ),
            )
            self.assertEqual(sum((complete, cancelling)), 1)

            current = await store.get_owned(
                scope=task.ownership,
                task_id=task.task_id,
            )
            assert current is not None
            if current.status is TaskStatus.CANCELLING:
                self.assertTrue(
                    await store.transition(
                        scope=task.ownership,
                        task_id=task.task_id,
                        expected_version=current.version,
                        target_status=TaskStatus.CANCELLED,
                        occurred_at=datetime.now(UTC),
                    )
                )
                current = await store.get_owned(
                    scope=task.ownership,
                    task_id=task.task_id,
                )
                assert current is not None

            self.assertIn(current.status, {TaskStatus.COMPLETE, TaskStatus.CANCELLED})
            self.assertTrue(current.status.terminal)

            with self.assertRaisesRegex(ValueError, "invalid task transition"):
                await store.transition(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=current.version,
                    target_status=TaskStatus.QUEUED,
                    occurred_at=datetime.now(UTC),
                )

            async with engine.connect() as connection:
                history = (
                    await connection.execute(
                        sa.select(task_transitions.c.to_status)
                        .where(task_transitions.c.task_id == task.task_id)
                        .order_by(task_transitions.c.task_version)
                    )
                ).scalars().all()
            self.assertEqual(history[:3], ["CREATED", "QUEUED", "RUNNING"])
            self.assertEqual(history[-1], current.status.value)
        finally:
            await engine.dispose()

    async def _test_cycle_decisions(self) -> None:
        engine, store, session = await self._session_and_stores()
        try:
            task = self._task(session)
            await store.create(task)
            await self._start(store, task)

            decision = TaskCycleDecision(
                cycle_index=1,
                candidate_order=session.policy.authorized_targets,
                accepted_snapshot=(),
                missing_snapshot=task.requirements,
                escalation_reason_code=None,
                recorded_at=datetime.now(UTC),
            )
            self.assertTrue(
                await store.record_cycle_decision(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=3,
                    decision=decision,
                )
            )

            current = await store.get_owned(
                scope=task.ownership,
                task_id=task.task_id,
            )
            assert current is not None
            self.assertEqual(current.current_cycle, 1)
            self.assertEqual(current.version, 4)

            with self.assertRaisesRegex(ValueError, "advance exactly by one"):
                await store.record_cycle_decision(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=4,
                    decision=TaskCycleDecision(
                        cycle_index=3,
                        candidate_order=session.policy.authorized_targets,
                        accepted_snapshot=(),
                        missing_snapshot=task.requirements,
                        recorded_at=datetime.now(UTC),
                    ),
                )

            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        sa.select(
                            task_cycle_decisions.c.cycle_index,
                            task_cycle_decisions.c.task_version,
                        ).where(task_cycle_decisions.c.task_id == task.task_id)
                    )
                ).all()
            self.assertEqual(rows, [(1, 4)])
        finally:
            await engine.dispose()

    async def _test_explicit_target_cycle(self) -> None:
        engine, store, session = await self._session_and_stores()
        try:
            target = session.policy.authorized_targets[0]
            task = self._task(
                session,
                requested_execution_mode=ExecutionMode.EXPLICIT_TARGET,
                requested_target=RequestedTargetSnapshot(
                    provider_id=target.provider_id,
                    model_id=target.model_id,
                    reasoning_profile=target.reasoning_profile,
                ),
                effective_target=target,
            )
            await store.create(task)
            await self._start(store, task)

            with self.assertRaisesRegex(ValueError, "cross-target"):
                await store.record_cycle_decision(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=3,
                    decision=TaskCycleDecision(
                        cycle_index=1,
                        candidate_order=(
                            ExecutionTargetSnapshot("OTHER", "model", "standard"),
                        ),
                        accepted_snapshot=(),
                        missing_snapshot=task.requirements,
                        recorded_at=datetime.now(UTC),
                    ),
                )
        finally:
            await engine.dispose()

    @staticmethod
    async def _start(
        store: PostgresExecutionTaskStore,
        task: ExecutionTask,
    ) -> None:
        self_scope = task.ownership
        if not await store.transition(
            scope=self_scope,
            task_id=task.task_id,
            expected_version=1,
            target_status=TaskStatus.QUEUED,
            occurred_at=datetime.now(UTC),
        ):
            raise AssertionError("CREATED -> QUEUED CAS unexpectedly lost")
        if not await store.transition(
            scope=self_scope,
            task_id=task.task_id,
            expected_version=2,
            target_status=TaskStatus.RUNNING,
            occurred_at=datetime.now(UTC),
        ):
            raise AssertionError("QUEUED -> RUNNING CAS unexpectedly lost")

    @staticmethod
    def _session() -> ExecutionSession:
        now = datetime.now(UTC)
        target = ExecutionTargetSnapshot("OPENAI", "gpt-test", "standard")
        return ExecutionSession(
            session_id=uuid7(),
            ownership=OwnershipScope(uuid7(), uuid7()),
            status=SessionStatus.ACTIVE,
            policy=SessionPolicySnapshot(
                effective_policy_version_id=uuid7(),
                authorized_targets=(target,),
            ),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _task(session: ExecutionSession, **overrides: object) -> ExecutionTask:
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "task_id": uuid7(),
            "session_id": session.session_id,
            "ownership": session.ownership,
            "operation": "TASK_EXECUTION",
            "status": TaskStatus.CREATED,
            "effective_policy_version_id": session.policy.effective_policy_version_id,
            "requested_execution_mode": ExecutionMode.AUTO,
            "requirements": ("VALID_JSON",),
            "accepted_requirements": (),
            "missing_requirements": ("VALID_JSON",),
            "payloads": TaskPayloadReferences(
                input_reference="payload://input/integration",
                input_fingerprint="b" * 64,
            ),
            "created_at": now,
            "updated_at": now,
        }
        values.update(overrides)
        return ExecutionTask(**values)


if __name__ == "__main__":
    unittest.main()
