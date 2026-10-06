from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid7

import sqlalchemy as sa

from orqetia.control_plane import (
    AuthorizedExecutionTarget,
    ClientPolicyAssignment,
    ExecutionPolicyVersion,
    PostgresExecutionPolicyRepository,
    ProviderCredentialSelection,
)
from orqetia.execution import (
    ExecutionMode,
    ExecutionSession,
    ExecutionTargetSnapshot,
    ExecutionTask,
    OrchestrationCandidate,
    OwnershipScope,
    PostgresExecutionSessionStore,
    PostgresExecutionTaskStore,
    PostgresProviderAttemptStore,
    SessionStatus,
    TaskPayloadReferences,
    TaskStatus,
)
from orqetia.infrastructure.messaging import PostgresWorkQueue
from orqetia.infrastructure.persistence import (
    create_engine,
    create_session_factory,
)
from orqetia.infrastructure.processes import (
    PROVIDER_ATTEMPT_OPERATION,
    PROVIDER_ATTEMPT_OPERATION_VERSION,
    TASK_ORCHESTRATION_OPERATION,
    TASK_ORCHESTRATION_OPERATION_VERSION,
    HandlerRegistry,
    ProviderAttemptHandler,
    TaskOrchestrationHandler,
    WorkerProcess,
    build_task_orchestration_work_item,
)
from orqetia.providers import (
    DeterministicTestProvider,
    ProviderTarget,
    SimulatorFixture,
    SimulatorScenario,
    SimulatorStep,
)
from orqetia.settings import RuntimeSettings
from orqetia.shared.messaging import QueueName


class _Candidates:
    def __init__(
        self,
        candidates: tuple[OrchestrationCandidate, ...],
    ) -> None:
        self._candidates = candidates

    async def resolve(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        occurred_at: datetime,
    ) -> tuple[OrchestrationCandidate, ...]:
        del task, session, occurred_at
        return self._candidates


class _Credentials:
    def __init__(self) -> None:
        self.account_id = uuid7()
        self.credential_id = uuid7()

    async def select_credential(
        self,
        *,
        provider_id: str,
        occurred_at: datetime,
        region: str | None = None,
    ) -> ProviderCredentialSelection:
        del region
        return ProviderCredentialSelection(
            provider_account_id=self.account_id,
            provider_credential_id=self.credential_id,
            provider_id=provider_id,
            key_version=1,
            selected_at=occurred_at,
        )


class TaskOrchestrationWorkerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ORQETIA_DATABASE_DSN" not in os.environ:
            raise unittest.SkipTest(
                "PostgreSQL integration DSN is not configured"
            )

    def setUp(self) -> None:
        asyncio.run(self._truncate())

    def test_auto_escalates_using_frozen_policy_and_completes(self) -> None:
        asyncio.run(self._test_auto_escalation())

    async def _resources(self):
        settings = RuntimeSettings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        return engine, factory

    async def _truncate(self) -> None:
        engine, _factory = await self._resources()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text(
                        "TRUNCATE messaging.work_items, "
                        "execution.provider_attempts, "
                        "execution.task_cycle_decisions, "
                        "execution.task_transitions, execution.tasks, "
                        "execution.session_target_runtime, "
                        "execution.execution_sessions, "
                        "control.client_policy_assignments, "
                        "control.execution_policy_versions CASCADE"
                    )
                )
        finally:
            await engine.dispose()

    async def _test_auto_escalation(self) -> None:
        engine, factory = await self._resources()
        sessions = PostgresExecutionSessionStore(factory)
        tasks = PostgresExecutionTaskStore(factory)
        attempts = PostgresProviderAttemptStore(factory)
        policies = PostgresExecutionPolicyRepository(factory)
        queue = PostgresWorkQueue(factory)
        credentials = _Credentials()

        now = datetime.now(UTC)
        scope = OwnershipScope(uuid7(), uuid7())
        cheap = ExecutionTargetSnapshot(
            "ORQETIA_TEST_PROVIDER",
            "sim-cheap",
            "standard",
        )
        expensive = ExecutionTargetSnapshot(
            "ORQETIA_TEST_PROVIDER",
            "sim-expensive",
            "standard",
        )
        frozen = ExecutionPolicyVersion(
            policy_version_id=uuid7(),
            tenant_id=scope.tenant_id,
            client_id=scope.client_id,
            version_number=1,
            max_cycles=1,
            max_attempts=2,
            cycle_delay_seconds=0,
            retry_after_cap_seconds=3,
            authorized_targets=tuple(
                AuthorizedExecutionTarget(
                    item.provider_id,
                    item.model_id,
                    item.reasoning_profile,
                )
                for item in (cheap, expensive)
            ),
            created_at=now,
        )
        await policies.publish_and_activate(
            version=frozen,
            assignment=ClientPolicyAssignment(
                tenant_id=scope.tenant_id,
                client_id=scope.client_id,
                policy_version_id=frozen.policy_version_id,
                assignment_version=1,
                assigned_at=now,
            ),
            expected_assignment_version=None,
        )
        current = ExecutionPolicyVersion(
            policy_version_id=uuid7(),
            tenant_id=scope.tenant_id,
            client_id=scope.client_id,
            version_number=2,
            max_cycles=1,
            max_attempts=1,
            cycle_delay_seconds=0,
            retry_after_cap_seconds=0,
            authorized_targets=frozen.authorized_targets,
            created_at=datetime.now(UTC),
        )
        await policies.publish_and_activate(
            version=current,
            assignment=ClientPolicyAssignment(
                tenant_id=scope.tenant_id,
                client_id=scope.client_id,
                policy_version_id=current.policy_version_id,
                assignment_version=2,
                assigned_at=datetime.now(UTC),
            ),
            expected_assignment_version=1,
        )

        session = ExecutionSession(
            session_id=uuid7(),
            ownership=scope,
            status=SessionStatus.ACTIVE,
            policy=frozen.session_policy_snapshot(),
            created_at=now,
            updated_at=now,
        )
        await sessions.create(session)
        task = ExecutionTask(
            task_id=uuid7(),
            session_id=session.session_id,
            ownership=scope,
            operation="TASK_EXECUTION",
            status=TaskStatus.CREATED,
            effective_policy_version_id=frozen.policy_version_id,
            requested_execution_mode=ExecutionMode.AUTO,
            requirements=("VALID_RESPONSE",),
            accepted_requirements=(),
            missing_requirements=("VALID_RESPONSE",),
            payloads=TaskPayloadReferences(
                input_reference="payload://orchestration/input",
                input_fingerprint="d" * 64,
            ),
            created_at=now,
            updated_at=now,
        )
        await tasks.create(task)

        candidates = _Candidates(
            (
                OrchestrationCandidate(
                    target=cheap,
                    comparison_group="CURRENCY:USD",
                    comparison_group_rank=0,
                    estimated_cost=Decimal("0.01"),
                    currency="USD",
                    policy_rank=0,
                ),
                OrchestrationCandidate(
                    target=expensive,
                    comparison_group="CURRENCY:USD",
                    comparison_group_rank=0,
                    estimated_cost=Decimal("0.02"),
                    currency="USD",
                    policy_rank=1,
                ),
            )
        )
        provider = DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="task-orchestration-auto",
                seed="frozen-policy",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=(
                            SimulatorScenario.REQUIREMENT_NOT_SATISFIED
                        ),
                        expected_cycle=1,
                        expected_attempt_index=1,
                        expected_target=ProviderTarget(
                            cheap.provider_id,
                            cheap.model_id,
                            cheap.reasoning_profile,
                        ),
                    ),
                    SimulatorStep(
                        scenario=SimulatorScenario.SUCCESS,
                        expected_cycle=1,
                        expected_attempt_index=2,
                        expected_target=ProviderTarget(
                            expensive.provider_id,
                            expensive.model_id,
                            expensive.reasoning_profile,
                        ),
                    ),
                ),
            )
        )
        task_handler = TaskOrchestrationHandler(
            sessions=sessions,
            tasks=tasks,
            attempts=attempts,
            policies=policies,
            candidates=candidates,
            credentials=credentials,
            work_queue=queue,
        )
        attempt_handler = ProviderAttemptHandler(
            store=attempts,
            resolve_adapter=lambda _attempt: provider,
            continuation_queue=queue,
        )
        registry = HandlerRegistry()
        registry.register(
            TASK_ORCHESTRATION_OPERATION,
            TASK_ORCHESTRATION_OPERATION_VERSION,
            task_handler,
        )
        registry.register(
            PROVIDER_ATTEMPT_OPERATION,
            PROVIDER_ATTEMPT_OPERATION_VERSION,
            attempt_handler,
        )
        worker = WorkerProcess(
            queue=queue,
            registry=registry,
            queue_name=QueueName.EXECUTION,
            concurrency=1,
            lease_seconds=30,
            poll_interval_seconds=0.01,
            shutdown_grace_seconds=2,
            process_id="orchestration-test-worker",
        )
        await queue.enqueue(
            build_task_orchestration_work_item(
                scope=scope,
                task_id=task.task_id,
                available_at=now,
            )
        )

        try:
            for _index in range(8):
                await worker.run_once()
                persisted = await tasks.get_owned(
                    scope=scope,
                    task_id=task.task_id,
                )
                assert persisted is not None
                if persisted.status.terminal:
                    break

            persisted = await tasks.get_owned(
                scope=scope,
                task_id=task.task_id,
            )
            assert persisted is not None
            self.assertIs(persisted.status, TaskStatus.COMPLETE)
            self.assertEqual(
                persisted.accepted_requirements,
                ("VALID_RESPONSE",),
            )
            self.assertEqual(persisted.missing_requirements, ())
            self.assertIsNotNone(persisted.result_reference)

            attempt_rows = await attempts.list_for_task(
                scope=scope,
                task_id=task.task_id,
            )
            self.assertEqual(
                [item.target for item in attempt_rows],
                [cheap, expensive],
            )
            self.assertTrue(
                all(
                    item.provider_account_id
                    == credentials.account_id
                    for item in attempt_rows
                )
            )
            self.assertTrue(
                all(
                    item.provider_credential_id
                    == credentials.credential_id
                    for item in attempt_rows
                )
            )
            self.assertEqual(len(provider.invocations), 2)

            effective = await policies.get_effective(
                tenant_id=scope.tenant_id,
                client_id=scope.client_id,
            )
            assert effective is not None
            self.assertEqual(
                effective.version.policy_version_id,
                current.policy_version_id,
            )
            self.assertEqual(
                persisted.effective_policy_version_id,
                frozen.policy_version_id,
            )
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
