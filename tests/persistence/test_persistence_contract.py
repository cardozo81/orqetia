from __future__ import annotations

import ast
import unittest
from pathlib import Path

from orqetia.infrastructure.persistence.schemas import (
    AUTHORITATIVE_SCHEMAS,
    INFRASTRUCTURE_SCHEMAS,
    SCHEMA_OWNERSHIP,
    metadata_for_schema,
)

ROOT = Path(__file__).resolve().parents[2]


class PersistenceFoundationContractTests(unittest.TestCase):
    def test_schema_registry_matches_accepted_architecture(self) -> None:
        self.assertEqual(
            AUTHORITATIVE_SCHEMAS,
            ("identity", "control", "execution", "accounting", "estimation", "audit", "readmodel"),
        )
        self.assertEqual(INFRASTRUCTURE_SCHEMAS, ("messaging",))
        self.assertEqual(set(SCHEMA_OWNERSHIP), set(AUTHORITATIVE_SCHEMAS + INFRASTRUCTURE_SCHEMAS))

    def test_metadata_is_isolated_per_schema(self) -> None:
        execution = metadata_for_schema("execution")
        accounting = metadata_for_schema("accounting")
        self.assertEqual(execution.schema, "execution")
        self.assertEqual(accounting.schema, "accounting")
        self.assertIsNot(execution, accounting)

    def test_unknown_schema_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            metadata_for_schema("shared_business")

    def test_initial_migration_contains_no_table_creation_or_cross_schema_fk(self) -> None:
        migration = ROOT / "migrations" / "versions" / "20261005_0001_foundation_schemas.py"
        tree = ast.parse(migration.read_text(encoding="utf-8"), filename=str(migration))

        called_attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
        }
        self.assertNotIn("create_table", called_attributes)
        self.assertNotIn("create_foreign_key", called_attributes)

    def test_no_sqlite_persistence_configuration_is_present(self) -> None:
        for path in (
            ROOT / "alembic.ini",
            ROOT / "migrations" / "env.py",
            ROOT / "src" / "orqetia" / "infrastructure" / "persistence" / "database.py",
        ):
            self.assertNotIn("sqlite", path.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
