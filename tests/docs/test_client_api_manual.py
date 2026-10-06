from __future__ import annotations

import json
import re
from pathlib import Path

from orqetia.infrastructure.http.models import TaskCreateRequest

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json"
MANUAL = ROOT / "docs" / "api" / "client-api-v1-manual.md"


def _endpoint_sections(manual: str) -> dict[str, str]:
    matches = list(
        re.finditer(
            r"^### (GET|POST|PUT|PATCH|DELETE) (/v1/[^\n]+)$",
            manual,
            flags=re.MULTILINE,
        )
    )
    output: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(manual)
        output[f"{match.group(1)} {match.group(2)}"] = manual[start:end]
    return output


def _quickstart_json(manual: str, name: str) -> dict[str, object]:
    match = re.search(
        rf"<!-- QUICKSTART_{name}_JSON_START -->\s*"
        r"~~~json\s*(\{.*?\})\s*~~~\s*"
        rf"<!-- QUICKSTART_{name}_JSON_END -->",
        manual,
        flags=re.DOTALL,
    )
    assert match is not None, f"missing {name} quickstart JSON"
    payload = json.loads(match.group(1))
    assert isinstance(payload, dict)
    return payload


def test_manual_covers_every_openapi_v1_operation() -> None:
    contract = json.loads(OPENAPI.read_text(encoding="utf-8"))
    manual = MANUAL.read_text(encoding="utf-8")
    sections = _endpoint_sections(manual)

    expected: dict[str, dict[str, object]] = {}
    for path, path_item in contract["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is not None:
                expected[f"{method.upper()} {path}"] = operation

    assert set(sections) == set(expected)
    assert len(expected) == 16

    for key, operation in expected.items():
        section = sections[key]
        assert "cURL:" in section
        for scope in operation.get("x-orqetia-required-scopes", []):
            assert scope in section
        idempotent = any(
            parameter.get("$ref", "").endswith("/IdempotencyKey")
            for parameter in operation.get("parameters", [])
        )
        if idempotent:
            assert "Idempotency-Key" in section

    assert "X-Correlation-ID" in manual
    assert "Error envelope" in manual or "Error Envelope" in manual
    assert "provider cost" in manual.lower()
    assert "client_charge" in manual


def test_auto_and_explicit_quickstarts_match_http_model() -> None:
    manual = MANUAL.read_text(encoding="utf-8")
    auto = TaskCreateRequest.model_validate(
        _quickstart_json(manual, "AUTO")
    )
    explicit = TaskCreateRequest.model_validate(
        _quickstart_json(manual, "EXPLICIT")
    )

    assert auto.execution is None
    assert explicit.execution is not None
    assert explicit.execution.mode == "EXPLICIT_TARGET"
    assert explicit.execution.target.provider == "provider-visible-id"
    assert explicit.execution.target.model == "model-visible-id"
    assert explicit.execution.target.reasoning_profile == "standard"
