from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate_supply_chain_artifacts.py"
REVISION = "a" * 40


def _generate(output: Path, *, secret: str) -> tuple[bytes, bytes]:
    env = dict(os.environ)
    env["ORQETIA_DATABASE_DSN"] = (
        "postgresql+psycopg://user:" + secret + "@db.example/orqetia"
    )
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "generate",
            "--lock",
            str(ROOT / "uv.lock"),
            "--pyproject",
            str(ROOT / "pyproject.toml"),
            "--source-revision",
            REVISION,
            "--output-dir",
            str(output),
        ],
        check=True,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    return (
        (output / "orqetia.cdx.json").read_bytes(),
        (output / "orqetia.provenance.json").read_bytes(),
    )


def test_supply_chain_artifacts_are_deterministic_and_secret_free(
    tmp_path: Path,
) -> None:
    secret = "supply-chain-test-secret-do-not-copy"
    first_sbom, first_provenance = _generate(tmp_path / "first", secret=secret)
    second_sbom, second_provenance = _generate(tmp_path / "second", secret=secret)

    assert first_sbom == second_sbom
    assert first_provenance == second_provenance
    assert secret.encode() not in first_sbom
    assert secret.encode() not in first_provenance

    sbom = json.loads(first_sbom)
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.6"
    assert len(sbom["components"]) > 10

    components = {item["name"]: item for item in sbom["components"]}
    assert "fastapi" in components
    assert "pip-audit" in components
    fastapi_properties = {
        item["name"]: item["value"]
        for item in components["fastapi"]["properties"]
    }
    audit_properties = {
        item["name"]: item["value"]
        for item in components["pip-audit"]["properties"]
    }
    assert fastapi_properties["orqetia:dependency-scope"] == "runtime-direct"
    assert audit_properties["orqetia:dependency-scope"] == "dev-direct"


def test_provenance_verifies_sbom_and_locked_materials(tmp_path: Path) -> None:
    sbom_bytes, provenance_bytes = _generate(tmp_path / "out", secret="unused")
    provenance = json.loads(provenance_bytes)

    assert provenance["_type"] == "https://in-toto.io/Statement/v1"
    assert provenance["predicateType"] == "https://slsa.dev/provenance/v1"
    assert provenance["subject"] == [
        {
            "name": "orqetia.cdx.json",
            "digest": {"sha256": hashlib.sha256(sbom_bytes).hexdigest()},
        }
    ]

    dependencies = provenance["predicate"]["buildDefinition"][
        "resolvedDependencies"
    ]
    source = dependencies[0]
    lock = dependencies[1]
    assert source["digest"]["gitCommit"] == REVISION
    assert source["uri"].endswith("@" + REVISION)
    assert lock["digest"]["sha256"] == hashlib.sha256(
        (ROOT / "uv.lock").read_bytes()
    ).hexdigest()
