from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid7

import httpx
import pytest

from orqetia.control_plane import (
    AuthorizedExecutionTarget,
    ClientPolicyAssignment,
    EffectiveExecutionPolicy,
    EffectiveProviderCatalog,
    ExecutionPolicyVersion,
    ProviderCatalogAssignment,
    ProviderCatalogVersion,
)
from orqetia.execution import (
    ExecutionSession,
    ExecutionTargetSnapshot,
    ExecutionTask,
    InMemoryClientApiIdempotencyJournal,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
    TaskStatus,
    validate_transition,
)
from orqetia.identity.authentication import AuthenticatedPrincipal
from orqetia.infrastructure.http import create_app
from orqetia.infrastructure.http.execution_runtime import (
    ClientExchangeEvidence,
    ClientExecutionRuntime,
    InMemoryClientExecutionArtifacts,
    SanitizedEvidenceRecord,
)
from orqetia.providers import (
    AdapterResolution,
    ProviderCapability,
    ProviderModelSpec,
    ProviderSpec,
    ReasoningProfileSpec,
)
from orqetia.shared.messaging import QueueName, WorkItem

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = json.loads(
    (ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json").read_text(
        encoding="utf-8"
    )
)
NOW = datetime(2026, 10, 6, 10, tzinfo=UTC)
TENANT = UUID("0199b39a-9bf1-7000-8000-000000000101")
CLIENT = UUID("0199b39a-9bf1-7000-8000-000000000102")
OTHER_TENANT = UUID("0199b39a-9bf1-7000-8000-000000000201")
OTHER_CLIENT = UUID("0199b39a-9bf1-7000-8000-000000000202")
ALL_SCOPES = frozenset(
    {
        "sessions:write",
        "sessions:read",
        "tasks:write",
        "tasks:read",
        "tasks:cancel",
        "tasks:target",
        "catalog:read",
    }
)


class FakeAuthenticator:
    async def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal:
        if token == "owner":
            tenant_id, client_id = TENANT, CLIENT
        elif token == "other":
            tenant_id, client_id = OTHER_TENANT, OTHER_CLIENT
        else:
            raise RuntimeError("unexpected synthetic token")
        return AuthenticatedPrincipal(
            subject_type="SERVICE_CLIENT",
            subject_id=token,
            tenant_id=str(tenant_id),
            client_id=str(client_id),
            scopes=ALL_SCOPES,
        )


class FakeSessionStore:
    def __init__(self) -> None:
        self.items: dict[UUID, ExecutionSession] = {}

    async def create(self, session: ExecutionSession) -> None:
        if session.session_id in self.items:
            raise ValueError("session exists")
        self.items[session.session_id] = session

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        session_id: UUID,
    ) -> ExecutionSession | None:
        session = self.items.get(session_id)
        return session if session is not None and session.ownership == scope else None

    async def put_target_runtime_state(self, **_kwargs: object) -> bool:
        return True


class FakeTaskStore:
    def __init__(self) -> None:
        self.items: dict[UUID, ExecutionTask] = {}

    async def create(self, task: ExecutionTask) -> None:
        if task.task_id in self.items:
            raise ValueError("task exists")
        self.items[task.task_id] = task

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
    ) -> ExecutionTask | None:
        task = self.items.get(task_id)
        return task if task is not None and task.ownership == scope else None

    async def transition(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        expected_version: int,
        target_status: TaskStatus,
        occurred_at: datetime,
        accepted_requirements: tuple[str, ...] | None = None,
        missing_requirements: tuple[str, ...] | None = None,
        result_reference: str | None = None,
        reason: object | None = None,
    ) -> bool:
        del reason
        task = await self.get_owned(scope=scope, task_id=task_id)
        if task is None:
            raise LookupError("task missing")
        if task.version != expected_version:
            return False
        validate_transition(task.status, target_status)
        accepted = (
            task.accepted_requirements
            if accepted_requirements is None
            else accepted_requirements
        )
        missing = (
            task.missing_requirements
            if missing_requirements is None
            else missing_requirements
        )
        updated = replace(
            task,
            status=target_status,
            accepted_requirements=accepted,
            missing_requirements=missing,
            result_reference=(
                task.result_reference
                if result_reference is None
                else result_reference
            ),
            queued_at=(
                occurred_at
                if target_status is TaskStatus.QUEUED
                else task.queued_at
            ),
            terminal_at=occurred_at if target_status.terminal else None,
            updated_at=occurred_at,
            version=task.version + 1,
        )
        self.items[task_id] = updated
        return True

    async def record_cycle_decision(self, **_kwargs: object) -> bool:
        return True

    def complete(self, task_id: UUID, *, result_reference: str) -> ExecutionTask:
        task = self.items[task_id]
        updated = replace(
            task,
            status=TaskStatus.COMPLETE,
            accepted_requirements=task.requirements,
            missing_requirements=(),
            result_reference=result_reference,
            terminal_at=NOW,
            updated_at=NOW,
            version=task.version + 1,
        )
        self.items[task_id] = updated
        return updated


class FakeAttemptStore:
    def __init__(self) -> None:
        self.items: dict[UUID, ProviderAttempt] = {}

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
    ) -> ProviderAttempt | None:
        attempt = self.items.get(attempt_id)
        return attempt if attempt is not None and attempt.ownership == scope else None

    async def list_for_task(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
    ) -> tuple[ProviderAttempt, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.items.values()
                    if item.ownership == scope and item.task_id == task_id
                ),
                key=lambda item: (item.cycle, item.attempt_index),
            )
        )


class FakeWorkQueue:
    def __init__(self) -> None:
        self.items: dict[UUID, WorkItem] = {}
        self.reported_depth = 0

    async def ready_depth(self, queue_name: QueueName) -> int:
        assert queue_name is QueueName.EXECUTION
        return self.reported_depth

    async def enqueue(self, item: WorkItem) -> None:
        self.items.setdefault(item.work_id, item)


class FakePolicyResolver:
    def __init__(self) -> None:
        target = AuthorizedExecutionTarget("alpha", "alpha-1", "standard")
        version = ExecutionPolicyVersion(
            policy_version_id=uuid7(),
            tenant_id=TENANT,
            client_id=CLIENT,
            version_number=1,
            max_cycles=3,
            max_attempts=6,
            cycle_delay_seconds=0,
            retry_after_cap_seconds=120,
            authorized_targets=(target,),
            created_at=NOW,
        )
        self.effective = EffectiveExecutionPolicy(
            version=version,
            assignment=ClientPolicyAssignment(
                tenant_id=TENANT,
                client_id=CLIENT,
                policy_version_id=version.policy_version_id,
                assignment_version=1,
                assigned_at=NOW,
            ),
        )

    async def resolve_effective(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> EffectiveExecutionPolicy:
        if (tenant_id, client_id) != (TENANT, CLIENT):
            raise PermissionError("owner unavailable")
        return self.effective


class FakeCatalogResolver:
    def __init__(self, *, approve_synchronous: bool = True) -> None:
        alpha_capabilities = frozenset({
            ProviderCapability.STRUCTURED_OUTPUT,
            ProviderCapability.REASONING,
            ProviderCapability.SYNCHRONOUS,
        })
        alpha = ProviderSpec(
            provider_id="alpha",
            display_name="Alpha Provider",
            default_model_id="alpha-1",
            models=(
                ProviderModelSpec(
                    model_id="alpha-1",
                    adapter=AdapterResolution("alpha.adapter"),
                    offered_capabilities=alpha_capabilities,
                    approved_capabilities=(
                        alpha_capabilities
                        if approve_synchronous
                        else alpha_capabilities - {ProviderCapability.SYNCHRONOUS}
                    ),
                    reasoning_profiles=(ReasoningProfileSpec("standard"),),
                    default_reasoning_profile="standard",
                ),
            ),
        )
        beta = ProviderSpec(
            provider_id="beta",
            display_name="Beta Provider",
            default_model_id="beta-1",
            models=(
                ProviderModelSpec(
                    model_id="beta-1",
                    adapter=AdapterResolution("beta.adapter"),
                    offered_capabilities=frozenset({ProviderCapability.REASONING}),
                    approved_capabilities=frozenset({ProviderCapability.REASONING}),
                    reasoning_profiles=(ReasoningProfileSpec("standard"),),
                    default_reasoning_profile="standard",
                ),
            ),
        )
        version = ProviderCatalogVersion(
            catalog_version_id=uuid7(),
            version_number=1,
            providers=(alpha, beta),
            endpoints=(),
            created_at=NOW,
        )
        self.effective = EffectiveProviderCatalog(
            version=version,
            assignment=ProviderCatalogAssignment(
                catalog_version_id=version.catalog_version_id,
                assignment_version=1,
                assigned_at=NOW,
            ),
        )

    async def resolve_effective(self) -> EffectiveProviderCatalog:
        return self.effective


def _runtime(*, catalog: FakeCatalogResolver | None = None) -> tuple[
    ClientExecutionRuntime,
    FakeSessionStore,
    FakeTaskStore,
    FakeAttemptStore,
    InMemoryClientExecutionArtifacts,
    FakeWorkQueue,
]:
    sessions = FakeSessionStore()
    tasks = FakeTaskStore()
    attempts = FakeAttemptStore()
    artifacts = InMemoryClientExecutionArtifacts()
    queue = FakeWorkQueue()
    runtime = ClientExecutionRuntime(
        sessions=sessions,
        tasks=tasks,
        attempts=attempts,
        policies=FakePolicyResolver(),
        catalog=catalog or FakeCatalogResolver(),
        request_artifacts=artifacts,
        results=artifacts,
        exchanges=artifacts,
        idempotency=InMemoryClientApiIdempotencyJournal(),
        work_queue=queue,
    )
    return runtime, sessions, tasks, attempts, artifacts, queue


async def _request(
    app: Any,
    method: str,
    path: str,
    *,
    token: str = "owner",
    body: dict[str, object] | None = None,
    key: str | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers=headers, json=body)


@pytest.mark.asyncio
async def test_sessions_tasks_catalog_cancel_and_ownership_are_real_runtime() -> None:
    runtime, _sessions, _tasks, _attempts, _artifacts, queue = _runtime()
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=FakeAuthenticator(),
        execution_runtime=runtime,
    )

    created = await _request(
        app,
        "POST",
        "/v1/sessions",
        body={"external_reference": "portal"},
        key="session-1",
    )
    assert created.status_code == 201
    session_id = UUID(created.json()["session_id"])
    assert created.headers["location"] == f"/v1/sessions/{session_id}"

    replay = await _request(
        app,
        "POST",
        "/v1/sessions",
        body={"external_reference": "portal"},
        key="session-1",
    )
    assert replay.status_code == 201
    assert replay.json()["session_id"] == str(session_id)

    forbidden_owner = await _request(
        app,
        "GET",
        f"/v1/sessions/{session_id}",
        token="other",
    )
    assert forbidden_owner.status_code == 404

    providers = await _request(app, "GET", "/v1/providers")
    assert providers.status_code == 200
    assert [item["provider_id"] for item in providers.json()] == ["alpha"]
    assert "cost" not in providers.text.lower()
    assert "currency" not in providers.text.lower()

    models = await _request(app, "GET", "/v1/models?provider_id=alpha")
    assert models.status_code == 200
    assert models.json()[0]["model_id"] == "alpha-1"
    assert models.json()[0]["reasoning_profiles"] == ["standard"]

    auto = await _request(
        app,
        "POST",
        f"/v1/sessions/{session_id}/tasks",
        body={"operation": "TASK_EXECUTION", "input": {"message": "hello"}},
        key="task-auto",
    )
    assert auto.status_code == 202
    assert auto.json()["requested_execution_mode"] == "AUTO"
    assert auto.json()["requested_target"] is None
    assert auto.json()["effective_target"] is None
    assert auto.json()["status"] == "QUEUED"
    assert len(queue.items) == 1
    auto_task_id = UUID(auto.json()["task_id"])

    auto_replay = await _request(
        app,
        "POST",
        f"/v1/sessions/{session_id}/tasks",
        body={"operation": "TASK_EXECUTION", "input": {"message": "hello"}},
        key="task-auto",
    )
    assert auto_replay.status_code == 202
    assert auto_replay.json()["task_id"] == str(auto_task_id)
    assert len(queue.items) == 1

    explicit = await _request(
        app,
        "POST",
        f"/v1/sessions/{session_id}/tasks",
        body={
            "operation": "TASK_EXECUTION",
            "input": {"message": "explicit"},
            "execution": {
                "mode": "EXPLICIT_TARGET",
                "target": {
                    "provider": "alpha",
                    "model": "alpha-1",
                    "reasoning_profile": "standard",
                },
            },
        },
        key="task-explicit",
    )
    assert explicit.status_code == 202
    assert explicit.json()["requested_execution_mode"] == "EXPLICIT_TARGET"
    assert explicit.json()["effective_target"]["provider_id"] == "alpha"

    denied = await _request(
        app,
        "POST",
        f"/v1/sessions/{session_id}/tasks",
        body={
            "operation": "TASK_EXECUTION",
            "input": {"message": "forbidden"},
            "execution": {
                "mode": "EXPLICIT_TARGET",
                "target": {
                    "provider": "beta",
                    "model": "beta-1",
                    "reasoning_profile": "standard",
                },
            },
        },
        key="task-forbidden",
    )
    assert denied.status_code == 403

    cancelled = await _request(
        app,
        "POST",
        f"/v1/tasks/{auto_task_id}/cancel",
        key="cancel-auto",
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_result_attempt_and_exchange_views_are_client_safe() -> None:
    runtime, _sessions, tasks, attempts, artifacts, _queue = _runtime()
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=FakeAuthenticator(),
        execution_runtime=runtime,
    )
    session = await _request(app, "POST", "/v1/sessions", body={}, key="session-result")
    session_id = UUID(session.json()["session_id"])
    task_response = await _request(
        app,
        "POST",
        f"/v1/sessions/{session_id}/tasks",
        body={"operation": "TASK_EXECUTION", "input": {"message": "safe"}},
        key="task-result",
    )
    task_id = UUID(task_response.json()["task_id"])
    result_reference = "artifact://safe-result/1"
    completed = tasks.complete(task_id, result_reference=result_reference)
    artifacts.seed_result(
        scope=completed.ownership,
        result_reference=result_reference,
        value={"answer": "safe output"},
    )

    attempt = ProviderAttempt(
        attempt_id=uuid7(),
        task_id=task_id,
        session_id=session_id,
        ownership=completed.ownership,
        operation="TASK_EXECUTION",
        target=ExecutionTargetSnapshot("alpha", "alpha-1", "standard"),
        cycle=1,
        attempt_index=1,
        request_reference=completed.payloads.input_reference,
        request_fingerprint=completed.payloads.input_fingerprint,
        status=ProviderAttemptStatus.COMPLETED,
        provider_outcome="SUCCESS",
        accepted_requirements=completed.requirements,
        missing_requirements=(),
        response_reference=result_reference,
        created_at=NOW,
        updated_at=NOW,
        terminal_at=NOW,
    )
    attempts.items[attempt.attempt_id] = attempt
    raw = '{"answer":"safe output"}'
    evidence = SanitizedEvidenceRecord(
        media_type="application/json",
        sanitized_raw_body=raw,
        sanitized_sha256=__import__("hashlib").sha256(raw.encode()).hexdigest(),
    )
    artifacts.seed_exchanges(
        scope=completed.ownership,
        attempt_id=attempt.attempt_id,
        items=(
            ClientExchangeEvidence(
                exchange_id=uuid7(),
                attempt_id=attempt.attempt_id,
                provider_id="alpha",
                provider_name="Alpha Provider",
                operation="TASK_EXECUTION",
                status="SUCCEEDED",
                status_label="Succeeded",
                request_evidence=evidence,
                response_evidence=evidence,
            ),
        ),
    )

    result = await _request(app, "GET", f"/v1/tasks/{task_id}/result")
    assert result.status_code == 200
    assert result.json()["result"] == {"answer": "safe output"}
    assert result.json()["attempt_ids"] == [str(attempt.attempt_id)]

    attempt_page = await _request(app, "GET", f"/v1/tasks/{task_id}/attempts")
    assert attempt_page.status_code == 200
    assert attempt_page.json()["items"][0]["provider"] == {
        "provider_id": "alpha",
        "provider_name": "Alpha Provider",
    }

    exchanges = await _request(
        app,
        "GET",
        f"/v1/tasks/{task_id}/attempts/{attempt.attempt_id}/exchanges",
    )
    assert exchanges.status_code == 200
    payload = exchanges.json()["items"][0]
    assert payload["request_evidence"]["sanitized_raw_body"] == raw
    forbidden = (
        "provider_cost",
        "provider_account",
        "provider_credential",
        "currency",
        "pricing",
        "credit_balance",
        "secret_reference",
    )
    assert not any(item in exchanges.text.lower() for item in forbidden)

    cross_owner = await _request(
        app,
        "GET",
        f"/v1/tasks/{task_id}/attempts/{attempt.attempt_id}/exchanges",
        token="other",
    )
    assert cross_owner.status_code == 404


@pytest.mark.asyncio
async def test_attempt_cursor_is_bound_to_task_query() -> None:
    runtime, _sessions, tasks, attempts, _artifacts, _queue = _runtime()
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=FakeAuthenticator(),
        execution_runtime=runtime,
    )
    session = await _request(
        app, "POST", "/v1/sessions", body={}, key="session-cursor"
    )
    session_id = UUID(session.json()["session_id"])
    first_task = await _request(
        app,
        "POST",
        f"/v1/sessions/{session_id}/tasks",
        body={"operation": "TASK_EXECUTION", "input": {"message": "first"}},
        key="task-cursor-1",
    )
    second_task = await _request(
        app,
        "POST",
        f"/v1/sessions/{session_id}/tasks",
        body={"operation": "TASK_EXECUTION", "input": {"message": "second"}},
        key="task-cursor-2",
    )
    first_id = UUID(first_task.json()["task_id"])
    second_id = UUID(second_task.json()["task_id"])
    first_domain = tasks.items[first_id]

    for index in (1, 2):
        attempt = ProviderAttempt(
            attempt_id=uuid7(),
            task_id=first_id,
            session_id=session_id,
            ownership=first_domain.ownership,
            operation="TASK_EXECUTION",
            target=ExecutionTargetSnapshot("alpha", "alpha-1", "standard"),
            cycle=1,
            attempt_index=index,
            request_reference=first_domain.payloads.input_reference,
            request_fingerprint=first_domain.payloads.input_fingerprint,
            status=ProviderAttemptStatus.COMPLETED,
            provider_outcome="SUCCESS",
            accepted_requirements=(),
            missing_requirements=(),
            created_at=NOW,
            updated_at=NOW,
            terminal_at=NOW,
        )
        attempts.items[attempt.attempt_id] = attempt

    page = await _request(
        app,
        "GET",
        f"/v1/tasks/{first_id}/attempts?limit=1",
    )
    assert page.status_code == 200
    cursor = page.json()["next_cursor"]
    assert cursor is not None

    misuse = await _request(
        app,
        "GET",
        f"/v1/tasks/{second_id}/attempts?limit=1&cursor={cursor}",
    )
    assert misuse.status_code == 400
    assert misuse.json()["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_task_submission_rejects_when_execution_queue_is_saturated() -> None:
    runtime, _sessions, _tasks, _attempts, _artifacts, queue = _runtime()
    queue.reported_depth = 10_000
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=FakeAuthenticator(),
        execution_runtime=runtime,
    )
    session = await _request(
        app,
        "POST",
        "/v1/sessions",
        body={},
        key="session-backpressure",
    )
    assert session.status_code == 201
    session_id = UUID(session.json()["session_id"])

    rejected = await _request(
        app,
        "POST",
        f"/v1/sessions/{session_id}/tasks",
        body={"operation": "TASK_EXECUTION", "input": {"message": "blocked"}},
        key="task-backpressure",
    )
    assert rejected.status_code == 429
    assert rejected.json()["code"] == "QUEUE_BACKPRESSURE"
    assert rejected.headers["retry-after"] == "5"
    assert queue.items == {}


@pytest.mark.asyncio
async def test_explicit_target_rejects_unapproved_synchronous_capability() -> None:
    """Even a tenant-authorized target must be approved for the synchronous runtime."""
    runtime, _sessions, _tasks, _attempts, _artifacts, queue = _runtime(
        catalog=FakeCatalogResolver(approve_synchronous=False)
    )
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=FakeAuthenticator(),
        execution_runtime=runtime,
    )
    created = await _request(
        app, "POST", "/v1/sessions", body={}, key="session-capability"
    )
    assert created.status_code == 201
    response = await _request(
        app,
        "POST",
        f"/v1/sessions/{created.json()['session_id']}/tasks",
        body={
            "operation": "TASK_EXECUTION",
            "input": {"message": "capability bound"},
            "execution": {
                "mode": "EXPLICIT_TARGET",
                "target": {
                    "provider": "alpha",
                    "model": "alpha-1",
                    "reasoning_profile": "standard",
                },
            },
        },
        key="task-capability-reject",
    )
    assert response.status_code == 403
    assert queue.items == []
