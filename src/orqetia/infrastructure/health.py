"""Local dependency health/readiness probes.

Health probes never call external AI providers.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class ReadinessProbe(Protocol):
    async def check(self) -> None: ...


class DatabaseReadinessProbe:
    def __init__(self, engine: AsyncEngine, *, timeout_seconds: float = 2.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._engine = engine
        self._timeout = timeout_seconds

    async def check(self) -> None:
        async with asyncio.timeout(self._timeout):
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
