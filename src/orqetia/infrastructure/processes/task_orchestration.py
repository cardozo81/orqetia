"""Durable task-orchestration worker over the canonical execution policy engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from orqetia.control_plane import (
    ExecutionPolicyRepository,
    ProviderCredentialSelection,
)
from orqetia.execution import (
    AttemptObservation,
    CanonicalOrchestrationPolicyEngine,
    ExecutionSession,
    ExecutionSessionStore,
    ExecutionTargetSnapshot,
    ExecutionTask,
    ExecutionTaskStore,
    OrchestrationCandidate,
    OrchestrationCyclePlan,
    OrchestrationDecision,
    OrchestrationDisposition,
    OrchestrationPolicy,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
    ProviderAttemptStore,
    QuarantineState,
    TargetHealth,
    TargetRuntimeState,
    TaskCycleDecision,
    TaskReasonEnvelope,
    TaskStatus,
)
from orqetia.shared.messaging import (
    DataClassification,
    QueueName,
    WorkItem,
    WorkLease,
    WorkQueuePort,
)

from .provider_attempts import build_provider_attempt_work_item
from .worker import HandlerOutcome

TASK_ORCHESTRATION_OPERATION = "execution.task.orchestrate"
TASK_ORCHESTRATION_OPERATION_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(UTC)


def task_orchestration_work_id(
    task_id: UUID,
    *,
    continuation_key: str | None = None,
) -> UUID:
    identity = (
        f"orqetia:task-orchestration:{task_id}"
        if continuation_key is None
        else f"orqetia:task-orchestration:{task_id}:{continuation_key}"
    )
    return uuid5(NAMESPACE_URL, identity)


def provider_dispatch_work_id(attempt_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"orqetia:provider-dispatch:{attempt_id}")


def provider_attempt_id(
    task_id: UUID,
    cycle: int,
    attempt_index: int,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orqetia:provider-attempt:{task_id}:{cycle}:{attempt_index}",
    )


def build_task_orchestration_work_item(
    *,
    scope: OwnershipScope,
    task_id: UUID,
    available_at: datetime,
    continuation_key: str | None = None,
    correlation_id: UUID | None = None,
    causation_id: UUID | None = None,
    trace_id: str | None = None,
) -> WorkItem:
    if available_at.tzinfo is None or available_at.utcoffset() is None:
        raise ValueError("available_at must be timezone-aware")
    return WorkItem(
        work_id=task_orchestration_work_id(
            task_id,
            continuation_key=continuation_key,
        ),
        queue_name=QueueName.EXECUTION,
        operation_type=TASK_ORCHESTRATION_OPERATION,
        operation_version=TASK_ORCHESTRATION_OPERATION_VERSION,
        tenant_id=scope.tenant_id,
        client_id=scope.client_id,
        resource_type="execution_task",
        resource_id=task_id,
        data_classification=DataClassification.CLIENT_PRIVATE,
        payload={"task_id": str(task_id)},
        available_at=available_at,
        correlation_id=correlation_id,
        causation_id=causation_id,
        trace_id=trace_id,
        logical_operation_id=str(task_id),
    )


class OrchestrationCandidateResolver(Protocol):
    async def resolve(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        occurred_at: datetime,
    ) -> tuple[OrchestrationCandidate, ...]: ...


class ProviderCredentialSelector(Protocol):
    async def select_credential(
        self,
        *,
        provider_id: str,
        occurred_at: datetime,
        region: str | None = None,
    ) -> ProviderCredentialSelection: ...


class TaskOrchestrationHandler:
    """Advance one durable task by replaying canonical policy over durable facts."""

    def __init__(
        self,
        *,
        sessions: ExecutionSessionStore,
        tasks: ExecutionTaskStore,
        attempts: ProviderAttemptStore,
        policies: ExecutionPolicyRepository,
        candidates: OrchestrationCandidateResolver,
        credentials: ProviderCredentialSelector,
        work_queue: WorkQueuePort,
        engine: CanonicalOrchestrationPolicyEngine | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._sessions = sessions
        self._tasks = tasks
        self._attempts = attempts
        self._policies = policies
        self._candidates = candidates
        self._credentials = credentials
        self._queue = work_queue
        self._engine = engine or CanonicalOrchestrationPolicyEngine()
        self._clock = clock

    async def __call__(self, lease: WorkLease) -> HandlerOutcome:
        scope, task_id = self._lease_identity(lease)
        if scope is None or task_id is None:
            return self._retry("INVALID_TASK_WORK_IDENTITY")

        task = await self._tasks.get_owned(scope=scope, task_id=task_id)
        if task is None:
            return self._retry("TASK_NOT_FOUND")
        if task.status.terminal:
            return HandlerOutcome.complete()

        task = await self._ensure_running(task)
        if task is None:
            return self._retry("TASK_STATE_CONFLICT")

        attempts = await self._attempts.list_for_task(
            scope=scope,
            task_id=task_id,
        )
        if task.status is TaskStatus.CANCELLING:
            return await self._handle_cancelling(task, attempts, lease)
        if task.status is not TaskStatus.RUNNING:
            return self._retry("TASK_NOT_RUNNABLE")

        active = tuple(item for item in attempts if not item.status.terminal)
        if active:
            if len(active) != 1:
                return self._retry("MULTIPLE_ACTIVE_ATTEMPTS")
            current = active[0]
            if current.status is ProviderAttemptStatus.PREPARED:
                await self._enqueue_attempt(
                    current,
                    available_at=await self._attempt_available_at(
                        task,
                        current,
                    ),
                    lease=lease,
                )
            return HandlerOutcome.complete()

        session = await self._sessions.get_owned(
            scope=scope,
            session_id=task.session_id,
        )
        if session is None:
            return self._retry("SESSION_NOT_FOUND")
        policy_version = await self._policies.get_version(
            tenant_id=scope.tenant_id,
            client_id=scope.client_id,
            policy_version_id=task.effective_policy_version_id,
        )
        if policy_version is None:
            await self._terminalize(
                task,
                status=TaskStatus.UNAVAILABLE,
                reason_code="FROZEN_POLICY_UNAVAILABLE",
                result_reference=task.result_reference,
            )
            return HandlerOutcome.complete()
        policy = policy_version.orchestration_policy()

        if task.current_cycle == 0:
            return await self._start_cycle(
                task=task,
                session=session,
                policy=policy,
                attempts=attempts,
                prior_retry_after_seconds=(),
                lease=lease,
            )

        recorded = await self._tasks.get_cycle_decision(
            scope=scope,
            task_id=task.task_id,
            cycle_index=task.current_cycle,
        )
        if recorded is None:
            return self._retry("CYCLE_PLAN_NOT_FOUND")
        plan = self._plan_from_recorded(task=task, recorded=recorded)
        cycle_attempts = tuple(
            item
            for item in attempts
            if item.cycle == recorded.cycle_index
        )
        if any(not item.status.terminal for item in cycle_attempts):
            return self._retry("ACTIVE_ATTEMPT_NOT_OBSERVED")
        observations = tuple(
            AttemptObservation.from_attempt(item)
            for item in cycle_attempts
        )
        before = sum(
            1
            for item in attempts
            if item.cycle < recorded.cycle_index
        )
        decision = self._engine.evaluate_cycle(
            plan=plan,
            policy=policy,
            observations=observations,
            attempts_used_before_cycle=before,
        )
        return await self._apply_decision(
            task=task,
            session=session,
            policy=policy,
            attempts=attempts,
            plan_recorded_at=recorded.recorded_at,
            decision=decision,
            lease=lease,
            retry_after_seconds=tuple(
                item.retry_after_seconds
                for item in cycle_attempts
            ),
        )

    def _lease_identity(
        self,
        lease: WorkLease,
    ) -> tuple[OwnershipScope | None, UUID | None]:
        if lease.tenant_id is None or lease.client_id is None:
            return None, None
        raw_task_id = lease.payload.get("task_id")
        try:
            task_id = UUID(str(raw_task_id))
        except (TypeError, ValueError, AttributeError):
            return None, None
        if lease.resource_id is not None and lease.resource_id != task_id:
            return None, None
        return OwnershipScope(lease.tenant_id, lease.client_id), task_id

    async def _ensure_running(
        self,
        task: ExecutionTask,
    ) -> ExecutionTask | None:
        current = task
        if current.status is TaskStatus.CREATED:
            await self._tasks.transition(
                scope=current.ownership,
                task_id=current.task_id,
                expected_version=current.version,
                target_status=TaskStatus.QUEUED,
                occurred_at=self._clock(),
            )
            current = await self._tasks.get_owned(
                scope=current.ownership,
                task_id=current.task_id,
            )
            if current is None:
                return None
        if current.status is TaskStatus.QUEUED:
            changed = await self._tasks.transition(
                scope=current.ownership,
                task_id=current.task_id,
                expected_version=current.version,
                target_status=TaskStatus.RUNNING,
                occurred_at=self._clock(),
            )
            current = await self._tasks.get_owned(
                scope=current.ownership,
                task_id=current.task_id,
            )
            if current is None:
                return None
            if not changed and current.status is not TaskStatus.RUNNING:
                return None
        return current

    async def _handle_cancelling(
        self,
        task: ExecutionTask,
        attempts: tuple[ProviderAttempt, ...],
        lease: WorkLease,
    ) -> HandlerOutcome:
        active = tuple(item for item in attempts if not item.status.terminal)
        if active:
            if len(active) != 1:
                return self._retry("MULTIPLE_ACTIVE_ATTEMPTS")
            if active[0].status is ProviderAttemptStatus.PREPARED:
                await self._enqueue_attempt(
                    active[0],
                    available_at=self._clock(),
                    lease=lease,
                )
            return HandlerOutcome.complete()
        await self._terminalize(
            task,
            status=TaskStatus.CANCELLED,
            reason_code="TASK_CANCELLED",
            result_reference=task.result_reference,
        )
        return HandlerOutcome.complete()

    async def _start_cycle(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        policy: OrchestrationPolicy,
        attempts: tuple[ProviderAttempt, ...],
        prior_retry_after_seconds: tuple[int | None, ...],
        lease: WorkLease,
    ) -> HandlerOutcome:
        now = self._clock()
        candidates = await self._candidates.resolve(
            task=task,
            session=session,
            occurred_at=now,
        )
        planned = self._engine.plan_next_cycle(
            task=task,
            session=session,
            policy=policy,
            candidates=candidates,
            now=now,
            prior_retry_after_seconds=prior_retry_after_seconds,
        )
        if isinstance(planned, OrchestrationDecision):
            return await self._apply_terminal_decision(
                task,
                session,
                planned,
            )

        recorded = planned.as_task_cycle_decision(recorded_at=now)
        changed = await self._tasks.record_cycle_decision(
            scope=task.ownership,
            task_id=task.task_id,
            expected_version=task.version,
            decision=recorded,
        )
        if not changed:
            return self._retry("TASK_VERSION_CONFLICT")
        refreshed = await self._tasks.get_owned(
            scope=task.ownership,
            task_id=task.task_id,
        )
        if refreshed is None:
            return self._retry("TASK_NOT_FOUND_AFTER_CYCLE_RECORD")
        decision = self._engine.evaluate_cycle(
            plan=planned,
            policy=policy,
            observations=(),
            attempts_used_before_cycle=len(attempts),
        )
        return await self._apply_decision(
            task=refreshed,
            session=session,
            policy=policy,
            attempts=attempts,
            plan_recorded_at=recorded.recorded_at,
            decision=decision,
            lease=lease,
            retry_after_seconds=prior_retry_after_seconds,
        )

    async def _apply_decision(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        policy: OrchestrationPolicy,
        attempts: tuple[ProviderAttempt, ...],
        plan_recorded_at: datetime,
        decision: OrchestrationDecision,
        lease: WorkLease,
        retry_after_seconds: tuple[int | None, ...],
    ) -> HandlerOutcome:
        if decision.disposition is OrchestrationDisposition.DISPATCH:
            await self._apply_quarantine(
                session=session,
                targets=decision.terminal_quarantine_targets,
            )
            assert decision.next_target is not None
            assert decision.attempt_index is not None
            attempt = await self._prepare_attempt(
                task=task,
                target=decision.next_target,
                cycle=decision.cycle_index,
                attempt_index=decision.attempt_index,
                missing_requirements=decision.progress.missing,
            )
            available_at = max(
                self._clock(),
                plan_recorded_at
                + timedelta(seconds=decision.delay_seconds),
            )
            await self._enqueue_attempt(
                attempt,
                available_at=available_at,
                lease=lease,
            )
            return HandlerOutcome.complete()

        if decision.disposition is OrchestrationDisposition.NEXT_CYCLE:
            await self._apply_quarantine(
                session=session,
                targets=decision.terminal_quarantine_targets,
            )
            refreshed_session = await self._sessions.get_owned(
                scope=task.ownership,
                session_id=task.session_id,
            )
            if refreshed_session is None:
                return self._retry("SESSION_NOT_FOUND_AFTER_QUARANTINE")
            progress_task = replace(
                task,
                accepted_requirements=decision.progress.accepted,
                missing_requirements=decision.progress.missing,
                result_reference=decision.result_reference,
            )
            return await self._start_cycle(
                task=progress_task,
                session=refreshed_session,
                policy=policy,
                attempts=attempts,
                prior_retry_after_seconds=retry_after_seconds,
                lease=lease,
            )

        return await self._apply_terminal_decision(
            task,
            session,
            decision,
        )

    async def _apply_terminal_decision(
        self,
        task: ExecutionTask,
        session: ExecutionSession,
        decision: OrchestrationDecision,
    ) -> HandlerOutcome:
        await self._apply_quarantine(
            session=session,
            targets=decision.terminal_quarantine_targets,
        )
        mapping = {
            OrchestrationDisposition.COMPLETE: TaskStatus.COMPLETE,
            OrchestrationDisposition.PARTIAL: TaskStatus.PARTIAL,
            OrchestrationDisposition.UNAVAILABLE: TaskStatus.UNAVAILABLE,
            OrchestrationDisposition.CANCELLED: TaskStatus.CANCELLED,
        }
        status = mapping.get(decision.disposition)
        if status is None:
            return self._retry("UNSUPPORTED_TERMINAL_DISPOSITION")
        await self._terminalize(
            task,
            status=status,
            reason_code=decision.reason_code,
            result_reference=decision.result_reference,
            accepted=decision.progress.accepted,
            missing=decision.progress.missing,
        )
        return HandlerOutcome.complete()

    async def _prepare_attempt(
        self,
        *,
        task: ExecutionTask,
        target: ExecutionTargetSnapshot,
        cycle: int,
        attempt_index: int,
        missing_requirements: tuple[str, ...],
    ) -> ProviderAttempt:
        attempt_id = provider_attempt_id(
            task.task_id,
            cycle,
            attempt_index,
        )
        existing = await self._attempts.get_owned(
            scope=task.ownership,
            attempt_id=attempt_id,
        )
        if existing is not None:
            self._validate_existing_attempt(
                existing=existing,
                task=task,
                target=target,
                cycle=cycle,
                attempt_index=attempt_index,
            )
            return existing

        selected = await self._credentials.select_credential(
            provider_id=target.provider_id,
            occurred_at=self._clock(),
        )
        if selected.provider_id != target.provider_id:
            raise ValueError(
                "credential selector returned cross-provider credential"
            )
        now = self._clock()
        attempt = ProviderAttempt(
            attempt_id=attempt_id,
            task_id=task.task_id,
            session_id=task.session_id,
            ownership=task.ownership,
            operation=task.operation,
            target=target,
            cycle=cycle,
            attempt_index=attempt_index,
            request_reference=task.payloads.input_reference,
            request_fingerprint=task.payloads.input_fingerprint,
            status=ProviderAttemptStatus.PREPARED,
            provider_account_id=selected.provider_account_id,
            provider_credential_id=selected.provider_credential_id,
            missing_requirements=missing_requirements,
            created_at=now,
            updated_at=now,
        )
        await self._attempts.create(attempt)
        return attempt

    @staticmethod
    def _validate_existing_attempt(
        *,
        existing: ProviderAttempt,
        task: ExecutionTask,
        target: ExecutionTargetSnapshot,
        cycle: int,
        attempt_index: int,
    ) -> None:
        if (
            existing.task_id != task.task_id
            or existing.session_id != task.session_id
            or existing.target != target
            or existing.cycle != cycle
            or existing.attempt_index != attempt_index
        ):
            raise ValueError(
                "deterministic provider attempt identity conflict"
            )

    async def _enqueue_attempt(
        self,
        attempt: ProviderAttempt,
        *,
        available_at: datetime,
        lease: WorkLease,
    ) -> None:
        await self._queue.enqueue(
            build_provider_attempt_work_item(
                attempt,
                work_id=provider_dispatch_work_id(
                    attempt.attempt_id
                ),
                available_at=available_at,
                correlation_id=lease.correlation_id,
                causation_id=lease.work_id,
                trace_id=lease.trace_id,
            )
        )

    async def _attempt_available_at(
        self,
        task: ExecutionTask,
        attempt: ProviderAttempt,
    ) -> datetime:
        if task.current_cycle != attempt.cycle:
            return self._clock()
        recorded = await self._tasks.get_cycle_decision(
            scope=task.ownership,
            task_id=task.task_id,
            cycle_index=attempt.cycle,
        )
        if recorded is None:
            return self._clock()
        return max(
            self._clock(),
            recorded.recorded_at
            + timedelta(seconds=recorded.delay_seconds),
        )

    async def _apply_quarantine(
        self,
        *,
        session: ExecutionSession,
        targets: tuple[ExecutionTargetSnapshot, ...],
    ) -> None:
        for target in targets:
            await self._sessions.put_target_runtime_state(
                scope=session.ownership,
                session_id=session.session_id,
                state=TargetRuntimeState(
                    target=target,
                    health=TargetHealth.UNAVAILABLE,
                    quarantine=QuarantineState.TERMINAL,
                    quarantine_reason_code=(
                        "TERMINAL_PROVIDER_OUTCOME"
                    ),
                    updated_at=self._clock(),
                ),
            )

    async def _terminalize(
        self,
        task: ExecutionTask,
        *,
        status: TaskStatus,
        reason_code: str | None,
        result_reference: str | None,
        accepted: tuple[str, ...] | None = None,
        missing: tuple[str, ...] | None = None,
    ) -> None:
        changed = await self._tasks.transition(
            scope=task.ownership,
            task_id=task.task_id,
            expected_version=task.version,
            target_status=status,
            occurred_at=self._clock(),
            accepted_requirements=accepted,
            missing_requirements=missing,
            result_reference=result_reference,
            reason=TaskReasonEnvelope(reason_code=reason_code),
        )
        if changed:
            return
        current = await self._tasks.get_owned(
            scope=task.ownership,
            task_id=task.task_id,
        )
        if current is None or not current.status.terminal:
            raise RuntimeError(
                "task terminal transition version conflict"
            )

    @staticmethod
    def _plan_from_recorded(
        *,
        task: ExecutionTask,
        recorded: TaskCycleDecision,
    ) -> OrchestrationCyclePlan:
        candidates = tuple(
            OrchestrationCandidate(
                target=target,
                comparison_group="FROZEN",
                comparison_group_rank=0,
                estimated_cost=Decimal("0"),
                policy_rank=index,
            )
            for index, target in enumerate(recorded.candidate_order)
        )
        return OrchestrationCyclePlan(
            cycle_index=recorded.cycle_index,
            execution_mode=task.requested_execution_mode,
            candidates=candidates,
            accepted_snapshot=recorded.accepted_snapshot,
            missing_snapshot=recorded.missing_snapshot,
            delay_seconds=recorded.delay_seconds,
            result_reference_snapshot=(
                recorded.result_reference_snapshot
            ),
        )

    def _retry(self, error_class: str) -> HandlerOutcome:
        return HandlerOutcome.requeue_infrastructure(
            available_at=self._clock() + timedelta(seconds=1),
            error_class=error_class,
        )
