"""Client-facing execution runtime over canonical Execution/Control Plane contracts."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5, uuid7

from orqetia.control_plane import (
    EffectiveExecutionPolicy,
    EffectiveProviderCatalog,
)
from orqetia.execution import (
    ClientApiIdempotencyJournal,
    ClientExchangeEvidence,
    ClientExchangeEvidenceReader,
    ClientRequestArtifactWriter,
    ClientResultArtifactReader,
    ExecutionMode,
    ExecutionSession,
    ExecutionSessionStore,
    ExecutionTargetSnapshot,
    ExecutionTask,
    ExecutionTaskStore,
    OwnershipScope,
    ProviderAttempt,
    RequestedTargetSnapshot,
    SessionStatus,
    TaskPayloadReferences,
    TaskStatus,
)
from orqetia.execution import (
    SanitizedEvidenceRecord as SanitizedEvidenceRecord,
)
from orqetia.providers import PublicProviderTargetMetadata
from orqetia.shared.messaging import (
    DataClassification,
    QueueName,
    WorkItem,
    WorkQueuePort,
)

TASK_ORCHESTRATION_OPERATION = "execution.task.orchestrate"
TASK_ORCHESTRATION_OPERATION_VERSION = 1


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _work_id(task_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"orqetia:task-orchestration:{task_id}")


def _cursor(offset: int, *, query_fingerprint: str) -> str:
    raw = json.dumps(
        {"v": 1, "offset": offset, "query": query_fingerprint},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _offset(cursor: str | None, *, query_fingerprint: str) -> int:
    if cursor is None:
        return 0
    if not cursor.strip() or len(cursor) > 500:
        raise ValueError("invalid attempt cursor")
    padding = "=" * (-len(cursor) % 4)
    try:
        value = json.loads(
            base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        )
        if (
            not isinstance(value, dict)
            or value.get("v") != 1
            or value.get("query") != query_fingerprint
        ):
            raise ValueError("attempt cursor query fingerprint mismatch")
        offset = int(value["offset"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("invalid attempt cursor") from error
    if offset < 0:
        raise ValueError("invalid attempt cursor")
    return offset


@dataclass(frozen=True)
class ClientRequestedTarget:
    provider_id: str
    model_id: str | None = None
    reasoning_profile: str | None = None


@dataclass(frozen=True)
class ClientAttemptPage:
    items: tuple[ProviderAttempt, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ClientTaskResult:
    task_id: UUID
    status: str
    result: dict[str, object]
    accepted: tuple[str, ...]
    missing: tuple[str, ...]
    attempt_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class ClientProviderView:
    provider_id: str
    provider_name: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ClientModelView:
    provider_id: str
    model_id: str
    model_name: str
    capabilities: tuple[str, ...]
    reasoning_profiles: tuple[str, ...]


class EffectivePolicyResolver(Protocol):
    async def resolve_effective(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> EffectiveExecutionPolicy: ...


class EffectiveProviderCatalogResolver(Protocol):
    async def resolve_effective(self) -> EffectiveProviderCatalog: ...


class OperationRequirementResolver(Protocol):
    def requirements_for(
        self,
        *,
        operation: str,
        payload: dict[str, object],
    ) -> tuple[str, ...]:
        """Derive server-owned requirements; browser input cannot define them."""


class BaselineOperationRequirementResolver:
    def requirements_for(
        self,
        *,
        operation: str,
        payload: dict[str, object],
    ) -> tuple[str, ...]:
        del payload
        return (f"OPERATION:{operation}",)


class ProviderAttemptQueryStore(Protocol):
    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
    ) -> ProviderAttempt | None: ...

    async def list_for_task(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
    ) -> tuple[ProviderAttempt, ...]: ...


class ClientExecutionRuntimeError(RuntimeError):
    pass


class ClientExecutionNotFound(ClientExecutionRuntimeError):
    pass


class ClientExecutionConflict(ClientExecutionRuntimeError):
    pass


class ClientExecutionForbidden(ClientExecutionRuntimeError):
    pass


class ClientExecutionArtifactUnavailable(ClientExecutionRuntimeError):
    pass


class ClientExecutionRuntime:
    def __init__(
        self,
        *,
        sessions: ExecutionSessionStore,
        tasks: ExecutionTaskStore,
        attempts: ProviderAttemptQueryStore,
        policies: EffectivePolicyResolver,
        catalog: EffectiveProviderCatalogResolver,
        request_artifacts: ClientRequestArtifactWriter,
        results: ClientResultArtifactReader,
        exchanges: ClientExchangeEvidenceReader,
        idempotency: ClientApiIdempotencyJournal,
        work_queue: WorkQueuePort,
        requirements: OperationRequirementResolver | None = None,
    ) -> None:
        self._sessions = sessions
        self._tasks = tasks
        self._attempts = attempts
        self._policies = policies
        self._catalog = catalog
        self._request_artifacts = request_artifacts
        self._results = results
        self._exchanges = exchanges
        self._idempotency = idempotency
        self._work_queue = work_queue
        self._requirements = requirements or BaselineOperationRequirementResolver()

    async def create_session(
        self,
        *,
        scope: OwnershipScope,
        external_reference: str | None,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> ExecutionSession:
        _aware(occurred_at, "occurred_at")
        request_fingerprint = _fingerprint(
            {"external_reference": external_reference}
        )
        reservation = await self._idempotency.reserve(
            scope=scope,
            operation="SESSION_CREATE",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            resource_id=uuid7(),
            occurred_at=occurred_at,
        )
        existing = await self._sessions.get_owned(
            scope=scope,
            session_id=reservation.resource_id,
        )
        if existing is not None:
            return existing

        effective = await self._policies.resolve_effective(
            tenant_id=scope.tenant_id,
            client_id=scope.client_id,
        )
        session = ExecutionSession(
            session_id=reservation.resource_id,
            ownership=scope,
            status=SessionStatus.ACTIVE,
            policy=effective.version.session_policy_snapshot(),
            external_reference=external_reference,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        await self._sessions.create(session)
        return session

    async def get_session(
        self,
        *,
        scope: OwnershipScope,
        session_id: UUID,
    ) -> ExecutionSession:
        session = await self._sessions.get_owned(
            scope=scope,
            session_id=session_id,
        )
        if session is None:
            raise ClientExecutionNotFound("owned execution session not found")
        return session

    async def create_task(
        self,
        *,
        scope: OwnershipScope,
        session_id: UUID,
        operation: str,
        input_payload: dict[str, object],
        target: ClientRequestedTarget | None,
        external_reference: str | None,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> ExecutionTask:
        _aware(occurred_at, "occurred_at")
        session = await self.get_session(scope=scope, session_id=session_id)
        if session.status is not SessionStatus.ACTIVE:
            raise ClientExecutionConflict("session is not active")

        target_payload = (
            None
            if target is None
            else {
                "provider_id": target.provider_id,
                "model_id": target.model_id,
                "reasoning_profile": target.reasoning_profile,
            }
        )
        request_fingerprint = _fingerprint(
            {
                "session_id": str(session_id),
                "operation": operation,
                "input": input_payload,
                "target": target_payload,
                "external_reference": external_reference,
            }
        )
        reservation = await self._idempotency.reserve(
            scope=scope,
            operation=f"TASK_CREATE:{session_id}",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            resource_id=uuid7(),
            occurred_at=occurred_at,
        )
        existing = await self._tasks.get_owned(
            scope=scope,
            task_id=reservation.resource_id,
        )
        if existing is not None:
            await self._enqueue_task(existing, occurred_at=occurred_at)
            if existing.status is TaskStatus.CREATED:
                changed = await self._tasks.transition(
                    scope=scope,
                    task_id=existing.task_id,
                    expected_version=existing.version,
                    target_status=TaskStatus.QUEUED,
                    occurred_at=occurred_at,
                )
                if changed:
                    queued = await self._tasks.get_owned(
                        scope=scope,
                        task_id=existing.task_id,
                    )
                    if queued is not None:
                        return queued
            return existing

        mode = ExecutionMode.AUTO
        requested_target = None
        effective_target = None
        if target is not None:
            mode = ExecutionMode.EXPLICIT_TARGET
            effective_catalog = await self._catalog.resolve_effective()
            try:
                resolved = effective_catalog.registry.resolve_explicit_target(
                    requested=target,
                    required_capabilities=(),
                    authorized_targets=session.policy.authorized_targets,
                )
            except ValueError as error:
                raise ClientExecutionForbidden(
                    "explicit target is not authorized/eligible"
                ) from error
            requested_target = RequestedTargetSnapshot(
                provider_id=target.provider_id,
                model_id=target.model_id,
                reasoning_profile=target.reasoning_profile,
            )
            effective_target = ExecutionTargetSnapshot(
                provider_id=resolved.target.provider_id,
                model_id=resolved.target.model_id,
                reasoning_profile=resolved.target.reasoning_profile,
            )

        payloads = await self._request_artifacts.store_request(
            scope=scope,
            task_id=reservation.resource_id,
            payload=input_payload,
            occurred_at=occurred_at,
        )
        expected_fingerprint = _fingerprint(input_payload)
        if payloads.input_fingerprint != expected_fingerprint:
            raise ClientExecutionConflict(
                "request artifact fingerprint does not match canonical input"
            )

        requirements = self._requirements.requirements_for(
            operation=operation,
            payload=input_payload,
        )
        if not requirements:
            raise ClientExecutionConflict(
                "operation contract produced no requirements"
            )

        task = ExecutionTask(
            task_id=reservation.resource_id,
            session_id=session_id,
            ownership=scope,
            operation=operation,
            status=TaskStatus.CREATED,
            effective_policy_version_id=session.policy.effective_policy_version_id,
            requested_execution_mode=mode,
            requested_target=requested_target,
            effective_target=effective_target,
            requirements=requirements,
            accepted_requirements=(),
            missing_requirements=requirements,
            payloads=payloads,
            external_reference=external_reference,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        await self._tasks.create(task)
        await self._enqueue_task(task, occurred_at=occurred_at)
        try:
            changed = await self._tasks.transition(
                scope=scope,
                task_id=task.task_id,
                expected_version=task.version,
                target_status=TaskStatus.QUEUED,
                occurred_at=occurred_at,
            )
        except ValueError as error:
            raise ClientExecutionConflict(
                "task could not enter durable queue state"
            ) from error
        if changed:
            queued = await self._tasks.get_owned(scope=scope, task_id=task.task_id)
            if queued is not None:
                return queued
        return task

    async def get_task(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
    ) -> ExecutionTask:
        task = await self._tasks.get_owned(scope=scope, task_id=task_id)
        if task is None:
            raise ClientExecutionNotFound("owned task not found")
        return task

    async def get_task_result(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
    ) -> ClientTaskResult:
        task = await self.get_task(scope=scope, task_id=task_id)
        if not task.status.terminal:
            raise ClientExecutionConflict("task result is not terminal")
        result: dict[str, object] = {}
        if task.result_reference is not None:
            loaded = await self._results.read_result(
                scope=scope,
                result_reference=task.result_reference,
            )
            if loaded is None:
                raise ClientExecutionArtifactUnavailable(
                    "task result artifact is unavailable"
                )
            result = loaded
        attempts = await self._attempts.list_for_task(
            scope=scope,
            task_id=task_id,
        )
        return ClientTaskResult(
            task_id=task.task_id,
            status=task.status.value,
            result=result,
            accepted=task.accepted_requirements,
            missing=task.missing_requirements,
            attempt_ids=tuple(item.attempt_id for item in attempts),
        )

    async def cancel_task(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> ExecutionTask:
        _aware(occurred_at, "occurred_at")
        request_fingerprint = _fingerprint({"task_id": str(task_id)})
        await self._idempotency.reserve(
            scope=scope,
            operation=f"TASK_CANCEL:{task_id}",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            resource_id=task_id,
            occurred_at=occurred_at,
        )
        task = await self.get_task(scope=scope, task_id=task_id)
        if task.status.terminal or task.status is TaskStatus.CANCELLING:
            return task
        target = (
            TaskStatus.CANCELLING
            if task.status is TaskStatus.RUNNING
            else TaskStatus.CANCELLED
        )
        try:
            changed = await self._tasks.transition(
                scope=scope,
                task_id=task_id,
                expected_version=task.version,
                target_status=target,
                occurred_at=occurred_at,
            )
        except ValueError as error:
            raise ClientExecutionConflict(
                "task cannot be cancelled from its current state"
            ) from error
        if not changed:
            current = await self.get_task(scope=scope, task_id=task_id)
            if current.status.terminal or current.status is TaskStatus.CANCELLING:
                return current
            raise ClientExecutionConflict("task cancellation lost a concurrent update")
        return await self.get_task(scope=scope, task_id=task_id)

    async def list_task_attempts(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        cursor: str | None,
        limit: int,
    ) -> ClientAttemptPage:
        if not 1 <= limit <= 100:
            raise ValueError("attempt page limit must be between 1 and 100")
        await self.get_task(scope=scope, task_id=task_id)
        items = await self._attempts.list_for_task(
            scope=scope,
            task_id=task_id,
        )
        query_fingerprint = _fingerprint(
            {
                "tenant_id": str(scope.tenant_id),
                "client_id": str(scope.client_id),
                "task_id": str(task_id),
            }
        )
        start = _offset(cursor, query_fingerprint=query_fingerprint)
        page = items[start : start + limit]
        next_offset = start + len(page)
        return ClientAttemptPage(
            items=page,
            next_cursor=(
                _cursor(next_offset, query_fingerprint=query_fingerprint)
                if next_offset < len(items)
                else None
            ),
        )

    async def list_attempt_exchanges(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        attempt_id: UUID,
    ) -> tuple[ClientExchangeEvidence, ...]:
        await self.get_task(scope=scope, task_id=task_id)
        attempt = await self._attempts.get_owned(
            scope=scope,
            attempt_id=attempt_id,
        )
        if attempt is None or attempt.task_id != task_id:
            raise ClientExecutionNotFound("owned task attempt not found")
        evidence = await self._exchanges.list_for_attempt(
            scope=scope,
            attempt_id=attempt_id,
        )
        if any(item.attempt_id != attempt_id for item in evidence):
            raise ClientExecutionConflict("exchange evidence attempt mismatch")
        return evidence

    async def submission_queue_depth(self) -> int:
        probe = getattr(self._work_queue, "ready_depth", None)
        if probe is None:
            raise ClientExecutionRuntimeError(
                "execution queue backpressure probe is unavailable"
            )
        depth = await probe(QueueName.EXECUTION)
        if not isinstance(depth, int) or depth < 0:
            raise ClientExecutionRuntimeError(
                "execution queue backpressure probe returned invalid depth"
            )
        return depth

    async def list_providers(
        self,
        *,
        scope: OwnershipScope,
    ) -> tuple[ClientProviderView, ...]:
        metadata = await self._public_metadata(scope)
        grouped: dict[str, tuple[str, set[str]]] = {}
        for item in metadata:
            value = grouped.setdefault(
                item.provider_id,
                (item.display_name, set()),
            )
            value[1].update(capability.value for capability in item.capabilities)
        return tuple(
            ClientProviderView(
                provider_id=provider_id,
                provider_name=value[0],
                capabilities=tuple(sorted(value[1])),
            )
            for provider_id, value in sorted(grouped.items())
        )

    async def list_models(
        self,
        *,
        scope: OwnershipScope,
        provider_id: str | None = None,
    ) -> tuple[ClientModelView, ...]:
        metadata = await self._public_metadata(scope)
        grouped: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
        for item in metadata:
            if provider_id is not None and item.provider_id != provider_id:
                continue
            key = (item.provider_id, item.model_id)
            value = grouped.setdefault(key, (set(), set()))
            value[0].update(capability.value for capability in item.capabilities)
            value[1].add(item.reasoning_profile)
        return tuple(
            ClientModelView(
                provider_id=provider,
                model_id=model,
                model_name=model,
                capabilities=tuple(sorted(value[0])),
                reasoning_profiles=tuple(sorted(value[1])),
            )
            for (provider, model), value in sorted(grouped.items())
        )

    async def provider_name(
        self,
        *,
        scope: OwnershipScope,
        provider_id: str,
    ) -> str:
        metadata = await self._public_metadata(scope)
        return next(
            (
                item.display_name
                for item in metadata
                if item.provider_id == provider_id
            ),
            provider_id,
        )

    async def _public_metadata(
        self,
        scope: OwnershipScope,
    ) -> tuple[PublicProviderTargetMetadata, ...]:
        effective_policy = await self._policies.resolve_effective(
            tenant_id=scope.tenant_id,
            client_id=scope.client_id,
        )
        effective_catalog = await self._catalog.resolve_effective()
        return effective_catalog.registry.public_metadata(
            authorized_targets=effective_policy.version.authorized_targets
        )

    async def _enqueue_task(
        self,
        task: ExecutionTask,
        *,
        occurred_at: datetime,
    ) -> None:
        item = WorkItem(
            work_id=_work_id(task.task_id),
            queue_name=QueueName.EXECUTION,
            operation_type=TASK_ORCHESTRATION_OPERATION,
            operation_version=TASK_ORCHESTRATION_OPERATION_VERSION,
            tenant_id=task.ownership.tenant_id,
            client_id=task.ownership.client_id,
            resource_type="execution_task",
            resource_id=task.task_id,
            data_classification=DataClassification.CLIENT_PRIVATE,
            payload={"task_id": str(task.task_id)},
            available_at=occurred_at,
            logical_operation_id=str(task.task_id),
        )
        await self._work_queue.enqueue(item)


class InMemoryClientExecutionArtifacts:
    """Test/reference artifact port; production storage strategy remains separate."""

    def __init__(self) -> None:
        self._requests: dict[
            tuple[UUID, UUID, UUID],
            tuple[dict[str, object], TaskPayloadReferences],
        ] = {}
        self._results: dict[tuple[UUID, UUID, str], dict[str, object]] = {}
        self._exchanges: dict[
            tuple[UUID, UUID, UUID],
            tuple[ClientExchangeEvidence, ...],
        ] = {}

    async def store_request(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> TaskPayloadReferences:
        _aware(occurred_at, "occurred_at")
        fingerprint = _fingerprint(payload)
        reference = f"memory://client-request/{task_id}"
        refs = TaskPayloadReferences(
            input_reference=reference,
            input_fingerprint=fingerprint,
        )
        key = (scope.tenant_id, scope.client_id, task_id)
        current = self._requests.get(key)
        if current is not None:
            if current[1].input_fingerprint != fingerprint:
                raise ValueError("task request artifact conflict")
            return current[1]
        self._requests[key] = (dict(payload), refs)
        return refs

    async def read_result(
        self,
        *,
        scope: OwnershipScope,
        result_reference: str,
    ) -> dict[str, object] | None:
        value = self._results.get(
            (scope.tenant_id, scope.client_id, result_reference)
        )
        return None if value is None else dict(value)

    async def list_for_attempt(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
    ) -> tuple[ClientExchangeEvidence, ...]:
        return self._exchanges.get(
            (scope.tenant_id, scope.client_id, attempt_id),
            (),
        )

    def seed_result(
        self,
        *,
        scope: OwnershipScope,
        result_reference: str,
        value: dict[str, object],
    ) -> None:
        self._results[
            (scope.tenant_id, scope.client_id, result_reference)
        ] = dict(value)

    def seed_exchanges(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
        items: tuple[ClientExchangeEvidence, ...],
    ) -> None:
        self._exchanges[
            (scope.tenant_id, scope.client_id, attempt_id)
        ] = items
