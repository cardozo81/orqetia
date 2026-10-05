"""Broker-neutral worker and scheduler process loops."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid7

from orqetia.shared.messaging import QueueName, WakeupPort, WorkLease, WorkQueuePort


class HandlerDisposition(StrEnum):
    COMPLETE = "COMPLETE"
    REQUEUE_INFRASTRUCTURE = "REQUEUE_INFRASTRUCTURE"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True)
class HandlerOutcome:
    disposition: HandlerDisposition
    available_at: datetime | None = None
    error_class: str | None = None

    @classmethod
    def complete(cls) -> HandlerOutcome:
        return cls(HandlerDisposition.COMPLETE)

    @classmethod
    def requeue_infrastructure(
        cls,
        *,
        available_at: datetime,
        error_class: str,
    ) -> HandlerOutcome:
        if not error_class.strip():
            raise ValueError("error_class is required for infrastructure requeue")
        return cls(
            HandlerDisposition.REQUEUE_INFRASTRUCTURE,
            available_at=available_at,
            error_class=error_class,
        )

    @classmethod
    def dead_letter(cls, *, error_class: str) -> HandlerOutcome:
        if not error_class.strip():
            raise ValueError("error_class is required for dead-letter")
        return cls(
            HandlerDisposition.DEAD_LETTER,
            error_class=error_class,
        )


WorkHandler = Callable[[WorkLease], Awaitable[HandlerOutcome]]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], WorkHandler] = {}

    def register(
        self,
        operation_type: str,
        operation_version: int,
        handler: WorkHandler,
    ) -> None:
        key = (operation_type.strip(), operation_version)
        if not key[0] or operation_version < 1:
            raise ValueError("handler operation type/version must be explicit")
        if key in self._handlers:
            raise ValueError(f"handler already registered for {key[0]} v{key[1]}")
        self._handlers[key] = handler

    def resolve(self, lease: WorkLease) -> WorkHandler | None:
        return self._handlers.get((lease.operation_type, lease.operation_version))

    @property
    def has_handlers(self) -> bool:
        return bool(self._handlers)


class WorkerProcess:
    """Claims durable work and delegates all semantic outcomes to handlers.

    This shell never infers provider/business retry from exceptions. A handler
    must explicitly return REQUEUE_INFRASTRUCTURE for infrastructure-only
    retry. Unclassified exceptions leave the lease to expire/recover through
    the durable queue and the handler's idempotency contract.
    """

    def __init__(
        self,
        *,
        queue: WorkQueuePort,
        registry: HandlerRegistry,
        queue_name: QueueName,
        concurrency: int,
        lease_seconds: int,
        poll_interval_seconds: float,
        shutdown_grace_seconds: int,
        process_id: str | None = None,
        heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be >= 1")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if shutdown_grace_seconds < 1:
            raise ValueError("shutdown_grace_seconds must be >= 1")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be > 0")

        self._queue = queue
        self._registry = registry
        self._queue_name = queue_name
        self._concurrency = concurrency
        self._lease_seconds = lease_seconds
        self._poll_interval = poll_interval_seconds
        self._shutdown_grace = shutdown_grace_seconds
        self._process_id = process_id or default_process_id(queue_name.value)
        self._heartbeat_interval = heartbeat_interval_seconds
        self._last_heartbeat = 0.0
        self._stop = asyncio.Event()
        self._inflight: set[asyncio.Task[None]] = set()

    @property
    def process_id(self) -> str:
        return self._process_id

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._log("process.started")
        try:
            while not self._stop.is_set():
                self._emit_heartbeat_if_due()
                handled = await self.run_once()
                if handled == 0:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self._poll_interval,
                        )
        finally:
            await self._drain_inflight()
            self._log("process.stopped")

    async def run_once(self) -> int:
        # Phase-1 shells deliberately do not claim unknown work before domain
        # handlers are registered by later implementation issues.
        if not self._registry.has_handlers:
            return 0

        leases = await self._queue.claim(
            queue_name=self._queue_name,
            lease_owner=self._process_id,
            lease_seconds=self._lease_seconds,
            limit=self._concurrency,
        )
        if not leases:
            return 0

        tasks = {asyncio.create_task(self._handle(lease)) for lease in leases}
        self._inflight.update(tasks)
        try:
            await asyncio.gather(*tasks)
        finally:
            self._inflight.difference_update(tasks)
        return len(leases)

    def _emit_heartbeat_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat < self._heartbeat_interval:
            return
        self._last_heartbeat = now
        self._log("process.heartbeat")

    async def _handle(self, lease: WorkLease) -> None:
        handler = self._registry.resolve(lease)
        if handler is None:
            if not await self._queue.dead_letter(
                lease,
                error_class="UNSUPPORTED_OPERATION",
            ):
                self._log("work.stale_dead_letter_rejected", lease)
            else:
                self._log("work.unsupported_dead_lettered", lease)
            return

        handler_done = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(
                lease,
                handler_done=handler_done,
                lease_lost=lease_lost,
            )
        )

        try:
            outcome = await handler(lease)
            if lease_lost.is_set():
                self._log("work.lease_lost", lease)
                return

            if outcome.disposition is HandlerDisposition.COMPLETE:
                if not await self._queue.complete(lease):
                    self._log("work.stale_completion_rejected", lease)
                return

            if outcome.disposition is HandlerDisposition.REQUEUE_INFRASTRUCTURE:
                if outcome.available_at is None or outcome.error_class is None:
                    raise ValueError("infrastructure requeue outcome is incomplete")
                if not await self._queue.requeue_infrastructure_failure(
                    lease,
                    available_at=outcome.available_at,
                    error_class=outcome.error_class,
                ):
                    self._log("work.stale_requeue_rejected", lease)
                return

            if outcome.disposition is HandlerDisposition.DEAD_LETTER:
                if outcome.error_class is None:
                    raise ValueError("dead-letter outcome is incomplete")
                if not await self._queue.dead_letter(
                    lease,
                    error_class=outcome.error_class,
                ):
                    self._log("work.stale_dead_letter_rejected", lease)
                return

            raise ValueError(f"unsupported handler disposition: {outcome.disposition}")
        except Exception as exc:
            # Do not infer a provider/business retry from an arbitrary exception.
            self._log(
                "work.handler_exception",
                lease,
                error_class=type(exc).__name__,
            )
        finally:
            handler_done.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(
        self,
        lease: WorkLease,
        *,
        handler_done: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        interval = max(0.25, self._lease_seconds / 3)
        while not handler_done.is_set():
            try:
                await asyncio.wait_for(handler_done.wait(), timeout=interval)
                return
            except TimeoutError:
                pass

            renewed = await self._queue.renew_lease(
                lease,
                lease_seconds=self._lease_seconds,
            )
            if not renewed:
                lease_lost.set()
                return

    async def _drain_inflight(self) -> None:
        if not self._inflight:
            return
        done, pending = await asyncio.wait(
            self._inflight,
            timeout=self._shutdown_grace,
        )
        del done
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _log(
        self,
        event: str,
        lease: WorkLease | None = None,
        *,
        error_class: str | None = None,
    ) -> None:
        record: dict[str, object] = {
            "event": event,
            "process_id": self._process_id,
            "queue_name": self._queue_name.value,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        if lease is not None:
            record.update(
                {
                    "work_id": str(lease.work_id),
                    "operation_type": lease.operation_type,
                    "operation_version": lease.operation_version,
                    "correlation_id": (
                        None if lease.correlation_id is None else str(lease.correlation_id)
                    ),
                }
            )
        if error_class is not None:
            record["error_class"] = error_class
        print(json.dumps(record, sort_keys=True), flush=True)


class SchedulerProcess:
    """Phase-1 scheduler shell.

    Durable timing state is owned elsewhere. This shell processes only work
    already materialized in the scheduler queue and may emit a generic wakeup
    hint. Duplicate wakeups do not create durable work.
    """

    def __init__(self, worker: WorkerProcess, wakeup: WakeupPort) -> None:
        self._worker = worker
        self._wakeup = wakeup

    async def run(self) -> None:
        await self._worker.run()

    def request_stop(self) -> None:
        self._worker.request_stop()

    async def wake_due_scan(self) -> None:
        await self._wakeup.notify(QueueName.SCHEDULER)


def default_process_id(role: str) -> str:
    return f"{role}:{socket.gethostname()}:{os.getpid()}:{uuid7()}"


def install_signal_handlers(process: WorkerProcess | SchedulerProcess) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, process.request_stop)
        except NotImplementedError:
            signal.signal(signum, lambda _signum, _frame: process.request_stop())
