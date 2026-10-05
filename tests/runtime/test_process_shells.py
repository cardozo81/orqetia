from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from uuid import uuid7

from orqetia.infrastructure.processes import (
    HandlerOutcome,
    HandlerRegistry,
    SchedulerProcess,
    WorkerProcess,
)
from orqetia.shared.messaging import (
    DataClassification,
    QueueName,
    WorkLease,
)


class FakeQueue:
    def __init__(
        self,
        leases: tuple[WorkLease, ...] = (),
        *,
        renew_result: bool = True,
    ) -> None:
        self.leases = list(leases)
        self.renew_result = renew_result
        self.claim_count = 0
        self.renew_count = 0
        self.completed: list[WorkLease] = []
        self.requeued: list[WorkLease] = []

    async def enqueue(self, item: object) -> None:
        raise AssertionError("process shell must not enqueue synthetic durable work")

    async def claim(
        self,
        *,
        queue_name: QueueName,
        lease_owner: str,
        lease_seconds: int,
        limit: int,
    ) -> tuple[WorkLease, ...]:
        del lease_seconds
        self.claim_count += 1
        selected = [lease for lease in self.leases if lease.queue_name is queue_name][:limit]
        self.leases = [lease for lease in self.leases if lease not in selected]
        return tuple(
            WorkLease(
                **{
                    **lease.__dict__,
                    "lease_owner": lease_owner,
                }
            )
            for lease in selected
        )

    async def renew_lease(self, lease: WorkLease, *, lease_seconds: int) -> bool:
        del lease, lease_seconds
        self.renew_count += 1
        return self.renew_result

    async def complete(self, lease: WorkLease) -> bool:
        self.completed.append(lease)
        return True

    async def requeue_infrastructure_failure(
        self,
        lease: WorkLease,
        *,
        available_at: datetime,
        error_class: str,
    ) -> bool:
        del available_at, error_class
        self.requeued.append(lease)
        return True


class FakeWakeup:
    def __init__(self) -> None:
        self.notifications: list[QueueName] = []

    async def notify(self, queue_name: QueueName) -> None:
        self.notifications.append(queue_name)


def lease(operation_type: str = "noop") -> WorkLease:
    return WorkLease(
        work_id=uuid7(),
        queue_name=QueueName.EXECUTION,
        operation_type=operation_type,
        operation_version=1,
        payload={"safe": "metadata"},
        data_classification=DataClassification.INTERNAL,
        tenant_id=None,
        client_id=None,
        resource_type=None,
        resource_id=None,
        correlation_id=uuid7(),
        causation_id=None,
        trace_id=None,
        logical_operation_id=None,
        lease_owner="unclaimed",
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
        attempt_count=1,
    )


class ProcessShellTests(unittest.TestCase):
    def test_process_starts_and_stops_with_empty_registry_without_claiming(self) -> None:
        asyncio.run(self._test_start_stop())

    def test_registered_noop_handler_completes_one_work_item(self) -> None:
        asyncio.run(self._test_noop_completion())

    def test_heartbeat_renews_long_running_handler(self) -> None:
        asyncio.run(self._test_heartbeat())

    def test_lease_loss_prevents_completion(self) -> None:
        asyncio.run(self._test_lease_loss())

    def test_unclassified_handler_exception_does_not_auto_retry(self) -> None:
        asyncio.run(self._test_handler_exception())

    def test_scheduler_duplicate_wakeup_is_only_a_hint(self) -> None:
        asyncio.run(self._test_scheduler_wakeup())

    def test_process_emits_safe_operational_heartbeat(self) -> None:
        asyncio.run(self._test_operational_heartbeat())

    async def _test_start_stop(self) -> None:
        queue = FakeQueue()
        process = self.process(queue, HandlerRegistry())
        task = asyncio.create_task(process.run())
        await asyncio.sleep(0.03)
        process.request_stop()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(queue.claim_count, 0)

    async def _test_noop_completion(self) -> None:
        item = lease()
        queue = FakeQueue((item,))
        registry = HandlerRegistry()

        async def noop(_lease: WorkLease) -> HandlerOutcome:
            return HandlerOutcome.complete()

        registry.register("noop", 1, noop)
        process = self.process(queue, registry)
        handled = await process.run_once()

        self.assertEqual(handled, 1)
        self.assertEqual(len(queue.completed), 1)
        self.assertEqual(queue.requeued, [])

    async def _test_heartbeat(self) -> None:
        item = lease()
        queue = FakeQueue((item,))
        registry = HandlerRegistry()

        async def slow(_lease: WorkLease) -> HandlerOutcome:
            await asyncio.sleep(0.4)
            return HandlerOutcome.complete()

        registry.register("noop", 1, slow)
        process = self.process(queue, registry, lease_seconds=1)
        await process.run_once()

        self.assertGreaterEqual(queue.renew_count, 1)
        self.assertEqual(len(queue.completed), 1)

    async def _test_lease_loss(self) -> None:
        item = lease()
        queue = FakeQueue((item,), renew_result=False)
        registry = HandlerRegistry()

        async def slow(_lease: WorkLease) -> HandlerOutcome:
            await asyncio.sleep(0.4)
            return HandlerOutcome.complete()

        registry.register("noop", 1, slow)
        process = self.process(queue, registry, lease_seconds=1)
        await process.run_once()

        self.assertGreaterEqual(queue.renew_count, 1)
        self.assertEqual(queue.completed, [])
        self.assertEqual(queue.requeued, [])

    async def _test_handler_exception(self) -> None:
        item = lease()
        queue = FakeQueue((item,))
        registry = HandlerRegistry()

        async def exploding(_lease: WorkLease) -> HandlerOutcome:
            raise RuntimeError("synthetic failure")

        registry.register("noop", 1, exploding)
        process = self.process(queue, registry)
        await process.run_once()

        self.assertEqual(queue.completed, [])
        self.assertEqual(queue.requeued, [])

    async def _test_operational_heartbeat(self) -> None:
        queue = FakeQueue()
        process = WorkerProcess(
            queue=queue,
            registry=HandlerRegistry(),
            queue_name=QueueName.EXECUTION,
            concurrency=1,
            lease_seconds=30,
            poll_interval_seconds=0.01,
            shutdown_grace_seconds=1,
            process_id="heartbeat-test",
            heartbeat_interval_seconds=0.01,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            task = asyncio.create_task(process.run())
            await asyncio.sleep(0.04)
            process.request_stop()
            await asyncio.wait_for(task, timeout=1)

        rendered = output.getvalue()
        self.assertIn('"event": "process.heartbeat"', rendered)
        self.assertNotIn('"payload"', rendered)
        self.assertNotIn("safe\": \"metadata", rendered)

    async def _test_scheduler_wakeup(self) -> None:
        queue = FakeQueue()
        worker = self.process(queue, HandlerRegistry(), queue_name=QueueName.SCHEDULER)
        wakeup = FakeWakeup()
        scheduler = SchedulerProcess(worker, wakeup)

        await scheduler.wake_due_scan()
        await scheduler.wake_due_scan()

        self.assertEqual(
            wakeup.notifications,
            [QueueName.SCHEDULER, QueueName.SCHEDULER],
        )
        self.assertEqual(queue.claim_count, 0)

    @staticmethod
    def process(
        queue: FakeQueue,
        registry: HandlerRegistry,
        *,
        queue_name: QueueName = QueueName.EXECUTION,
        lease_seconds: int = 30,
    ) -> WorkerProcess:
        return WorkerProcess(
            queue=queue,
            registry=registry,
            queue_name=queue_name,
            concurrency=2,
            lease_seconds=lease_seconds,
            poll_interval_seconds=0.01,
            shutdown_grace_seconds=1,
            process_id="test-process",
        )


if __name__ == "__main__":
    unittest.main()
