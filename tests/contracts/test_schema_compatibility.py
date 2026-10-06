from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "schema_compatibility_check.py"


def _run(
    tmp_path: Path,
    *,
    old: dict[str, object],
    new: dict[str, object],
    kind: str = "json-schema",
    approvals: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    approvals_path = tmp_path / "approvals.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    new_path.write_text(json.dumps(new), encoding="utf-8")
    approvals_path.write_text(
        json.dumps(approvals or {"version": 1, "approvals": []}),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "check",
            "--kind",
            kind,
            "--old",
            str(old_path),
            "--new",
            str(new_path),
            "--contract",
            "fixture",
            "--approvals",
            str(approvals_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _schema(*, required: list[str] | None = None) -> dict[str, object]:
    return {
        "type": "object",
        "required": required or ["id"],
        "properties": {
            "id": {"type": "string"},
        },
        "additionalProperties": False,
    }


def test_optional_property_addition_is_additive(tmp_path: Path) -> None:
    old = _schema()
    new = _schema()
    new["properties"]["note"] = {"type": "string"}  # type: ignore[index]

    result = _run(tmp_path, old=old, new=new)

    assert result.returncode == 0
    assert "classification=additive" in result.stdout


def test_required_property_change_is_breaking(tmp_path: Path) -> None:
    old = _schema()
    new = _schema(required=["id", "note"])
    new["properties"]["note"] = {"type": "string"}  # type: ignore[index]

    result = _run(tmp_path, old=old, new=new)

    assert result.returncode == 1
    assert "classification=breaking" in result.stdout
    assert "breaking_fingerprint=" in result.stdout
    assert "breaking_approval=missing" in result.stdout


def test_enum_growth_requires_extensible_marker(tmp_path: Path) -> None:
    old = {"type": "string", "enum": ["A"]}
    new = {"type": "string", "enum": ["A", "B"]}

    closed = _run(tmp_path / "closed", old=old, new=new)
    assert closed.returncode == 1
    assert "enum values added" in closed.stdout

    extensible_old = {
        "type": "string",
        "enum": ["A"],
        "x-orqetia-extensible-enum": True,
    }
    extensible_new = {
        "type": "string",
        "enum": ["A", "B"],
        "x-orqetia-extensible-enum": True,
    }
    extensible = _run(
        tmp_path / "extensible",
        old=extensible_old,
        new=extensible_new,
    )
    assert extensible.returncode == 0
    assert "classification=additive" in extensible.stdout


def test_exact_breaking_fingerprint_can_be_explicitly_approved(
    tmp_path: Path,
) -> None:
    old = _schema()
    new = {"type": "object", "properties": {}, "additionalProperties": False}
    first = _run(tmp_path / "first", old=old, new=new)
    assert first.returncode == 1
    fingerprint = next(
        line.split("=", 1)[1]
        for line in first.stdout.splitlines()
        if line.startswith("breaking_fingerprint=")
    )
    assert len(fingerprint) == len(hashlib.sha256().hexdigest())

    approvals = {
        "version": 1,
        "approvals": [
            {
                "contract": "fixture",
                "fingerprint": fingerprint,
                "issue": "#999",
                "reason": "Intentional development contract reset.",
            }
        ],
    }
    approved = _run(
        tmp_path / "approved",
        old=old,
        new=new,
        approvals=approvals,
    )
    assert approved.returncode == 0
    assert "breaking_approval=#999" in approved.stdout


def test_openapi_operation_removal_is_breaking(tmp_path: Path) -> None:
    operation = {
        "responses": {
            "200": {
                "description": "ok",
            }
        }
    }
    old = {
        "openapi": "3.1.0",
        "paths": {"/v1/items": {"get": operation}},
        "components": {"schemas": {}},
    }
    new = {
        "openapi": "3.1.0",
        "paths": {},
        "components": {"schemas": {}},
    }

    result = _run(tmp_path, old=old, new=new, kind="openapi")

    assert result.returncode == 1
    assert "GET /v1/items" in result.stdout
    assert "operation removed" in result.stdout
