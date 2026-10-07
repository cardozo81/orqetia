"""Bounded synthetic capacity profile for M9 hardening (#147)."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid7

import httpx
from benchmark_read_models import benchmark_read_models

from orqetia.identity.authentication import AuthenticatedPrincipal
from orqetia.infrastructure.http import create_app
from orqetia.infrastructure.messaging import PostgresWorkQueue
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings
from orqetia.shared.messaging import DataClassification, QueueName, WorkItem

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json"


class _SyntheticAuthenticator:
    async def authenticate_bearer(self, _token: str) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            subject_type="SERVICE_CLIENT",
            subject_id="capacity-profile",
            tenant_id=str(uuid7()),
            client_id=str(uuid7()),
            scopes=frozenset(),
        )


def _p95_ms(values: list[float]) -> float:
    if not values:
        raise ValueError("latency sample is empty")
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index] * 1000.0


async def benchmark_api_shell(*, requests: int) -> float:
    if requests < 1:
        raise ValueError("requests must be positive")
    contract = json.loads(OPENAPI.read_text(encoding="utf-8"))
    app = create_app(
        openapi_document=contract,
        authenticator=_SyntheticAuthenticator(),
    )
    latencies: list[float] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://capacity.test",
    ) as client:
        for _ in range(requests):
            started = perf_counter()
            response = await client.get("/health/live")
            elapsed = perf_counter() - started
            if response.status_code != 200 or response.json() != {"status": "live"}:
                raise RuntimeError("synthetic API health request failed")
            latencies.append(elapsed)
    return _p95_ms(latencies)


async def benchmark_queue(*, items: int) -> float:
    if items < 1:
        raise ValueError("items must be positive")
    settings = RuntimeSettings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    queue = PostgresWorkQueue(factory)
    tenant_id, client_id = uuid7(), uuid7()
    now = datetime.now(UTC)
    started = perf_counter()
    try:
        for index in range(items):
            await queue.enqueue(
                WorkItem(
                    work_id=uuid7(),
                    queue_name=QueueName.MAINTENANCE,
                    operation_type="capacity.synthetic",
                    operation_version=1,
                    tenant_id=tenant_id,
                    client_id=client_id,
                    resource_type="capacity_probe",
                    resource_id=uuid7(),
                    data_classification=DataClassification.CLIENT_PRIVATE,
                    payload={"index": index},
                    available_at=now,
                    logical_operation_id=f"capacity-{index}",
                )
            )

        completed = 0
        while completed < items:
            leases = await queue.claim(
                queue_name=QueueName.MAINTENANCE,
                lease_owner="capacity-profile",
                lease_seconds=30,
                limit=min(100, items - completed),
            )
            if not leases:
                raise RuntimeError("synthetic queue stopped yielding ready work")
            for lease in leases:
                if not await queue.complete(lease):
                    raise RuntimeError("synthetic queue completion was rejected")
                completed += 1
    finally:
        await engine.dispose()
    return perf_counter() - started


async def run_profile(
    *,
    api_requests: int,
    queue_items: int,
    read_model_size: int,
) -> dict[str, float]:
    api_p95_ms = await benchmark_api_shell(requests=api_requests)
    queue_seconds = await benchmark_queue(items=queue_items)
    read_model_seconds = benchmark_read_models(size=read_model_size)
    return {
        "api_p95_ms": api_p95_ms,
        "queue_seconds": queue_seconds,
        "read_model_seconds": read_model_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-requests", type=int, default=250)
    parser.add_argument("--queue-items", type=int, default=100)
    parser.add_argument("--read-model-size", type=int, default=5_000)
    parser.add_argument("--max-api-p95-ms", type=float, default=150.0)
    parser.add_argument("--max-queue-seconds", type=float, default=15.0)
    parser.add_argument("--max-read-model-seconds", type=float, default=10.0)
    args = parser.parse_args()

    if min(args.api_requests, args.queue_items, args.read_model_size) < 1:
        raise SystemExit("profile workload sizes must be positive")
    if min(
        args.max_api_p95_ms,
        args.max_queue_seconds,
        args.max_read_model_seconds,
    ) <= 0:
        raise SystemExit("profile thresholds must be positive")

    results = asyncio.run(
        run_profile(
            api_requests=args.api_requests,
            queue_items=args.queue_items,
            read_model_size=args.read_model_size,
        )
    )
    print(
        "capacity_profile "
        f"api_p95_ms={results['api_p95_ms']:.3f} "
        f"queue_seconds={results['queue_seconds']:.3f} "
        f"read_model_seconds={results['read_model_seconds']:.3f}"
    )

    failures: list[str] = []
    if results["api_p95_ms"] > args.max_api_p95_ms:
        failures.append(
            f"api p95 {results['api_p95_ms']:.3f}ms > "
            f"{args.max_api_p95_ms:.3f}ms"
        )
    if results["queue_seconds"] > args.max_queue_seconds:
        failures.append(
            f"queue {results['queue_seconds']:.3f}s > "
            f"{args.max_queue_seconds:.3f}s"
        )
    if results["read_model_seconds"] > args.max_read_model_seconds:
        failures.append(
            f"read model {results['read_model_seconds']:.3f}s > "
            f"{args.max_read_model_seconds:.3f}s"
        )
    if failures:
        raise SystemExit("capacity regression: " + "; ".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
