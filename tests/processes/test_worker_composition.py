from __future__ import annotations

from orqetia.infrastructure.messaging import PostgresWorkQueue
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.infrastructure.processes import build_execution_handler_registry
from orqetia.settings import RuntimeSettings


def test_execution_worker_registry_is_not_empty() -> None:
    settings = RuntimeSettings()
    engine = create_engine(settings)
    try:
        factory = create_session_factory(engine)
        registry = build_execution_handler_registry(
            session_factory=factory,
            queue=PostgresWorkQueue(factory),
        )
        assert registry.has_handlers
    finally:
        import asyncio

        asyncio.run(engine.dispose())
