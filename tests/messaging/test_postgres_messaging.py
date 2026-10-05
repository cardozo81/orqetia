from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import sqlalchemy as sa

from orqetia.infrastructure.messaging import (
    PostgresEventTransport,
    PostgresWorkQueue,
    event_deliveries,
    work_items,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings
from orqetia.shared.messaging import (
    DataClassification,
    EventEnvelope,
    QueueName,
    WorkItem,
)


class PostgreSQLMessagingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ORQETIA_DATABASE_DSN" not in os.environ:
            raise unittest.SkipTest("PostgreSQL integration DSN is not configured")

    def setUp(self) -> None:
        asyncio.run(self._truncate())

    def test_two_workers_never_own_same_active_lease(self) -> None:
        asyncio.run(self._test_two_workers_never_own_same_active_lease())

    def test_future_work_is_not_claimed_early_and_poll_finds_due_work(self) -> None:
        asyncio.run(self._test_delayed_and_poll_fallback())

    def test_expired_lease_is_reclaimed_and_stale_worker_cannot_complete(self) -> None:
        asyncio.run(self._test_reclaim_and_stale_completion())

    def test_event_delivery_registration_is_deduplicated_per_consumer(self) -> None:
        asyncio.run(self._test_event_dedup())

    def test_active_lease_can_be_renewed(self) -> None:
        asyncio.run(self._test_active_lease_renewal())

    def test_expired_exhausted_lease_becomes_dead_instead_of_reclaiming(self) -> None:
        asyncio.run(self._test_expired_exhausted_lease())

    def test_stale_worker_cannot_dead_letter_after_reclaim(self) -> None:
        asyncio.run(self._test_stale_dead_letter())

    def test_client_private_scope_and_secret_are_defended_by_database(self) -> None:
        asyncio.run(self._test_database_classification_checks())

    async def _resources(self):
        settings = RuntimeSettings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        return engine, factory

    async def _truncate(self) -> None:
        engine, _ = await self._resources()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text("TRUNCATE messaging.event_deliveries, messaging.work_items")
                )
        finally:
            await engine.dispose()

    async def _test_two_workers_never_own_same_active_lease(self) -> None:
        engine, factory = await self._resources()
        queue = PostgresWorkQueue(factory)
        try:
            item = self._item()
            await queue.enqueue(item)
            a, b = await asyncio.gather(
                queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                ),
                queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-b",
                    lease_seconds=30,
                    limit=1,
                ),
            )
            leases = tuple(a) + tuple(b)
            self.assertEqual(len(leases), 1)
            self.assertEqual(leases[0].work_id, item.work_id)
        finally:
            await engine.dispose()

    async def _test_delayed_and_poll_fallback(self) -> None:
        engine, factory = await self._resources()
        queue = PostgresWorkQueue(factory)
        try:
            future = self._item(available_at=datetime.now(UTC) + timedelta(minutes=5))
            await queue.enqueue(future)
            early = await queue.claim(
                queue_name=QueueName.EXECUTION,
                lease_owner="poller",
                lease_seconds=30,
                limit=10,
            )
            self.assertEqual(tuple(early), ())

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == future.work_id)
                    .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
                )

            due = await queue.claim(
                queue_name=QueueName.EXECUTION,
                lease_owner="poller",
                lease_seconds=30,
                limit=10,
            )
            self.assertEqual([lease.work_id for lease in due], [future.work_id])
        finally:
            await engine.dispose()

    async def _test_reclaim_and_stale_completion(self) -> None:
        engine, factory = await self._resources()
        queue = PostgresWorkQueue(factory)
        try:
            item = self._item()
            await queue.enqueue(item)
            first = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == item.work_id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            second = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-b",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]

            self.assertEqual(second.work_id, first.work_id)
            self.assertEqual(second.attempt_count, 2)
            self.assertFalse(await queue.complete(first))
            self.assertTrue(await queue.complete(second))
        finally:
            await engine.dispose()

    async def _test_active_lease_renewal(self) -> None:
        engine, factory = await self._resources()
        queue = PostgresWorkQueue(factory)
        try:
            item = self._item()
            await queue.enqueue(item)
            claimed = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="heartbeat-worker",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]

            self.assertTrue(await queue.renew_lease(claimed, lease_seconds=60))
            self.assertTrue(await queue.complete(claimed))
        finally:
            await engine.dispose()

    async def _test_expired_exhausted_lease(self) -> None:
        engine, factory = await self._resources()
        queue = PostgresWorkQueue(factory)
        try:
            item = self._item(max_infrastructure_attempts=1)
            await queue.enqueue(item)
            first = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]
            self.assertEqual(first.attempt_count, 1)

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == item.work_id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            reclaimed = await queue.claim(
                queue_name=QueueName.EXECUTION,
                lease_owner="worker-b",
                lease_seconds=30,
                limit=1,
            )
            self.assertEqual(tuple(reclaimed), ())

            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        sa.select(
                            work_items.c.state,
                            work_items.c.last_error_class,
                            work_items.c.dead_at,
                        ).where(work_items.c.work_id == item.work_id)
                    )
                ).one()
            self.assertEqual(row.state, "DEAD")
            self.assertEqual(
                row.last_error_class,
                "INFRASTRUCTURE_ATTEMPTS_EXHAUSTED",
            )
            self.assertIsNotNone(row.dead_at)
        finally:
            await engine.dispose()

    async def _test_stale_dead_letter(self) -> None:
        engine, factory = await self._resources()
        queue = PostgresWorkQueue(factory)
        try:
            item = self._item(max_infrastructure_attempts=3)
            await queue.enqueue(item)
            first = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-a",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]

            async with engine.begin() as connection:
                await connection.execute(
                    sa.update(work_items)
                    .where(work_items.c.work_id == item.work_id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            second = (
                await queue.claim(
                    queue_name=QueueName.EXECUTION,
                    lease_owner="worker-b",
                    lease_seconds=30,
                    limit=1,
                )
            )[0]

            self.assertFalse(
                await queue.dead_letter(first, error_class="STALE_WORKER")
            )
            self.assertTrue(
                await queue.dead_letter(second, error_class="POISON_OPERATION")
            )
        finally:
            await engine.dispose()

    async def _test_event_dedup(self) -> None:
        engine, factory = await self._resources()
        transport = PostgresEventTransport(factory)
        try:
            envelope = EventEnvelope(
                event_id=uuid7(),
                event_type="task.completed",
                event_version=1,
                producer="execution",
                occurred_at=datetime.now(UTC),
                aggregate_type="task",
                aggregate_id=uuid7(),
                data_classification=DataClassification.INTERNAL,
                payload={"status": "COMPLETE"},
            )
            first = await transport.register_deliveries(
                envelope,
                consumers=("accounting-v1", "projection-v1"),
            )
            second = await transport.register_deliveries(
                envelope,
                consumers=("accounting-v1", "projection-v1"),
            )

            self.assertEqual(first, 2)
            self.assertEqual(second, 0)

            async with engine.connect() as connection:
                count = (
                    await connection.execute(
                        sa.select(sa.func.count()).select_from(event_deliveries)
                    )
                ).scalar_one()
            self.assertEqual(count, 2)

            claimed = await transport.claim(
                consumer_name="accounting-v1",
                lease_owner="consumer-a",
                lease_seconds=30,
                limit=1,
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].envelope.event_id, envelope.event_id)
            self.assertTrue(await transport.acknowledge(claimed[0]))
        finally:
            await engine.dispose()

    async def _test_database_classification_checks(self) -> None:
        engine, _ = await self._resources()
        try:
            with self.assertRaises(sa.exc.IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(
                        sa.insert(work_items).values(
                            work_id=uuid7(),
                            queue_name="execution",
                            operation_type="execute_task",
                            operation_version=1,
                            data_classification="SECRET",
                            payload={},
                            priority=0,
                            state="READY",
                            available_at=datetime.now(UTC),
                            max_infrastructure_attempts=1,
                        )
                    )

            with self.assertRaises(sa.exc.IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(
                        sa.insert(work_items).values(
                            work_id=uuid7(),
                            queue_name="execution",
                            operation_type="execute_task",
                            operation_version=1,
                            data_classification="CLIENT_PRIVATE",
                            payload={},
                            priority=0,
                            state="READY",
                            available_at=datetime.now(UTC),
                            max_infrastructure_attempts=1,
                        )
                    )
        finally:
            await engine.dispose()

    @staticmethod
    def _item(**overrides: object) -> WorkItem:
        values: dict[str, object] = {
            "work_id": uuid7(),
            "queue_name": QueueName.EXECUTION,
            "operation_type": "execute_task",
            "operation_version": 1,
            "data_classification": DataClassification.INTERNAL,
            "payload": {"resource_id": str(uuid7())},
            "available_at": datetime.now(UTC) - timedelta(seconds=1),
        }
        values.update(overrides)
        return WorkItem(**values)


if __name__ == "__main__":
    unittest.main()
