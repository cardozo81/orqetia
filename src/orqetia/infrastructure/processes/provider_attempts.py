"""Worker handler for one pre-resolved provider attempt."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from orqetia.execution import (
    DispatchAction,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStore,
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
)

from .worker import HandlerOutcome

PROVIDER_ATTEMPT_OPERATION = "provider.attempt.dispatch"
PROVIDER_ATTEMPT_OPERATION_VERSION = 1

AdapterResolver = Callable[[ProviderTarget], ProviderAdapter]
Clock = Callable[[], datetime]


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
        infrastructure_retry_delay_seconds: int = 1,
        clock: Clock = _utc_now,
    ) -> None:
        if infrastructure_retry_delay_seconds < 1:
            raise ValueError("infrastructure_retry_delay_seconds must be >= 1")
        self._store = store
        self._resolve_adapter = resolve_adapter
        self._infrastructure_retry_delay = infrastructure_retry_delay_seconds
        self._clock = clock

    async def __call__(self, lease: WorkLease) -> HandlerOutcome:
        if lease.tenant_id is None or lease.client_id is None:
            return self._infrastructure_failure("MISSING_OWNERSHIP")

        raw_attempt_id = lease.payload.get("attempt_id")
        try:
            attempt_id = UUID(str(raw_attempt_id))
        except (TypeError, ValueError, AttributeError):
            return self._infrastructure_failure("INVALID_ATTEMPT_ID")

        scope = OwnershipScope(
            tenant_id=lease.tenant_id,
            client_id=lease.client_id,
        )
        attempt = await self._store.get_owned(
            scope=scope,
            attempt_id=attempt_id,
        )
        if attempt is None:
            return self._infrastructure_failure("ATTEMPT_NOT_FOUND")

        target = ProviderTarget(
            provider_id=attempt.target.provider_id,
            model_id=attempt.target.model_id,
            reasoning_profile=attempt.target.reasoning_profile,
        )
        try:
            adapter = self._resolve_adapter(target)
        except Exception as exc:
            return self._infrastructure_failure(
                f"ADAPTER_RESOLUTION_{type(exc).__name__.upper()}"
            )

        claim = await self._store.claim_dispatch(
            scope=scope,
            attempt_id=attempt_id,
            work_id=lease.work_id,
            occurred_at=self._clock(),
        )
        if claim.action is not DispatchAction.DISPATCH:
            return HandlerOutcome.complete()

        journal = claim.attempt
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
        )

        try:
            result = await adapter.invoke(request)
        except Exception as exc:
            await self._store.mark_ambiguous(
                scope=scope,
                attempt_id=attempt_id,
                work_id=lease.work_id,
                occurred_at=self._clock(),
                error_class=type(exc).__name__,
            )
            return HandlerOutcome.complete()

        await self._store.complete_dispatch(
            scope=scope,
            attempt_id=attempt_id,
            work_id=lease.work_id,
            result=result,
            occurred_at=self._clock(),
        )
        return HandlerOutcome.complete()

    def _infrastructure_failure(self, error_class: str) -> HandlerOutcome:
        return HandlerOutcome.requeue_infrastructure(
            available_at=self._clock()
            + timedelta(seconds=self._infrastructure_retry_delay),
            error_class=error_class,
        )
