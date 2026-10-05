from __future__ import annotations

import asyncio
import os
import unittest

from sqlalchemy import text

from orqetia.infrastructure.persistence import (
    AUTHORITATIVE_SCHEMAS,
    INFRASTRUCTURE_SCHEMAS,
    create_engine,
    create_session_factory,
    transaction_scope,
)
from orqetia.settings import RuntimeSettings


class PostgreSQLPersistenceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "ORQETIA_DATABASE_DSN" not in os.environ:
            raise unittest.SkipTest("PostgreSQL integration DSN is not configured")

    def test_postgresql_18_and_schemas_exist(self) -> None:
        asyncio.run(self._assert_postgresql_18_and_schemas())

    def test_transaction_scope_rolls_back_on_exception(self) -> None:
        asyncio.run(self._assert_transaction_rollback())

    async def _assert_postgresql_18_and_schemas(self) -> None:
        settings = RuntimeSettings()
        engine = create_engine(settings)
        try:
            async with engine.connect() as connection:
                version_num = int((await connection.execute(text("SHOW server_version_num"))).scalar_one())
                isolation = (await connection.execute(text("SHOW transaction_isolation"))).scalar_one()
                rows = await connection.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name = ANY(:schemas)"
                    ),
                    {"schemas": list(AUTHORITATIVE_SCHEMAS + INFRASTRUCTURE_SCHEMAS)},
                )
                actual = {row[0] for row in rows}

            self.assertEqual(version_num // 10000, 18)
            self.assertEqual(str(isolation).lower(), "read committed")
            self.assertEqual(actual, set(AUTHORITATIVE_SCHEMAS + INFRASTRUCTURE_SCHEMAS))
        finally:
            await engine.dispose()

    async def _assert_transaction_rollback(self) -> None:
        settings = RuntimeSettings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        table = "execution.__orqetia_tx_probe"

        try:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
                await connection.execute(text(f"CREATE TABLE {table} (value integer NOT NULL)"))

            with self.assertRaisesRegex(RuntimeError, "force rollback"):
                async with transaction_scope(factory) as session:
                    await session.execute(text(f"INSERT INTO {table} (value) VALUES (1)"))
                    raise RuntimeError("force rollback")

            async with engine.connect() as connection:
                count = (await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            self.assertEqual(count, 0)
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
