from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
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
    PostgresProviderAttemptStore,
    ProviderAttempt,
    ProviderAttemptStatus,
    SessionPolicySnapshot,
    SessionStatus,
    TaskPayloadReferences,
    TaskStatus,
)
from orqetia.infrastructure.messaging import PostgresWorkQueue, work_items
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.infrastructure.processes import (
    PROVIDER_ATTEMPT_OPERATION,
    PROVIDER_ATTEMPT_OPERATION_VERSION,
    HandlerDisposition,
    HandlerRegistry,
    ProviderAttemptHandler,
    WorkerProcess,
    build_provider_attempt_work_item,
)
from orqetia.providers import (
    DeterministicTestProvider,
    ProviderOutcome,
    ProviderTarget,
    SimulatorFixture,
    SimulatorScenario,
    SimulatorStep,
)
from orqetia.settings import RuntimeSettings
from orqetia.shared.messaging import QueueName


class ProviderAttemptRecoveryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ORQETIA_DATABASE_DSN" not in os.environ:
            raise unittest.SkipTest("PostgreSQL integration DSN is not configured")

    def setUp(self) -> None:
        asyncio.run(self._truncate())

    def test_worker_dispatches_once_and_completes_work(self) -> None:
        asyncio.run(self._test_worker_dispatch())

    def test_reclaim_after_result_before_ack_does_not_redispatch(self) -> None:
        asyncio.run(self._test_result_before_ack_reclaim())

    def test_orphan_dispatch_becomes_ambiguous_without_redispatch(self) -> None:
        asyncio.run(self._test_orphan_dispatch())

    def test_duplicate_work_items_share_one_provider_dispatch(self) -> None:
        asyncio.run(self._test_duplicate_work())

    def test_provider_transient_result_is_not_infrastructure_retry(self) -> None:
        asyncio.run(self._test_provider_transient())

    def test_cancellation_before_dispatch_skips_provider(self) -> None:
        asyncio.run(self._test_cancel_before_dispatch())

    def test_adapter_resolution_failure_requeues_without_dispatch(self) -> None:
        asyncio.run(self._test_resolution_failure())

    def test_delayed_work_does_not_execute_early(self) -> None:
        asyncio.run(self._test_delayed_work())

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

    async def _prepared(self, *, scenario: SimulatorScenario = SimulatorScenario.SUCCESS):
        engine, factory = await self._resources()
        session_store = PostgresExecutionSessionStore(factory)
        task_store = PostgresExecutionTaskStore(factory)
        attempt_store = PostgresProviderAttemptStore(factory)
        queue = PostgresWorkQueue(factory)

        session = self._session()
        await session_store.create(session)
        task = self._task(session)
        await task_store.create(task)
        await self._start_task(task_store, task)

        attempt = self._attempt(session, task)
        await attempt_store.create(attempt)
        provider = self._provider(attempt, scenario=scenario)
        handler = ProviderAttemptHandler(
            store=attempt_store,
            resolve_adapter=lambda _target: provider,
        )
        return engine, factory, queue, task_store, attempt_store, task, attempt, provider, handler

    async def _test_worker_dispatch(self) -> None:
        (
            engine,
            _factory,
            queue,
            _task_store,
            attempt_store,
            _task,
            attempt,
            provider,
            handler,
        ) = await self._prepared()
        try:
            item = build_provider_attempt_work_item(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await queue.enqueue(item)

            registry = HandlerRegistry()
            registry.register(
                PROVIDER_ATTEMPT_OPERATION,
                PROVIDER_ATTEMPT_OPERATION_VERSION,
                handler,
            )
            worker = WorkerProcess(
                queue=queue,
                registry=registry,
                queue_name=QueueName.EXECUTION,
                concurrency=1,
                lease_seconds=30,
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=2,
                process_id="worker-a",
            )

            self.assertEqual(await worker.run_once(), 1)
            self.assertEqual(len(provider.invocations), 1)

            persisted = await attempt_store.get_owned(
                scope=attempt.ownership,
                attempt_id=attempt.attempt_id,
            )
            assert persisted is not None
            self.assertIs(persisted.status, ProviderAttemptStatus.COMPLETED)
            self.assertEqual(persisted.provider_outcome, ProviderOutcome.SUCCESS.value)

            async with engine.connect() as connection:
                state = (
                    await connection.execute(
                        sa.select(work_items.c.state).where(
                            work_items.c.work_id == item.work_id
                        )
                    )
                ).scalar_one()
            self.assertEqual(state, "DONE")
        finally:
            await engine.dispose()

    async def _test_result_before_ack_reclaim(self) -> None:
        (
            engine,
            _factory,
            queue,
            _task_store,
            attempt_store,
            _task,
            attempt,
            provider,
            handler,
        ) = await self._prepared()
        try:
            item = build_provider_attempt_work_item(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await queue.enqueue(item)
            first = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]

            outcome = await handler(first)
            self.assertIs(outcome.disposition, HandlerDisposition.COMPLETE)
            self.assertEqual(len(provider.invocations), 1)

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == item.work_id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            second = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-b",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]
            second_outcome = await handler(second)
            self.assertIs(second_outcome.disposition, HandlerDisposition.COMPLETE)
            self.assertEqual(len(provider.invocations), 1)
            self.assertTrue(await queue.complete(second))

            persisted = await attempt_store.get_owned(
                scope=attempt.ownership,
                attempt_id=attempt.attempt_id,
            )
            assert persisted is not None
            self.assertIs(persisted.status, ProviderAttemptStatus.COMPLETED)
        finally:
            await engine.dispose()

    async def _test_orphan_dispatch(self) -> None:
        (
            engine,
            _factory,
            queue,
            _task_store,
            attempt_store,
            _task,
            attempt,
            provider,
            handler,
        ) = await self._prepared()
        try:
            item = build_provider_attempt_work_item(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await queue.enqueue(item)
            first = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]
            claim = await attempt_store.claim_dispatch(
                scope=attempt.ownership,
                attempt_id=attempt.attempt_id,
                work_id=first.work_id,
                occurred_at=datetime.now(UTC),
            )
            self.assertEqual(claim.action.value, "DISPATCH")

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == item.work_id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            second = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-b",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]
            outcome = await handler(second)
            self.assertIs(outcome.disposition, HandlerDisposition.COMPLETE)
            self.assertEqual(len(provider.invocations), 0)
            self.assertTrue(await queue.complete(second))

            persisted = await attempt_store.get_owned(
                scope=attempt.ownership,
                attempt_id=attempt.attempt_id,
            )
            assert persisted is not None
            self.assertIs(persisted.status, ProviderAttemptStatus.AMBIGUOUS)
            self.assertEqual(
                persisted.error_class,
                "DISPATCH_RESULT_UNKNOWN_AFTER_RECLAIM",
            )
        finally:
            await engine.dispose()

    async def _test_duplicate_work(self) -> None:
        (
            engine,
            _factory,
            queue,
            _task_store,
            attempt_store,
            _task,
            attempt,
            provider,
            handler,
        ) = await self._prepared()
        try:
            now = datetime.now(UTC) - timedelta(seconds=1)
            first_item = build_provider_attempt_work_item(
                attempt,
                work_id=uuid7(),
                available_at=now,
            )
            second_item = build_provider_attempt_work_item(
                attempt,
                work_id=uuid7(),
                available_at=now,
            )
            await queue.enqueue(first_item)
            await queue.enqueue(second_item)
            leases = await queue.claim(
                queue_name=QueueName.EXECUTION,
                lease_owner="worker-a",
                lease_seconds=30,
                limit=2,
            )
            self.assertEqual(len(leases), 2)

            outcomes = await asyncio.gather(*(handler(lease) for lease in leases))
            self.assertTrue(
                all(item.disposition is HandlerDisposition.COMPLETE for item in outcomes)
            )
            self.assertEqual(len(provider.invocations), 1)
            for lease in leases:
                self.assertTrue(await queue.complete(lease))

            persisted = await attempt_store.get_owned(
                scope=attempt.ownership,
                attempt_id=attempt.attempt_id,
            )
            assert persisted is not None
            self.assertIs(persisted.status, ProviderAttemptStatus.COMPLETED)
        finally:
            await engine.dispose()

    async def _test_provider_transient(self) -> None:
        (
            engine,
            _factory,
            queue,
            _task_store,
            attempt_store,
            _task,
            attempt,
            provider,
            handler,
        ) = await self._prepared(scenario=SimulatorScenario.TRANSIENT_ERROR)
        try:
            item = build_provider_attempt_work_item(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await queue.enqueue(item)

            lease = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]
            outcome = await handler(lease)
            self.assertIs(outcome.disposition, HandlerDisposition.COMPLETE)
            self.assertTrue(await queue.complete(lease))
            self.assertEqual(len(provider.invocations), 1)

            persisted = await attempt_store.get_owned(
                scope=attempt.ownership,
                attempt_id=attempt.attempt_id,
            )
            assert persisted is not None
            self.assertIs(persisted.status, ProviderAttemptStatus.COMPLETED)
            self.assertEqual(
                persisted.provider_outcome,
                ProviderOutcome.TRANSIENT_ERROR.value,
            )

            no_more_work = await queue.claim(
                queue_name=QueueName.EXECUTION,
                lease_owner="worker-b",
                lease_seconds=30,
                limit=1,
            )
            self.assertEqual(tuple(no_more_work), ())
        finally:
            await engine.dispose()

    async def _test_cancel_before_dispatch(self) -> None:
        (
            engine,
            _factory,
            queue,
            task_store,
            attempt_store,
            task,
            attempt,
            provider,
            handler,
        ) = await self._prepared()
        try:
            self.assertTrue(
                await task_store.transition(
                    scope=task.ownership,
                    task_id=task.task_id,
                    expected_version=3,
                    target_status=TaskStatus.CANCELLING,
                    occurred_at=datetime.now(UTC),
                )
            )
            item = build_provider_attempt_work_item(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await queue.enqueue(item)
            lease = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]

            outcome = await handler(lease)
            self.assertIs(outcome.disposition, HandlerDisposition.COMPLETE)
            self.assertEqual(len(provider.invocations), 0)
            self.assertTrue(await queue.complete(lease))

            persisted = await attempt_store.get_owned(
                scope=attempt.ownership,
                attempt_id=attempt.attempt_id,
            )
            assert persisted is not None
            self.assertIs(persisted.status, ProviderAttemptStatus.CANCELLED)
            self.assertEqual(persisted.error_class, "TASK_NOT_RUNNABLE")
        finally:
            await engine.dispose()

    async def _test_resolution_failure(self) -> None:
        (
            engine,
            _factory,
            queue,
            _task_store,
            attempt_store,
            _task,
            attempt,
            _provider,
            _handler,
        ) = await self._prepared()
        try:
            handler = ProviderAttemptHandler(
                store=attempt_store,
                resolve_adapter=lambda _target: (_ for _ in ()).throw(
                    LookupError("adapter unavailable")
                ),
            )
            item = build_provider_attempt_work_item(
                attempt,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await queue.enqueue(item)
            lease = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]

            outcome = await handler(lease)
            self.assertIs(
                outcome.disposition,
                HandlerDisposition.REQUEUE_INFRASTRUCTURE,
            )
            assert outcome.available_at is not None
            assert outcome.error_class is not None
            self.assertTrue(
                await queue.requeue_infrastructure_failure(
                    lease,
                    available_at=outcome.available_at,
                    error_class=outcome.error_class,
                )
            )

            persisted = await attempt_store.get_owned(
                scope=attempt.ownership,
                attempt_id=attempt.attempt_id,
            )
            assert persisted is not None
            self.assertIs(persisted.status, ProviderAttemptStatus.PREPARED)
        finally:
            await engine.dispose()

    async def _test_delayed_work(self) -> None:
        (
            engine,
            _factory,
            queue,
            _task_store,
            _attempt_store,
            _task,
            attempt,
            provider,
            handler,
        ) = await self._prepared()
        try:
            item = build_provider_attempt_work_item(
                attempt,
                available_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            await queue.enqueue(item)
            registry = HandlerRegistry()
            registry.register(
                PROVIDER_ATTEMPT_OPERATION,
                PROVIDER_ATTEMPT_OPERATION_VERSION,
                handler,
            )
            worker = WorkerProcess(
                queue=queue,
                registry=registry,
                queue_name=QueueName.EXECUTION,
                concurrency=1,
                lease_seconds=30,
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=2,
                process_id="worker-delay",
            )
            self.assertEqual(await worker.run_once(), 0)
            self.assertEqual(len(provider.invocations), 0)

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == item.work_id)
                    .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
                )

            self.assertEqual(await worker.run_once(), 1)
            self.assertEqual(len(provider.invocations), 1)
        finally:
            await engine.dispose()

    @staticmethod
    async def _start_task(
        task_store: PostgresExecutionTaskStore,
        task: ExecutionTask,
    ) -> None:
        if not await task_store.transition(
            scope=task.ownership,
            task_id=task.task_id,
            expected_version=1,
            target_status=TaskStatus.QUEUED,
            occurred_at=datetime.now(UTC),
        ):
            raise AssertionError("CREATED -> QUEUED unexpectedly lost")
        if not await task_store.transition(
            scope=task.ownership,
            task_id=task.task_id,
            expected_version=2,
            target_status=TaskStatus.RUNNING,
            occurred_at=datetime.now(UTC),
        ):
            raise AssertionError("QUEUED -> RUNNING unexpectedly lost")

    @staticmethod
    def _session() -> ExecutionSession:
        now = datetime.now(UTC)
        target = ExecutionTargetSnapshot(
            "ORQETIA_TEST_PROVIDER",
            "sim-small",
            "standard",
        )
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
    def _task(session: ExecutionSession) -> ExecutionTask:
        now = datetime.now(UTC)
        return ExecutionTask(
            task_id=uuid7(),
            session_id=session.session_id,
            ownership=session.ownership,
            operation="TASK_EXECUTION",
            status=TaskStatus.CREATED,
            effective_policy_version_id=session.policy.effective_policy_version_id,
            requested_execution_mode=ExecutionMode.AUTO,
            requirements=("VALID_JSON",),
            accepted_requirements=(),
            missing_requirements=("VALID_JSON",),
            payloads=TaskPayloadReferences(
                input_reference="payload://input/recovery",
                input_fingerprint="b" * 64,
            ),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _attempt(
        session: ExecutionSession,
        task: ExecutionTask,
    ) -> ProviderAttempt:
        now = datetime.now(UTC)
        return ProviderAttempt(
            attempt_id=uuid7(),
            task_id=task.task_id,
            session_id=session.session_id,
            ownership=session.ownership,
            operation=task.operation,
            target=session.policy.authorized_targets[0],
            cycle=1,
            attempt_index=1,
            request_reference="payload://provider/request",
            request_fingerprint="c" * 64,
            status=ProviderAttemptStatus.PREPARED,
            missing_requirements=task.requirements,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _provider(
        attempt: ProviderAttempt,
        *,
        scenario: SimulatorScenario,
    ) -> DeterministicTestProvider:
        target = ProviderTarget(
            provider_id=attempt.target.provider_id,
            model_id=attempt.target.model_id,
            reasoning_profile=attempt.target.reasoning_profile,
        )
        return DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="worker-recovery",
                seed="deterministic",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=scenario,
                        expected_cycle=attempt.cycle,
                        expected_attempt_index=attempt.attempt_index,
                        expected_target=target,
                    ),
                ),
            )
        )


if __name__ == "__main__":
    unittest.main()
