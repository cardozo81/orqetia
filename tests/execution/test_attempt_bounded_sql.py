"""Directed SQL construction regression for client attempt read amplification (#169)."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from orqetia.execution import OwnershipScope
from orqetia.execution.attempt_postgres import PostgresProviderAttemptStore


class _QueryResult:
    def mappings(self) -> "_QueryResult":
        return self

    def all(self) -> list[object]:
        return []


class _RecordingSession:
    def __init__(self) -> None:
        self.statement = None

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, statement: object) -> _QueryResult:
        self.statement = statement
        return _QueryResult()


@pytest.mark.asyncio
async def test_client_attempt_page_uses_sql_limit_and_owner_scoping() -> None:
    session = _RecordingSession()
    store = PostgresProviderAttemptStore(lambda: session)
    scope = OwnershipScope(
        tenant_id=UUID("0199b39a-9bf1-7000-8000-000000000101"),
        client_id=UUID("0199b39a-9bf1-7000-8000-000000000102"),
    )
    task_id = UUID("0199b39a-9bf1-7000-8000-000000000103")
    assert await store.page_for_task(
        scope=scope, task_id=task_id, offset=20, limit=101,
    ) == ()
    compiled = str(session.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))
    assert "LIMIT 101" in compiled
    assert "OFFSET 20" in compiled
    assert "tenant_id" in compiled and "client_id" in compiled and "task_id" in compiled
    assert str(scope.tenant_id) in compiled
    assert str(scope.client_id) in compiled
    assert str(task_id) in compiled


@pytest.mark.asyncio
async def test_client_attempt_page_rejects_bad_internal_limits_before_sql() -> None:
    session = _RecordingSession()
    store = PostgresProviderAttemptStore(lambda: session)
    scope = OwnershipScope(
        tenant_id=UUID("0199b39a-9bf1-7000-8000-000000000101"),
        client_id=UUID("0199b39a-9bf1-7000-8000-000000000102"),
    )
    task_id = UUID("0199b39a-9bf1-7000-8000-000000000103")
    for offset, limit in ((-1, 1), (0, 0), (0, 102)):
        with pytest.raises(ValueError, match="bounds"):
            await store.page_for_task(
                scope=scope, task_id=task_id, offset=offset, limit=limit,
            )
    assert session.statement is None
