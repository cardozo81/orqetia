"""Worker handler for one pre-resolved provider attempt."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from orqetia.execution import (
    DispatchAction,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
    ProviderAttemptStore,
)
from orqetia.observability import (
    EventEmitter,
    JsonEventEmitter,
    provider_attempt_telemetry_fields,
    work_telemetry_fields,
)
from orqetia.providers import (
    ProviderAdapter,
    ProviderAttemptRequest,
    ProviderTarget,
)
from orqetia.shared.messaging import (
    DataClassification,
    QueueName,
    WorkItem,
    WorkLease,
    WorkQueuePort,
)

from .worker import HandlerOutcome

PROVIDER_ATTEMPT_OPERATION = "provider.attempt.dispatch"
PROVIDER_ATTEMPT_OPERATION_VERSION = 1

AdapterResolver = Callable[[ProviderTarget], ProviderAdapter | Awaitable[ProviderAdapter]]
AttemptAdapterResolver = Callable[
    [ProviderAttempt],
    ProviderAdapter | Awaitable[ProviderAdapter],
]
Clock = Callable[[], datetime]


class CompletedAttemptObserver(Protocol):
    async def record(self, attempt: ProviderAttempt) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_provider_attempt_work_item(
    attempt: ProviderAttempt,
    *,
    available_at: datetime,
    work_id: UUID | None = None,
    priority: int = 0,
    max_infrastructure_attempts: int = 5,
    correlation_id: UUID | None = None,
    causation_id: UUID | None = None,
    trace_id: str | None = None,
) -> WorkItem:
    if available_at.tzinfo is None or available_at.utcoffset() is None:
        raise ValueError("available_at must be timezone-aware")
    return WorkItem(
        work_id=work_id or uuid7(),
        queue_name=QueueName.EXECUTION,
        operation_type=PROVIDER_ATTEMPT_OPERATION,
        operation_version=PROVIDER_ATTEMPT_OPERATION_VERSION,
        tenant_id=attempt.ownership.tenant_id,
        client_id=attempt.ownership.client_id,
        resource_type="provider_attempt",
        resource_id=attempt.attempt_id,
        data_classification=DataClassification.CLIENT_PRIVATE,
        payload={"attempt_id": str(attempt.attempt_id)},
        available_at=available_at,
        priority=priority,
        max_infrastructure_attempts=max_infrastructure_attempts,
        correlation_id=correlation_id,
        causation_id=causation_id,
        trace_id=trace_id,
        logical_operation_id=str(attempt.attempt_id),
    )


class ProviderAttemptHandler:
    """Dispatch one prepared attempt and never infer logical retry policy."""

    def __init__(
        self,
        *,
        store: ProviderAttemptStore,
        resolve_adapter: AdapterResolver,
        resolve_attempt_adapter: AttemptAdapterResolver | None = None,
        infrastructure_retry_delay_seconds: int = 1,
        clock: Clock = _utc_now,
        telemetry: EventEmitter | None = None,
        continuation_queue: WorkQueuePort | None = None,
        completed_observer: CompletedAttemptObserver | None = None,
    ) -> None:
        if infrastructure_retry_delay_seconds < 1:
            raise ValueError("infrastructure_retry_delay_seconds must be >= 1")
        self._store = store
        self._resolve_adapter = resolve_adapter
        self._resolve_attempt_adapter = resolve_attempt_adapter
        self._infrastructure_retry_delay = infrastructure_retry_delay_seconds
        self._clock = clock
        self._telemetry = telemetry or JsonEventEmitter()
        self._continuation_queue = continuation_queue
        self._completed_observer = completed_observer

    async def __call__(self, lease: WorkLease) -> HandlerOutcome:
        if lease.tenant_id is None or lease.client_id is None:
            return self._infrastructure_failure(
                "MISSING_OWNERSHIP",
                lease=lease,
            )

        raw_attempt_id = lease.payload.get("attempt_id")
        try:
            attempt_id = UUID(str(raw_attempt_id))
        except (TypeError, ValueError, AttributeError):
            return self._infrastructure_failure(
                "INVALID_ATTEMPT_ID",
                lease=lease,
            )

        scope = OwnershipScope(
            tenant_id=lease.tenant_id,
            client_id=lease.client_id,
        )
        attempt = await self._store.get_owned(
            scope=scope,
            attempt_id=attempt_id,
        )
        if attempt is None:
            return self._infrastructure_failure(
                "ATTEMPT_NOT_FOUND",
                lease=lease,
            )

        target = ProviderTarget(
            provider_id=attempt.target.provider_id,
            model_id=attempt.target.model_id,
            reasoning_profile=attempt.target.reasoning_profile,
        )
        adapter: ProviderAdapter | None = None
        if attempt.status is ProviderAttemptStatus.PREPARED:
            try:
                adapter = await self._resolve(attempt)
            except Exception as exc:
                error_class = (
                    f"ADAPTER_RESOLUTION_{type(exc).__name__.upper()}"
                )
                self._emit_attempt(
                    "provider.adapter_resolution_failed",
                    lease,
                    attempt,
                    error_class=error_class,
                )
                return self._infrastructure_failure(error_class)

        claim = await self._store.claim_dispatch(
            scope=scope,
            attempt_id=attempt_id,
            work_id=lease.work_id,
            occurred_at=self._clock(),
        )
        if claim.action is not DispatchAction.DISPATCH:
            self._emit_attempt(
                "provider.dispatch_skipped",
                lease,
                claim.attempt,
                dispatch_action=claim.action.value,
            )
            if claim.action in {
                DispatchAction.SKIP_COMPLETED,
                DispatchAction.SKIP_TERMINAL,
                DispatchAction.MARKED_AMBIGUOUS,
                DispatchAction.CANCELLED_BY_TASK,
            }:
                observed = await self._observe_completed(
                    claim.attempt,
                )
                if observed is not None:
                    return observed
                continuation = await self._continue_task(
                    lease,
                    claim.attempt,
                )
                if continuation is not None:
                    return continuation
            return HandlerOutcome.complete()

        if adapter is None:
            try:
                adapter = await self._resolve(claim.attempt)
            except Exception as exc:
                error_class = (
                    f"ADAPTER_RESOLUTION_{type(exc).__name__.upper()}"
                )
                self._emit_attempt(
                    "provider.adapter_resolution_failed",
                    lease,
                    claim.attempt,
                    error_class=error_class,
                )
                await self._store.mark_ambiguous(
                    scope=scope,
                    attempt_id=attempt_id,
                    work_id=lease.work_id,
                    occurred_at=self._clock(),
                    error_class=error_class,
                )
                continuation = await self._continue_task(
                    lease,
                    claim.attempt,
                )
                return (
                    HandlerOutcome.complete()
                    if continuation is None
                    else continuation
                )

        journal = claim.attempt
        self._emit_attempt("provider.dispatch_started", lease, journal)
        request = ProviderAttemptRequest(
            attempt_id=journal.attempt_id,
            operation=journal.operation,
            target=target,
            cycle=journal.cycle,
            attempt_index=journal.attempt_index,
            request_reference=journal.request_reference,
            request_fingerprint=journal.request_fingerprint,
            missing_requirements=journal.missing_requirements,
            task_id=journal.task_id,
            session_id=journal.session_id,
            correlation_id=lease.correlation_id,
            trace_id=lease.trace_id,
        )

        try:
            result = await adapter.invoke(request)
        except Exception as exc:
            self._emit_attempt(
                "provider.dispatch_ambiguous",
                lease,
                journal,
                error_class=type(exc).__name__,
                attempt_status="AMBIGUOUS",
            )
            await self._store.mark_ambiguous(
                scope=scope,
                attempt_id=attempt_id,
                work_id=lease.work_id,
                occurred_at=self._clock(),
                error_class=type(exc).__name__,
            )
            continuation = await self._continue_task(lease, journal)
            return (
                HandlerOutcome.complete()
                if continuation is None
                else continuation
            )

        await self._store.complete_dispatch(
            scope=scope,
            attempt_id=attempt_id,
            work_id=lease.work_id,
            result=result,
            occurred_at=self._clock(),
        )
        completed = await self._store.get_owned(
            scope=scope,
            attempt_id=attempt_id,
        )
        if completed is None or completed.status is not ProviderAttemptStatus.COMPLETED:
            return self._infrastructure_failure(
                "COMPLETED_ATTEMPT_NOT_DURABLE"
            )
        self._emit_attempt(
            "provider.dispatch_completed",
            lease,
            journal,
            provider_outcome=result.outcome.value,
            provider_latency_ms=result.latency_ms,
            retry_after_seconds=result.retry_after_seconds,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
            attempt_status="COMPLETED",
        )
        observed = await self._observe_completed(completed)
        if observed is not None:
            return observed
        continuation = await self._continue_task(lease, completed)
        return (
            HandlerOutcome.complete()
            if continuation is None
            else continuation
        )

    async def _observe_completed(
        self,
        attempt: ProviderAttempt,
    ) -> HandlerOutcome | None:
        if (
            self._completed_observer is None
            or attempt.status is not ProviderAttemptStatus.COMPLETED
        ):
            return None
        try:
            await self._completed_observer.record(attempt)
        except Exception as exc:
            return self._infrastructure_failure(
                f"ATTEMPT_POSTPROCESS_{type(exc).__name__.upper()}"
            )
        return None

    async def _resolve(
        self,
        attempt: ProviderAttempt,
    ) -> ProviderAdapter:
        if self._resolve_attempt_adapter is not None:
            resolved = self._resolve_attempt_adapter(attempt)
        else:
            resolved = self._resolve_adapter(
                ProviderTarget(
                    provider_id=attempt.target.provider_id,
                    model_id=attempt.target.model_id,
                    reasoning_profile=attempt.target.reasoning_profile,
                )
            )
        if isinstance(resolved, Awaitable):
            return await resolved
        return resolved

    async def _continue_task(
        self,
        lease: WorkLease,
        attempt: ProviderAttempt,
    ) -> HandlerOutcome | None:
        if self._continuation_queue is None or attempt.task_id is None:
            return None
        from .task_orchestration import build_task_orchestration_work_item

        try:
            await self._continuation_queue.enqueue(
                build_task_orchestration_work_item(
                    scope=attempt.ownership,
                    task_id=attempt.task_id,
                    available_at=self._clock(),
                    continuation_key=f"attempt:{attempt.attempt_id}",
                    correlation_id=lease.correlation_id,
                    causation_id=lease.work_id,
                    trace_id=lease.trace_id,
                )
            )
        except Exception as exc:
            return self._infrastructure_failure(
                f"TASK_CONTINUATION_{type(exc).__name__.upper()}"
            )
        return None

    def _emit_attempt(
        self,
        event: str,
        lease: WorkLease,
        attempt: ProviderAttempt,
        **extra: object,
    ) -> None:
        fields = provider_attempt_telemetry_fields(lease, attempt)
        fields.update(extra)
        self._telemetry.emit(event, fields)

    def _infrastructure_failure(
        self,
        error_class: str,
        *,
        lease: WorkLease | None = None,
    ) -> HandlerOutcome:
        if lease is not None:
            fields = work_telemetry_fields(lease)
            fields["error_class"] = error_class
            self._telemetry.emit("provider.infrastructure_failure", fields)
        return HandlerOutcome.requeue_infrastructure(
            available_at=self._clock()
            + timedelta(seconds=self._infrastructure_retry_delay),
            error_class=error_class,
        )
