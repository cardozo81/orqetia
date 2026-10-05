"""Reproducible synthetic read-model cache/page benchmark for issue #45."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid7

from orqetia.read_models import (
    InMemoryReadModelCache,
    ProjectionFreshness,
    ProjectionIdentity,
    ReadAudience,
    ReadClassification,
    ReadModelDocument,
    ReadSurface,
    paginate,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10_000)
    parser.add_argument("--max-seconds", type=float, default=10.0)
    args = parser.parse_args()
    if args.size < 1 or args.max_seconds <= 0:
        raise SystemExit("size/max-seconds must be positive")

    now = datetime.now(UTC)
    tenant_id, client_id = uuid7(), uuid7()
    cache = InMemoryReadModelCache()
    documents = []
    started = perf_counter()
    for index in range(args.size):
        identity = ProjectionIdentity(
            surface=ReadSurface.USAGE_SUMMARY,
            audience=ReadAudience.CLIENT,
            tenant_id=tenant_id,
            client_id=client_id,
            role_scope=("client_reader",),
            query_fingerprint=f"bucket={index}",
        )
        document = ReadModelDocument.create(
            identity=identity,
            version=1,
            classification=ReadClassification.CLIENT_PRIVATE,
            freshness=ProjectionFreshness(
                as_of=now,
                source_watermark=f"synthetic:{index}",
                refreshed_at=now,
            ),
            payload={"total_tokens": index, "requests": index % 100},
        )
        cache.put(document)
        documents.append(document)
    for document in documents:
        lookup = cache.get(identity=document.identity, now=now)
        if not lookup.hit:
            raise RuntimeError("synthetic cache lookup missed")
    page = paginate(
        documents,
        key=lambda item: item.identity.query_fingerprint,
        page_size=100,
        query_fingerprint="synthetic-page",
    )
    if len(page.items) != min(100, args.size):
        raise RuntimeError("synthetic page size mismatch")
    elapsed = perf_counter() - started
    print(f"read_model_benchmark size={args.size} elapsed_seconds={elapsed:.6f}")
    if elapsed > args.max_seconds:
        raise SystemExit(
            f"synthetic benchmark exceeded {args.max_seconds}s: {elapsed:.6f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
