"""Snapshot stores for privacy-governed estimation benchmarks."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .benchmarks import BenchmarkSnapshot, ReferenceScope
from .tables import benchmark_snapshots

SessionFactory = async_sessionmaker[AsyncSession]


class BenchmarkSnapshotStore(Protocol):
    async def append(self, snapshot: BenchmarkSnapshot) -> None: ...

    async def get(self, snapshot_id: UUID) -> BenchmarkSnapshot | None: ...


class PostgresBenchmarkSnapshotStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def append(self, snapshot: BenchmarkSnapshot) -> None:
        key = snapshot.feature_key
        metrics = snapshot.metrics
        statement = sa.insert(benchmark_snapshots).values(
            snapshot_id=snapshot.snapshot_id,
            scope=snapshot.scope.value,
            tenant_id=snapshot.tenant_id,
            client_id=snapshot.client_id,
            provider_id=key.provider_id,
            model_id=key.model_id,
            reasoning_profile=key.reasoning_profile,
            input_size_bucket=key.input_size_bucket,
            output_class=key.output_class,
            schema_class=key.schema_class,
            methodology_version=snapshot.methodology_version,
            benchmark_version=snapshot.benchmark_version,
            as_of=snapshot.as_of,
            sample_count=snapshot.sample_count,
            public_sample_size=snapshot.public_sample_size,
            distinct_client_count=snapshot.distinct_client_count,
            public_cohort_size=snapshot.public_cohort_size,
            confidence=snapshot.confidence.value,
            median_input_tokens=metrics.median_input_tokens,
            median_output_tokens=metrics.median_output_tokens,
            median_cached_input_tokens=metrics.median_cached_input_tokens,
            median_reasoning_tokens=metrics.median_reasoning_tokens,
            median_total_tokens=metrics.median_total_tokens,
            p90_output_tokens=metrics.p90_output_tokens,
            p90_total_tokens=metrics.p90_total_tokens,
        )
        async with self._sessions.begin() as database:
            await database.execute(statement)

    async def get(self, snapshot_id: UUID) -> BenchmarkSnapshot | None:
        statement = sa.select(benchmark_snapshots).where(
            benchmark_snapshots.c.snapshot_id == snapshot_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        from .benchmarks import (
            BenchmarkConfidence,
            BenchmarkFeatureKey,
            BenchmarkMetrics,
        )

        return BenchmarkSnapshot(
            snapshot_id=row["snapshot_id"],
            scope=ReferenceScope(row["scope"]),
            tenant_id=row["tenant_id"],
            client_id=row["client_id"],
            feature_key=BenchmarkFeatureKey(
                provider_id=row["provider_id"],
                model_id=row["model_id"],
                reasoning_profile=row["reasoning_profile"],
                input_size_bucket=row["input_size_bucket"],
                output_class=row["output_class"],
                schema_class=row["schema_class"],
            ),
            methodology_version=row["methodology_version"],
            benchmark_version=row["benchmark_version"],
            as_of=row["as_of"],
            sample_count=row["sample_count"],
            public_sample_size=row["public_sample_size"],
            distinct_client_count=row["distinct_client_count"],
            public_cohort_size=row["public_cohort_size"],
            confidence=BenchmarkConfidence(row["confidence"]),
            metrics=BenchmarkMetrics(
                median_input_tokens=row["median_input_tokens"],
                median_output_tokens=row["median_output_tokens"],
                median_cached_input_tokens=row["median_cached_input_tokens"],
                median_reasoning_tokens=row["median_reasoning_tokens"],
                median_total_tokens=row["median_total_tokens"],
                p90_output_tokens=row["p90_output_tokens"],
                p90_total_tokens=row["p90_total_tokens"],
            ),
        )
