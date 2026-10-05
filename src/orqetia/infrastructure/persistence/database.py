"""Async SQLAlchemy runtime primitives for PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from orqetia.settings import RuntimeSettings

SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(settings: RuntimeSettings) -> AsyncEngine:
    """Create the process-local async engine from typed runtime settings."""

    return create_async_engine(
        settings.database_dsn.get_secret_value(),
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        pool_reset_on_return="rollback",
    )


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create sessions that never expire objects implicitly after commit."""

    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )


@asynccontextmanager
async def transaction_scope(factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Own one local database transaction for one application use case.

    External provider calls must never be executed while holding this context.
    Exceptions roll the transaction back before propagating.
    """

    async with factory() as session:
        async with session.begin():
            yield session
