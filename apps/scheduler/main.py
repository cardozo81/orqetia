"""Scheduler/event-publisher process composition root."""

from __future__ import annotations

import asyncio

from orqetia.infrastructure.availability import (
    MaintenanceMode,
    OperationalAvailabilityController,
)
from orqetia.infrastructure.messaging import PostgresWakeup, PostgresWorkQueue
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.infrastructure.processes import (
    HandlerRegistry,
    SchedulerProcess,
    WorkerProcess,
    install_signal_handlers,
)
from orqetia.settings import ProcessRole, RuntimeSettings
from orqetia.shared.messaging import QueueName


async def amain() -> None:
    settings = RuntimeSettings()
    if settings.process_role is not ProcessRole.SCHEDULER:
        raise RuntimeError("scheduler entry point requires ORQETIA_PROCESS_ROLE=scheduler")

    engine = create_engine(settings)
    try:
        worker = WorkerProcess(
            queue=PostgresWorkQueue(create_session_factory(engine)),
            registry=HandlerRegistry(),
            queue_name=QueueName.SCHEDULER,
            concurrency=1,
            lease_seconds=settings.work_lease_seconds,
            poll_interval_seconds=settings.queue_poll_interval_seconds,
            shutdown_grace_seconds=settings.shutdown_grace_seconds,
            availability_controller=OperationalAvailabilityController(
                process_role="scheduler",
                maintenance_mode=MaintenanceMode(settings.maintenance_mode),
                retry_after_seconds=settings.maintenance_retry_after_seconds,
            ),
        )
        process = SchedulerProcess(worker, PostgresWakeup(engine))
        install_signal_handlers(process)
        await process.run()
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
