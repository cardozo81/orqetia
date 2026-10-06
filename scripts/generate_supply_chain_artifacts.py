#!/usr/bin/env python3
"""Generate deterministic ORQETIA supply-chain artifacts from locked inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPOSITORY_URI = "https://github.com/cardozo81/orqetia"
BUILD_TYPE = "https://orqetia.dev/build-types/python-locked/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
CYCLONEDX_SPEC_VERSION = "1.6"
REVISION = re.compile(r"^[0-9a-f]{40}$")
DEPENDENCY_NAME = re.compile(r"^\s*([A-Za-z0-9._-]+)")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _dependency_name(specification: str) -> str:
    match = DEPENDENCY_NAME.match(specification)
    if match is None:
        raise ValueError(f"invalid dependency specification: {specification!r}")
    return _normalized_name(match.group(1))


def _purl(name: str, version: str) -> str:
    return (
        "pkg:pypi/"
        + quote(_normalized_name(name), safe="-._~")
        + "@"
        + quote(version, safe="-._~+")
    )


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a TOML document")
    return value


def _direct_dependencies(pyproject: dict[str, Any]) -> tuple[set[str], set[str]]:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject is missing [project]")

    runtime_raw = project.get("dependencies", [])
    if not isinstance(runtime_raw, list):
        raise ValueError("project.dependencies must be an array")
    runtime = {_dependency_name(str(item)) for item in runtime_raw}

    groups = pyproject.get("dependency-groups", {})
    if not isinstance(groups, dict):
        raise ValueError("dependency-groups must be a table")
    dev_raw = groups.get("dev", [])
    if not isinstance(dev_raw, list):
        raise ValueError("dependency-groups.dev must be an array")
    dev = {_dependency_name(str(item)) for item in dev_raw}
    return runtime, dev


def _project_identity(pyproject: dict[str, Any]) -> tuple[str, str, str]:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject is missing [project]")
    name = str(project.get("name", "")).strip()
    version = str(project.get("version", "")).strip()
    requires_python = str(project.get("requires-python", "")).strip()
    if not name or not version or not requires_python:
        raise ValueError("project name/version/requires-python are required")
    return name, version, requires_python


def _locked_packages(lock: dict[str, Any]) -> list[dict[str, Any]]:
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise ValueError("uv.lock must contain [[package]] entries")
    output: list[dict[str, Any]] = []
    for raw in packages:
        if not isinstance(raw, dict):
            raise ValueError("uv.lock package entry must be a table")
        name = str(raw.get("name", "")).strip()
        version = str(raw.get("version", "")).strip()
        if not name or not version:
            raise ValueError("locked package name/version are required")
        output.append(raw)
    return output


def _source_hash(package: dict[str, Any]) -> dict[str, str] | None:
    candidates: list[object] = [package.get("sdist")]
    wheels = package.get("wheels", [])
    if isinstance(wheels, list):
        candidates.extend(wheels)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        raw_hash = candidate.get("hash")
        if not isinstance(raw_hash, str) or ":" not in raw_hash:
            continue
        algorithm, content = raw_hash.split(":", 1)
        if algorithm.lower() == "sha256" and re.fullmatch(r"[0-9a-f]{64}", content):
            return {"alg": "SHA-256", "content": content}
    return None


def _dependency_scope(
    name: str,
    *,
    runtime: set[str],
    dev: set[str],
) -> str:
    normalized = _normalized_name(name)
    if normalized in runtime:
        return "runtime-direct"
    if normalized in dev:
        return "dev-direct"
    return "transitive"


def build_sbom(
    *,
    lock_bytes: bytes,
    lock: dict[str, Any],
    pyproject: dict[str, Any],
) -> dict[str, Any]:
    project_name, project_version, requires_python = _project_identity(pyproject)
    runtime, dev = _direct_dependencies(pyproject)
    packages = _locked_packages(lock)
    lock_digest = _sha256(lock_bytes)
    root_ref = _purl(project_name, project_version)

    components: list[dict[str, Any]] = []
    refs_by_name: dict[str, list[str]] = {}
    package_by_ref: dict[str, dict[str, Any]] = {}

    for package in packages:
        name = str(package["name"])
        version = str(package["version"])
        if _normalized_name(name) == _normalized_name(project_name):
            continue
        ref = _purl(name, version)
        refs_by_name.setdefault(_normalized_name(name), []).append(ref)
        package_by_ref[ref] = package

        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": ref,
            "properties": [
                {
                    "name": "orqetia:dependency-scope",
                    "value": _dependency_scope(name, runtime=runtime, dev=dev),
                }
            ],
        }
        digest = _source_hash(package)
        if digest is not None:
            component["hashes"] = [digest]
        components.append(component)

    components.sort(key=lambda item: str(item["bom-ref"]))

    dependencies: list[dict[str, Any]] = []
    for ref in sorted(package_by_ref):
        package = package_by_ref[ref]
        depends_on: set[str] = set()
        raw_dependencies = package.get("dependencies", [])
        if isinstance(raw_dependencies, list):
            for raw in raw_dependencies:
                if not isinstance(raw, dict):
                    continue
                dep_name = raw.get("name")
                if not isinstance(dep_name, str):
                    continue
                matches = refs_by_name.get(_normalized_name(dep_name), [])
                if len(matches) == 1:
                    depends_on.add(matches[0])
        dependencies.append({"ref": ref, "dependsOn": sorted(depends_on)})

    root_dependencies: set[str] = set()
    for name in runtime | dev:
        matches = refs_by_name.get(name, [])
        if len(matches) == 1:
            root_dependencies.add(matches[0])
    dependencies.insert(0, {"ref": root_ref, "dependsOn": sorted(root_dependencies)})

    serial = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{REPOSITORY_URI}:{project_version}:{lock_digest}",
    )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": project_name,
                "version": project_version,
                "purl": root_ref,
                "properties": [
                    {"name": "orqetia:python", "value": requires_python},
                    {"name": "orqetia:lockfile", "value": "uv.lock"},
                    {"name": "orqetia:lockfile-sha256", "value": lock_digest},
                ],
            }
        },
        "components": components,
        "dependencies": dependencies,
    }


def build_provenance(
    *,
    sbom_bytes: bytes,
    lock_bytes: bytes,
    pyproject: dict[str, Any],
    source_revision: str,
) -> dict[str, Any]:
    if REVISION.fullmatch(source_revision) is None:
        raise ValueError("source_revision must be a lowercase 40-character git SHA")
    project_name, project_version, requires_python = _project_identity(pyproject)
    return {
        "_type": STATEMENT_TYPE,
        "subject": [
            {
                "name": "orqetia.cdx.json",
                "digest": {"sha256": _sha256(sbom_bytes)},
            }
        ],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": BUILD_TYPE,
                "externalParameters": {
                    "project": project_name,
                    "version": project_version,
                    "requiresPython": requires_python,
                    "lockfile": "uv.lock",
                },
                "internalParameters": {},
                "resolvedDependencies": [
                    {
                        "uri": f"git+{REPOSITORY_URI}@{source_revision}",
                        "digest": {"gitCommit": source_revision},
                    },
                    {
                        "uri": "file:uv.lock",
                        "digest": {"sha256": _sha256(lock_bytes)},
                    },
                ],
            },
            "runDetails": {
                "builder": {
                    "id": (
                        REPOSITORY_URI
                        + "/.github/workflows/supply-chain.yml"
                    )
                }
            },
        },
    }


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")


def generate_artifacts(
    *,
    lock_path: Path,
    pyproject_path: Path,
    source_revision: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    lock_bytes = lock_path.read_bytes()
    lock = _read_toml(lock_path)
    pyproject = _read_toml(pyproject_path)

    sbom_bytes = _canonical_json(
        build_sbom(lock_bytes=lock_bytes, lock=lock, pyproject=pyproject)
    )
    provenance_bytes = _canonical_json(
        build_provenance(
            sbom_bytes=sbom_bytes,
            lock_bytes=lock_bytes,
            pyproject=pyproject,
            source_revision=source_revision,
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    sbom_path = output_dir / "orqetia.cdx.json"
    provenance_path = output_dir / "orqetia.provenance.json"
    sbom_path.write_bytes(sbom_bytes)
    provenance_path.write_bytes(provenance_bytes)
    return sbom_path, provenance_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    generate = subcommands.add_parser("generate")
    generate.add_argument("--lock", type=Path, default=Path("uv.lock"))
    generate.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
    )
    generate.add_argument("--source-revision", required=True)
    generate.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "generate":
        sbom, provenance = generate_artifacts(
            lock_path=args.lock,
            pyproject_path=args.pyproject,
            source_revision=args.source_revision,
            output_dir=args.output_dir,
        )
        print(f"SBOM: {sbom}")
        print(f"Provenance: {provenance}")
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
