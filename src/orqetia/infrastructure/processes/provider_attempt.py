"""Worker handler for crash-safe single-attempt provider dispatch."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from orqetia.execution.attempt_postgres import (
    PROVIDER_DISPATCH_OPERATION,
    PROVIDER_DISPATCH_VERSION,
    PostgresProviderAttemptStore,
)
from orqetia.execution.attempts import ProviderAttempt, ProviderAttemptStatus
from orqetia.providers import ProviderAdapter, ProviderAttemptRequest, ProviderTarget
from orqetia.shared.messaging import WorkLease

from .worker import HandlerOutcome

AdapterResolver = Callable[[ProviderTarget], ProviderAdapter]


class ProviderAttemptWorkHandler:
    """Dispatch exactly one persisted provider attempt.

    Once DISPATCHING is durably committed, a redelivery never replays the
    provider call implicitly. It terminalizes the attempt as AMBIGUOUS instead.
    """

    operation_type = PROVIDER_DISPATCH_OPERATION
    operation_version = PROVIDER_DISPATCH_VERSION

    def __init__(
        self,
        *,
        store: PostgresProviderAttemptStore,
        resolve_adapter: AdapterResolver,
    ) -> None:
        self._store = store
        self._resolve_adapter = resolve_adapter

    async def __call__(self, lease: WorkLease) -> HandlerOutcome:
        attempt_id = self._attempt_id(lease)
        attempt = await self._store.get(attempt_id)
        if attempt is None:
            return HandlerOutcome.dead_letter(error_class="PROVIDER_ATTEMPT_NOT_FOUND")
        self._validate_lease_binding(lease, attempt)

        if attempt.status.terminal:
            return HandlerOutcome.complete()

        if attempt.status is ProviderAttemptStatus.DISPATCHING:
            await self._terminalize_redelivery_as_ambiguous(attempt)
            return HandlerOutcome.complete()

        try:
            adapter = self._resolve_adapter(attempt.target)
        except LookupError:
            return HandlerOutcome.dead_letter(
                error_class="PROVIDER_ADAPTER_UNAVAILABLE"
            )

        dispatch_time = datetime.now(UTC)
        if not await self._store.begin_dispatch(
            attempt.attempt_id,
            occurred_at=dispatch_time,
            expected_version=attempt.version,
        ):
            current = await self._store.get(attempt.attempt_id)
            if current is None:
                return HandlerOutcome.dead_letter(
                    error_class="PROVIDER_ATTEMPT_NOT_FOUND"
                )
            if current.status.terminal:
                return HandlerOutcome.complete()
            if current.status is ProviderAttemptStatus.DISPATCHING:
                await self._terminalize_redelivery_as_ambiguous(current)
                return HandlerOutcome.complete()
            raise RuntimeError("provider attempt dispatch CAS lost unexpectedly")

        dispatch_version = attempt.version + 1
        request = ProviderAttemptRequest(
            attempt_id=attempt.attempt_id,
            operation=attempt.operation,
            target=attempt.target,
            cycle=attempt.cycle,
            attempt_index=attempt.attempt_index,
            request_reference=attempt.request_reference,
            request_fingerprint=attempt.request_fingerprint,
            missing_requirements=attempt.missing_requirements,
            task_id=attempt.task_id,
            session_id=attempt.session_id,
        )

        try:
            result = await adapter.invoke(request)
        except Exception as exc:
            changed = await self._store.mark_ambiguous(
                attempt.attempt_id,
                error_class=type(exc).__name__,
                occurred_at=datetime.now(UTC),
                expected_version=dispatch_version,
            )
            if not changed:
                raise RuntimeError(
                    "provider attempt ambiguity could not be persisted"
                ) from exc
            return HandlerOutcome.complete()

        changed = await self._store.complete(
            attempt.attempt_id,
            result=result,
            occurred_at=datetime.now(UTC),
            expected_version=dispatch_version,
        )
        if changed:
            return HandlerOutcome.complete()

        current = await self._store.get(attempt.attempt_id)
        if current is not None and current.status.terminal:
            return HandlerOutcome.complete()
        raise RuntimeError("provider attempt result could not be persisted")

    @staticmethod
    def _attempt_id(lease: WorkLease) -> UUID:
        if lease.resource_type != "provider_attempt" or lease.resource_id is None:
            raise ValueError("provider dispatch work must reference provider_attempt")
        raw = lease.payload.get("attempt_id")
        if not isinstance(raw, str):
            raise ValueError("provider dispatch payload requires attempt_id string")
        attempt_id = UUID(raw)
        if attempt_id != lease.resource_id:
            raise ValueError("work resource_id and payload attempt_id must match")
        return attempt_id

    @staticmethod
    def _validate_lease_binding(
        lease: WorkLease,
        attempt: ProviderAttempt,
    ) -> None:
        if lease.work_id != attempt.work_id:
            raise ValueError("provider attempt is bound to a different work item")
        if attempt.ownership is None:
            if lease.tenant_id is not None or lease.client_id is not None:
                raise ValueError("taskless attempt work has unexpected ownership")
            return
        if (
            lease.tenant_id != attempt.ownership.tenant_id
            or lease.client_id != attempt.ownership.client_id
        ):
            raise ValueError("provider attempt work ownership mismatch")

    async def _terminalize_redelivery_as_ambiguous(
        self,
        attempt: ProviderAttempt,
    ) -> None:
        changed = await self._store.mark_ambiguous(
            attempt.attempt_id,
            error_class="LEASE_REDELIVERY_AFTER_DISPATCH_START",
            occurred_at=datetime.now(UTC),
            expected_version=attempt.version,
        )
        if changed:
            return
        current = await self._store.get(attempt.attempt_id)
        if current is None or not current.status.terminal:
            raise RuntimeError("ambiguous provider attempt could not be terminalized")
