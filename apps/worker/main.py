"""Worker process composition root."""

from __future__ import annotations

import asyncio

from orqetia.infrastructure.messaging import PostgresWorkQueue
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.infrastructure.processes import HandlerRegistry, WorkerProcess, install_signal_handlers
from orqetia.settings import ProcessRole, RuntimeSettings
from orqetia.shared.messaging import QueueName


async def amain() -> None:
    settings = RuntimeSettings()
    if settings.process_role is not ProcessRole.WORKER:
        raise RuntimeError("worker entry point requires ORQETIA_PROCESS_ROLE=worker")

    engine = create_engine(settings)
    try:
        queue = PostgresWorkQueue(create_session_factory(engine))
        process = WorkerProcess(
            queue=queue,
            registry=HandlerRegistry(),
            queue_name=QueueName.EXECUTION,
            concurrency=settings.worker_concurrency,
            lease_seconds=settings.work_lease_seconds,
            poll_interval_seconds=settings.queue_poll_interval_seconds,
            shutdown_grace_seconds=settings.shutdown_grace_seconds,
        )
        install_signal_handlers(process)
        await process.run()
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
