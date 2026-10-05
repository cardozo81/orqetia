#!/usr/bin/env python3
"""Development-only long-running process harness for Docker Compose.

Real API/worker/scheduler behavior is implemented by #103/#105. This harness
only proves container lifecycle, typed configuration and graceful termination.
"""

from __future__ import annotations

import argparse
import json
import signal
import threading

from orqetia.settings import ProcessRole, RuntimeSettings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=[role.value for role in ProcessRole])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = RuntimeSettings()

    if settings.process_role.value != args.role:
        raise SystemExit(
            f"configured process role {settings.process_role.value!r} "
            f"does not match command role {args.role!r}"
        )

    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    print(
        json.dumps(
            {
                "event": "development_process.started",
                **settings.safe_summary(),
            },
            sort_keys=True,
            default=str,
        ),
        flush=True,
    )

    while not stop.wait(timeout=1.0):
        pass

    print(
        json.dumps(
            {
                "event": "development_process.stopped",
                "process_role": settings.process_role.value,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
