from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import sqlalchemy as sa

from orqetia.execution import (
    PROVIDER_DISPATCH_OPERATION,
    PROVIDER_DISPATCH_VERSION,
    ExecutionMode,
    ExecutionSession,
    ExecutionTargetSnapshot,
    ExecutionTask,
    OwnershipScope,
    PostgresExecutionSessionStore,
    PostgresExecutionTaskStore,
    PostgresProviderAttemptStore,
    ProviderAttempt,
    ProviderAttemptStatus,
    RequestedTargetSnapshot,
    SessionPolicySnapshot,
    SessionStatus,
    TaskPayloadReferences,
    TaskStatus,
    provider_attempts,
)
from orqetia.infrastructure.messaging import PostgresWorkQueue, work_items
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.infrastructure.processes import (
    HandlerRegistry,
    ProviderAttemptWorkHandler,
    WorkerProcess,
)
from orqetia.providers import (
    DeterministicTestProvider,
    ProviderAttemptRequest,
    ProviderTarget,
    SimulatorFixture,
    SimulatorScenario,
    SimulatorStep,
)
from orqetia.settings import RuntimeSettings
from orqetia.shared.messaging import (
    DataClassification,
    QueueName,
    WorkItem,
)


class CountingAdapter:
    def __init__(self, delegate: DeterministicTestProvider) -> None:
        self.delegate = delegate
        self.calls = 0

    async def invoke(self, request: ProviderAttemptRequest):
        self.calls += 1
        return await self.delegate.invoke(request)


class ExplodingAdapter:
    async def invoke(self, request: ProviderAttemptRequest):
        del request
        raise RuntimeError("synthetic transport ambiguity")


class PostgreSQLProviderAttemptRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ORQETIA_DATABASE_DSN" not in os.environ:
            raise unittest.SkipTest("PostgreSQL integration DSN is not configured")

    def setUp(self) -> None:
        asyncio.run(self._truncate())

    def test_schedule_is_atomic_with_work_and_task_reference(self) -> None:
        asyncio.run(self._test_atomic_schedule())

    def test_completed_attempt_is_not_dispatched_again_on_duplicate_work(self) -> None:
        asyncio.run(self._test_duplicate_delivery())

    def test_dispatching_redelivery_becomes_ambiguous_without_provider_replay(self) -> None:
        asyncio.run(self._test_dispatching_redelivery())

    def test_adapter_exception_after_dispatch_start_becomes_ambiguous(self) -> None:
        asyncio.run(self._test_adapter_exception())

    def test_explicit_target_mismatch_is_rejected_before_queueing(self) -> None:
        asyncio.run(self._test_explicit_target_mismatch())

    def test_auto_target_outside_session_snapshot_is_rejected(self) -> None:
        asyncio.run(self._test_auto_unauthorized_target())

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
                        "TRUNCATE execution.provider_attempts, "
                        "execution.task_cycle_decisions, execution.task_transitions, "
                        "execution.tasks, execution.session_target_runtime, "
                        "execution.execution_sessions, messaging.work_items CASCADE"
                    )
                )
        finally:
            await engine.dispose()

    async def _test_atomic_schedule(self) -> None:
        engine, factory, session, task = await self._running_task()
        store = PostgresProviderAttemptStore(factory)
        queue = PostgresWorkQueue(factory)
        try:
            attempt = self._attempt(session, task)
            blocker = WorkItem(
                work_id=attempt.work_id,
                queue_name=QueueName.EXECUTION,
                operation_type="synthetic.blocker",
                operation_version=1,
                data_classification=DataClassification.INTERNAL,
                payload={},
                available_at=datetime.now(UTC),
            )
            await queue.enqueue(blocker)

            with self.assertRaises(sa.exc.IntegrityError):
                await store.schedule(attempt, available_at=datetime.now(UTC))

            self.assertIsNone(await store.get(attempt.attempt_id))
            current = await PostgresExecutionTaskStore(factory).get_owned(
                scope=task.ownership,
                task_id=task.task_id,
            )
            assert current is not None
            self.assertNotIn(attempt.attempt_id, current.attempt_reference_ids)

            async with engine.connect() as connection:
                count = (
                    await connection.execute(
                        sa.select(sa.func.count())
                        .select_from(provider_attempts)
                        .where(provider_attempts.c.attempt_id == attempt.attempt_id)
                    )
                ).scalar_one()
            self.assertEqual(count, 0)
        finally:
            await engine.dispose()

    async def _test_duplicate_delivery(self) -> None:
        engine, factory, session, task = await self._running_task()
        store = PostgresProviderAttemptStore(factory)
        queue = PostgresWorkQueue(factory)
        try:
            attempt = self._attempt(session, task)
            await store.schedule(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )

            simulator = DeterministicTestProvider(
                SimulatorFixture(
                    fixture_id="success-once",
                    seed="fixed",
                    candidates=(),
                    steps=(
                        SimulatorStep(
                            scenario=SimulatorScenario.SUCCESS,
                            expected_cycle=1,
                            expected_attempt_index=1,
                            expected_target=attempt.target,
                        ),
                    ),
                )
            )
            adapter = CountingAdapter(simulator)
            process = self._process(
                queue,
                ProviderAttemptWorkHandler(
                    store=store,
                    resolve_adapter=lambda target: (
                        adapter if target == attempt.target else self._missing_adapter(target)
                    ),
                ),
            )

            self.assertEqual(await process.run_once(), 1)
            persisted = await store.get(attempt.attempt_id)
            assert persisted is not None
            self.assertEqual(persisted.status, ProviderAttemptStatus.COMPLETED)
            self.assertEqual(adapter.calls, 1)

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == attempt.work_id)
                    .values(
                        state="READY",
                        available_at=datetime.now(UTC) - timedelta(seconds=1),
                        lease_owner=None,
                        lease_until=None,
                        completed_at=None,
                    )
                )

            self.assertEqual(await process.run_once(), 1)
            self.assertEqual(adapter.calls, 1)
            persisted = await store.get(attempt.attempt_id)
            assert persisted is not None
            self.assertEqual(persisted.status, ProviderAttemptStatus.COMPLETED)
        finally:
            await engine.dispose()

    async def _test_dispatching_redelivery(self) -> None:
        engine, factory, session, task = await self._running_task()
        store = PostgresProviderAttemptStore(factory)
        queue = PostgresWorkQueue(factory)
        try:
            attempt = self._attempt(session, task)
            await store.schedule(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
                max_infrastructure_attempts=3,
            )
            first = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="crashed-worker",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]
            self.assertTrue(
                await store.begin_dispatch(
                    attempt.attempt_id,
                    occurred_at=datetime.now(UTC),
                    expected_version=1,
                )
            )

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == first.work_id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            simulator = DeterministicTestProvider(
                SimulatorFixture(
                    fixture_id="must-not-run",
                    seed="fixed",
                    candidates=(),
                    steps=(
                        SimulatorStep(
                            scenario=SimulatorScenario.SUCCESS,
                            expected_cycle=1,
                            expected_attempt_index=1,
                        ),
                    ),
                )
            )
            adapter = CountingAdapter(simulator)
            process = self._process(
                queue,
                ProviderAttemptWorkHandler(
                    store=store,
                    resolve_adapter=lambda _target: adapter,
                ),
                process_id="recovery-worker",
            )
            self.assertEqual(await process.run_once(), 1)

            persisted = await store.get(attempt.attempt_id)
            assert persisted is not None
            self.assertEqual(persisted.status, ProviderAttemptStatus.AMBIGUOUS)
            self.assertEqual(
                persisted.ambiguity_error_class,
                "LEASE_REDELIVERY_AFTER_DISPATCH_START",
            )
            self.assertEqual(adapter.calls, 0)

            async with engine.connect() as connection:
                work_state = (
                    await connection.execute(
                        sa.select(work_items.c.state).where(
                            work_items.c.work_id == attempt.work_id
                        )
                    )
                ).scalar_one()
            self.assertEqual(work_state, "DONE")
        finally:
            await engine.dispose()

    async def _test_adapter_exception(self) -> None:
        engine, factory, session, task = await self._running_task()
        store = PostgresProviderAttemptStore(factory)
        queue = PostgresWorkQueue(factory)
        try:
            attempt = self._attempt(session, task)
            await store.schedule(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            process = self._process(
                queue,
                ProviderAttemptWorkHandler(
                    store=store,
                    resolve_adapter=lambda _target: ExplodingAdapter(),
                ),
            )
            self.assertEqual(await process.run_once(), 1)

            persisted = await store.get(attempt.attempt_id)
            assert persisted is not None
            self.assertEqual(persisted.status, ProviderAttemptStatus.AMBIGUOUS)
            self.assertEqual(persisted.ambiguity_error_class, "RuntimeError")
        finally:
            await engine.dispose()

    async def _test_explicit_target_mismatch(self) -> None:
        engine, factory, session, task = await self._running_task(explicit=True)
        store = PostgresProviderAttemptStore(factory)
        try:
            attempt = self._attempt(
                session,
                task,
                target=ProviderTarget("OTHER", "other-model", "standard"),
            )
            with self.assertRaisesRegex(PermissionError, "frozen explicit target"):
                await store.schedule(attempt, available_at=datetime.now(UTC))
            self.assertIsNone(await store.get(attempt.attempt_id))
        finally:
            await engine.dispose()

    async def _test_auto_unauthorized_target(self) -> None:
        engine, factory, session, task = await self._running_task()
        store = PostgresProviderAttemptStore(factory)
        try:
            attempt = self._attempt(
                session,
                task,
                target=ProviderTarget("OTHER", "other-model", "standard"),
            )
            with self.assertRaisesRegex(PermissionError, "session policy snapshot"):
                await store.schedule(attempt, available_at=datetime.now(UTC))
            self.assertIsNone(await store.get(attempt.attempt_id))
        finally:
            await engine.dispose()

    async def _running_task(self, *, explicit: bool = False):
        engine, factory = await self._resources()
        target = ExecutionTargetSnapshot(
            "ORQETIA_TEST_PROVIDER",
            "sim-small",
            "standard",
        )
        now = datetime.now(UTC)
        session = ExecutionSession(
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
        await PostgresExecutionSessionStore(factory).create(session)

        task = ExecutionTask(
            task_id=uuid7(),
            session_id=session.session_id,
            ownership=session.ownership,
            operation="TASK_EXECUTION",
            status=TaskStatus.CREATED,
            effective_policy_version_id=session.policy.effective_policy_version_id,
            requested_execution_mode=(
                ExecutionMode.EXPLICIT_TARGET if explicit else ExecutionMode.AUTO
            ),
            requested_target=(
                RequestedTargetSnapshot(
                    provider_id=target.provider_id,
                    model_id=target.model_id,
                    reasoning_profile=target.reasoning_profile,
                )
                if explicit
                else None
            ),
            effective_target=target if explicit else None,
            requirements=("VALID_JSON",),
            accepted_requirements=(),
            missing_requirements=("VALID_JSON",),
            payloads=TaskPayloadReferences(
                input_reference="payload://input/worker-recovery",
                input_fingerprint="b" * 64,
            ),
            created_at=now,
            updated_at=now,
        )
        task_store = PostgresExecutionTaskStore(factory)
        await task_store.create(task)
        self.assertTrue(
            await task_store.transition(
                scope=task.ownership,
                task_id=task.task_id,
                expected_version=1,
                target_status=TaskStatus.QUEUED,
                occurred_at=datetime.now(UTC),
            )
        )
        self.assertTrue(
            await task_store.transition(
                scope=task.ownership,
                task_id=task.task_id,
                expected_version=2,
                target_status=TaskStatus.RUNNING,
                occurred_at=datetime.now(UTC),
            )
        )
        return engine, factory, session, task

    @staticmethod
    def _attempt(
        session: ExecutionSession,
        task: ExecutionTask,
        *,
        target: ProviderTarget | None = None,
    ) -> ProviderAttempt:
        now = datetime.now(UTC)
        resolved = target or ProviderTarget(
            "ORQETIA_TEST_PROVIDER",
            "sim-small",
            "standard",
        )
        return ProviderAttempt(
            attempt_id=uuid7(),
            work_id=uuid7(),
            ownership=session.ownership,
            session_id=session.session_id,
            task_id=task.task_id,
            operation=task.operation,
            target=resolved,
            cycle=1,
            attempt_index=1,
            request_reference="payload://request/worker-recovery",
            request_fingerprint="c" * 64,
            missing_requirements=task.requirements,
            status=ProviderAttemptStatus.READY,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _process(
        queue: PostgresWorkQueue,
        handler: ProviderAttemptWorkHandler,
        *,
        process_id: str = "provider-worker",
    ) -> WorkerProcess:
        registry = HandlerRegistry()
        registry.register(
            PROVIDER_DISPATCH_OPERATION,
            PROVIDER_DISPATCH_VERSION,
            handler,
        )
        return WorkerProcess(
            queue=queue,
            registry=registry,
            queue_name=QueueName.EXECUTION,
            concurrency=2,
            lease_seconds=30,
            poll_interval_seconds=0.01,
            shutdown_grace_seconds=1,
            process_id=process_id,
        )

    @staticmethod
    def _missing_adapter(target: ProviderTarget):
        raise LookupError(f"no adapter for {target.provider_id}")


if __name__ == "__main__":
    unittest.main()
