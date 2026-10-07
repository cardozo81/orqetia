from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

import httpx

from orqetia.infrastructure.availability import (
    AvailabilityState,
    DependencyKind,
    DependencyStatus,
    MaintenanceMode,
    OperationalAvailabilityController,
)
from orqetia.infrastructure.http import create_app
from orqetia.infrastructure.processes.worker import (
    HandlerOutcome,
    HandlerRegistry,
    WorkerProcess,
)
from orqetia.shared.messaging import QueueName
from tests.api.test_fastapi_shell import FakeAuthenticator

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = json.loads(
    (ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json").read_text(
        encoding="utf-8"
    )
)


class ReadyProbe:
    async def check(self) -> None:
        return None


class FailingProbe:
    async def check(self) -> None:
        raise RuntimeError("synthetic database outage")


async def request(app: object, method: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
    ) as client:
        return await client.request(method, path)


class ClaimTrackingQueue:
    def __init__(self) -> None:
        self.claim_called = False

    async def claim(self, **_kwargs):
        self.claim_called = True
        return ()


class AvailabilityPolicyTests(unittest.TestCase):
    def test_dependency_policy_distinguishes_degraded_from_unavailable(self) -> None:
        api = OperationalAvailabilityController(process_role="api")
        api.set_dependency(
            DependencyKind.PROVIDER,
            DependencyStatus.UNAVAILABLE,
        )
        self.assertEqual(api.snapshot().readiness, AvailabilityState.DEGRADED)
        api.set_dependency(
            DependencyKind.DATABASE,
            DependencyStatus.UNAVAILABLE,
        )
        self.assertEqual(api.snapshot().readiness, AvailabilityState.UNAVAILABLE)

    def test_draining_rejects_mutations_with_retry_after_but_not_reads(self) -> None:
        availability = OperationalAvailabilityController(
            process_role="api",
            maintenance_mode=MaintenanceMode.DRAINING,
            retry_after_seconds=45,
        )
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            readiness_probe=ReadyProbe(),
            availability_controller=availability,
        )
        mutation = asyncio.run(request(app, "POST", "/v1/sessions"))
        safe_read = asyncio.run(request(app, "GET", "/v1/providers"))
        self.assertEqual(mutation.status_code, 503)
        self.assertEqual(mutation.headers["retry-after"], "45")
        self.assertEqual(mutation.json()["code"], "MAINTENANCE_DRAINING")
        self.assertEqual(safe_read.status_code, 401)

    def test_maintenance_rejects_v1_but_keeps_liveness(self) -> None:
        availability = OperationalAvailabilityController(
            process_role="api",
            maintenance_mode=MaintenanceMode.MAINTENANCE,
            retry_after_seconds=30,
        )
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            readiness_probe=ReadyProbe(),
            availability_controller=availability,
        )
        blocked = asyncio.run(request(app, "GET", "/v1/providers"))
        live = asyncio.run(request(app, "GET", "/health/live"))
        ready = asyncio.run(request(app, "GET", "/health/ready"))
        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(blocked.headers["retry-after"], "30")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json()["state"], "UNAVAILABLE")

    def test_provider_outage_is_degraded_and_database_outage_not_ready(self) -> None:
        availability = OperationalAvailabilityController(process_role="api")
        availability.set_dependency(
            DependencyKind.PROVIDER,
            DependencyStatus.UNAVAILABLE,
        )
        degraded_app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            readiness_probe=ReadyProbe(),
            availability_controller=availability,
        )
        degraded = asyncio.run(request(degraded_app, "GET", "/health/ready"))
        self.assertEqual(degraded.status_code, 200)
        self.assertEqual(degraded.json()["state"], "DEGRADED")

        unavailable_app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            readiness_probe=FailingProbe(),
            availability_controller=OperationalAvailabilityController(
                process_role="api"
            ),
        )
        unavailable = asyncio.run(request(unavailable_app, "GET", "/health/ready"))
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["state"], "UNAVAILABLE")
        self.assertNotIn("synthetic database outage", unavailable.text)

    def test_worker_drain_stops_new_claims(self) -> None:
        asyncio.run(self._worker_drain_stops_new_claims())

    async def _worker_drain_stops_new_claims(self) -> None:
        queue = ClaimTrackingQueue()
        registry = HandlerRegistry()

        async def handler(_lease):
            return HandlerOutcome.complete()

        registry.register("synthetic", 1, handler)
        availability = OperationalAvailabilityController(
            process_role="worker",
            maintenance_mode=MaintenanceMode.DRAINING,
        )
        worker = WorkerProcess(
            queue=queue,  # type: ignore[arg-type]
            registry=registry,
            queue_name=QueueName.EXECUTION,
            concurrency=1,
            lease_seconds=30,
            poll_interval_seconds=0.01,
            shutdown_grace_seconds=1,
            availability_controller=availability,
        )
        handled = await worker.run_once()
        self.assertEqual(handled, 0)
        self.assertFalse(queue.claim_called)


if __name__ == "__main__":
    unittest.main()
