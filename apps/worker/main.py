"""Worker process composition root."""

from __future__ import annotations

import asyncio
from pathlib import Path

from orqetia.control_plane import ProviderSecretStore
from orqetia.infrastructure.availability import (
    MaintenanceMode,
    OperationalAvailabilityController,
)
from orqetia.infrastructure.local_secrets import LocalFileProviderSecretStore
from orqetia.infrastructure.messaging import PostgresWorkQueue
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.infrastructure.processes import (
    WorkerProcess,
    build_execution_handler_registry,
    install_signal_handlers,
)
from orqetia.settings import (
    Environment,
    ProcessRole,
    RuntimeSettings,
    SecretStoreMode,
)
from orqetia.shared.messaging import QueueName

_LOCAL_SECRET_ROOT = Path("/var/lib/orqetia/provider-secrets")


async def amain() -> None:
    settings = RuntimeSettings()
    if settings.process_role is not ProcessRole.WORKER:
        raise RuntimeError(
            "worker entry point requires ORQETIA_PROCESS_ROLE=worker"
        )
    if settings.environment is Environment.PRODUCTION:
        raise RuntimeError(
            "production worker requires an injected managed/envelope secret store"
        )

    allow_test_provider = settings.environment in {
        Environment.LOCAL,
        Environment.TEST,
    }
    secret_store: ProviderSecretStore | None = None
    if settings.secret_store_mode is SecretStoreMode.LOCAL:
        if not allow_test_provider:
            raise RuntimeError(
                "local provider secret store is restricted to LOCAL/TEST"
            )
        secret_store = LocalFileProviderSecretStore(
            _LOCAL_SECRET_ROOT,
            environment=settings.environment.value,
        )

    engine = create_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        queue = PostgresWorkQueue(session_factory)
        registry = build_execution_handler_registry(
            session_factory=session_factory,
            queue=queue,
            secret_store=secret_store,
            allow_test_provider=allow_test_provider,
        )
        process = WorkerProcess(
            queue=queue,
            registry=registry,
            queue_name=QueueName.EXECUTION,
            concurrency=settings.worker_concurrency,
            lease_seconds=settings.work_lease_seconds,
            poll_interval_seconds=settings.queue_poll_interval_seconds,
            shutdown_grace_seconds=settings.shutdown_grace_seconds,
            availability_controller=OperationalAvailabilityController(
                process_role="worker",
                maintenance_mode=MaintenanceMode(settings.maintenance_mode),
                retry_after_seconds=settings.maintenance_retry_after_seconds,
            ),
        )
        install_signal_handlers(process)
        await process.run()
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
