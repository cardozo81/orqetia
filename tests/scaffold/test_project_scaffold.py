from __future__ import annotations

import ast
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PACKAGE = SRC / "orqetia"

EXPECTED_CONTEXTS = {
    "identity",
    "tenancy",
    "control_plane",
    "execution",
    "providers",
    "usage_accounting",
    "estimation",
    "audit",
    "read_models",
    "shared",
}


class ProjectScaffoldTests(unittest.TestCase):
    def test_pyproject_declares_python_314_only(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["requires-python"], ">=3.14,<3.15")

    def test_pyproject_matches_selected_stack_ranges(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        deps = "\n".join(data["project"]["dependencies"])
        for required in (
            "fastapi>=0.141,<0.142",
            "pydantic>=2.13,<2.14",
            "sqlalchemy[asyncio]>=2.1,<2.2",
            "alembic>=1.20,<1.21",
            "uvicorn[standard]>=0.46,<0.47",
        ):
            self.assertIn(required, deps)

    def test_bounded_context_packages_exist(self) -> None:
        actual = {
            path.name
            for path in PACKAGE.iterdir()
            if path.is_dir() and (path / "__init__.py").exists()
        }
        self.assertTrue(EXPECTED_CONTEXTS.issubset(actual))

    def test_apps_composition_roots_exist(self) -> None:
        for name in ("api", "worker", "scheduler"):
            self.assertTrue((ROOT / "apps" / name / "__init__.py").exists())

    def test_package_import_smoke(self) -> None:
        sys.path.insert(0, str(SRC))
        try:
            import orqetia
            self.assertEqual(orqetia.__version__, "0.1.0.dev0")
        finally:
            sys.path.pop(0)
            sys.modules.pop("orqetia", None)

    def test_production_source_does_not_import_tests_or_rasai(self) -> None:
        forbidden_prefixes = ("tests", "RASAI", "rasai")
        violations: list[str] = []

        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name.startswith(forbidden_prefixes):
                        violations.append(f"{path.relative_to(ROOT)} imports {name}")

        self.assertEqual(violations, [])

    def test_lockfile_is_committed_after_bootstrap(self) -> None:
        self.assertTrue(
            (ROOT / "uv.lock").exists(),
            "uv.lock must be committed; bootstrap workflow prints a generated lock when absent",
        )


if __name__ == "__main__":
    unittest.main()
