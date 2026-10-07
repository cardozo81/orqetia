"""Container probe for worker/scheduler process presence and database readiness."""

from __future__ import annotations

import asyncio
from pathlib import Path

from orqetia.infrastructure.health import DatabaseReadinessProbe
from orqetia.infrastructure.persistence import create_engine
from orqetia.settings import ProcessRole, RuntimeSettings


async def main() -> None:
    settings = RuntimeSettings()
    if settings.process_role not in {ProcessRole.WORKER, ProcessRole.SCHEDULER}:
        raise RuntimeError("probe supports worker/scheduler only")
    entrypoint = f"apps.{settings.process_role.value}.main".encode()
    if not any(
        entrypoint in path.read_bytes().split(b"\0")
        for path in Path("/proc").glob("[0-9]*/cmdline")
    ):
        raise RuntimeError("runtime process is absent")
    engine = create_engine(settings)
    try:
        await DatabaseReadinessProbe(engine).check()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
