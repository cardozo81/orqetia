#!/usr/bin/env python3
"""Conservative compatibility classifier for ORQETIA public JSON contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
APPROVAL_ISSUE = re.compile(r"^#[0-9]+$")
CONSTRAINT_KEYS = (
    "type",
    "const",
    "format",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "additionalProperties",
)


@dataclass(frozen=True, order=True)
class Change:
    severity: str
    location: str
    detail: str


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _append(
    changes: list[Change],
    severity: str,
    location: str,
    detail: str,
) -> None:
    changes.append(Change(severity, location, detail))


def _enum_changes(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    location: str,
    changes: list[Change],
) -> None:
    old_enum = old.get("enum")
    new_enum = new.get("enum")
    if not isinstance(old_enum, list) or not isinstance(new_enum, list):
        if old_enum != new_enum and (old_enum is not None or new_enum is not None):
            _append(changes, "breaking", location, "enum contract changed")
        return

    old_values = {json.dumps(item, sort_keys=True) for item in old_enum}
    new_values = {json.dumps(item, sort_keys=True) for item in new_enum}
    removed = sorted(old_values - new_values)
    added = sorted(new_values - old_values)
    if removed:
        _append(
            changes,
            "breaking",
            location,
            f"enum values removed: {', '.join(removed)}",
        )
    if added:
        severity = (
            "additive"
            if old.get("x-orqetia-extensible-enum") is True
            else "breaking"
        )
        _append(
            changes,
            severity,
            location,
            f"enum values added: {', '.join(added)}",
        )


def compare_schema(
    old: object,
    new: object,
    *,
    location: str,
    changes: list[Change],
) -> None:
    if not isinstance(old, dict) or not isinstance(new, dict):
        if old != new:
            _append(changes, "breaking", location, "schema shape changed")
        return

    old_ref = old.get("$ref")
    new_ref = new.get("$ref")
    if old_ref != new_ref and (old_ref is not None or new_ref is not None):
        _append(changes, "breaking", location, "$ref changed")
        return

    for key in CONSTRAINT_KEYS:
        if old.get(key) != new.get(key):
            _append(changes, "breaking", f"{location}.{key}", "constraint changed")

    _enum_changes(old, new, location=location, changes=changes)

    old_required = {
        str(item) for item in old.get("required", []) if isinstance(item, str)
    }
    new_required = {
        str(item) for item in new.get("required", []) if isinstance(item, str)
    }
    if old_required != new_required:
        _append(
            changes,
            "breaking",
            f"{location}.required",
            "required-property set changed",
        )

    old_properties = old.get("properties", {})
    new_properties = new.get("properties", {})
    if isinstance(old_properties, dict) and isinstance(new_properties, dict):
        old_names = {str(name) for name in old_properties}
        new_names = {str(name) for name in new_properties}
        for name in sorted(old_names - new_names):
            _append(
                changes,
                "breaking",
                f"{location}.properties.{name}",
                "property removed",
            )
        for name in sorted(new_names - old_names):
            severity = "breaking" if name in new_required else "additive"
            _append(
                changes,
                severity,
                f"{location}.properties.{name}",
                "property added",
            )
        for name in sorted(old_names & new_names):
            compare_schema(
                old_properties[name],
                new_properties[name],
                location=f"{location}.properties.{name}",
                changes=changes,
            )

    for keyword in ("oneOf", "anyOf", "allOf"):
        old_value = old.get(keyword)
        new_value = new.get(keyword)
        if old_value != new_value and (old_value is not None or new_value is not None):
            _append(
                changes,
                "breaking",
                f"{location}.{keyword}",
                "composition changed",
            )


def _resolve_parameter(
    document: dict[str, Any],
    raw: object,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    reference = raw.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/parameters/"):
        name = reference.rsplit("/", 1)[-1]
        components = document.get("components", {})
        parameters = (
            components.get("parameters", {})
            if isinstance(components, dict)
            else {}
        )
        resolved = parameters.get(name) if isinstance(parameters, dict) else None
        return resolved if isinstance(resolved, dict) else None
    return raw


def _parameter_map(
    document: dict[str, Any],
    path_item: dict[str, Any],
    operation: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    raw_parameters: list[object] = []
    for owner in (path_item, operation):
        value = owner.get("parameters", [])
        if isinstance(value, list):
            raw_parameters.extend(value)
    for raw in raw_parameters:
        parameter = _resolve_parameter(document, raw)
        if parameter is None:
            continue
        name = parameter.get("name")
        location = parameter.get("in")
        if isinstance(name, str) and isinstance(location, str):
            output[(location, name)] = parameter
    return output


def _compare_parameters(
    old_document: dict[str, Any],
    new_document: dict[str, Any],
    old_path_item: dict[str, Any],
    new_path_item: dict[str, Any],
    old_operation: dict[str, Any],
    new_operation: dict[str, Any],
    *,
    location: str,
    changes: list[Change],
) -> None:
    old_parameters = _parameter_map(old_document, old_path_item, old_operation)
    new_parameters = _parameter_map(new_document, new_path_item, new_operation)

    for key in sorted(old_parameters.keys() - new_parameters.keys()):
        _append(
            changes,
            "breaking",
            f"{location}.parameters.{key[0]}.{key[1]}",
            "parameter removed",
        )
    for key in sorted(new_parameters.keys() - old_parameters.keys()):
        parameter = new_parameters[key]
        severity = "breaking" if parameter.get("required") is True else "additive"
        _append(
            changes,
            severity,
            f"{location}.parameters.{key[0]}.{key[1]}",
            "parameter added",
        )
    for key in sorted(old_parameters.keys() & new_parameters.keys()):
        old_parameter = old_parameters[key]
        new_parameter = new_parameters[key]
        if old_parameter.get("required") != new_parameter.get("required"):
            _append(
                changes,
                "breaking",
                f"{location}.parameters.{key[0]}.{key[1]}.required",
                "parameter required flag changed",
            )
        compare_schema(
            old_parameter.get("schema", {}),
            new_parameter.get("schema", {}),
            location=f"{location}.parameters.{key[0]}.{key[1]}.schema",
            changes=changes,
        )


def _content_schemas(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    content = value.get("content", {})
    if not isinstance(content, dict):
        return {}
    output: dict[str, object] = {}
    for media_type, media in content.items():
        if isinstance(media_type, str) and isinstance(media, dict):
            output[media_type] = media.get("schema", {})
    return output


def _compare_content(
    old: object,
    new: object,
    *,
    location: str,
    changes: list[Change],
) -> None:
    old_content = _content_schemas(old)
    new_content = _content_schemas(new)
    for media_type in sorted(old_content.keys() - new_content.keys()):
        _append(
            changes,
            "breaking",
            f"{location}.{media_type}",
            "media type removed",
        )
    for media_type in sorted(new_content.keys() - old_content.keys()):
        _append(
            changes,
            "additive",
            f"{location}.{media_type}",
            "media type added",
        )
    for media_type in sorted(old_content.keys() & new_content.keys()):
        compare_schema(
            old_content[media_type],
            new_content[media_type],
            location=f"{location}.{media_type}.schema",
            changes=changes,
        )


def _compare_operation(
    old_document: dict[str, Any],
    new_document: dict[str, Any],
    old_path_item: dict[str, Any],
    new_path_item: dict[str, Any],
    old_operation: dict[str, Any],
    new_operation: dict[str, Any],
    *,
    location: str,
    changes: list[Change],
) -> None:
    old_scopes = tuple(sorted(old_operation.get("x-orqetia-required-scopes", [])))
    new_scopes = tuple(sorted(new_operation.get("x-orqetia-required-scopes", [])))
    if old_scopes != new_scopes:
        _append(changes, "breaking", f"{location}.scopes", "required scopes changed")

    _compare_parameters(
        old_document,
        new_document,
        old_path_item,
        new_path_item,
        old_operation,
        new_operation,
        location=location,
        changes=changes,
    )

    old_body = old_operation.get("requestBody")
    new_body = new_operation.get("requestBody")
    if old_body is None and new_body is not None:
        severity = (
            "breaking"
            if isinstance(new_body, dict) and new_body.get("required") is True
            else "additive"
        )
        _append(changes, severity, f"{location}.requestBody", "request body added")
    elif old_body is not None and new_body is None:
        _append(changes, "breaking", f"{location}.requestBody", "request body removed")
    elif old_body is not None and new_body is not None:
        if (
            isinstance(old_body, dict)
            and isinstance(new_body, dict)
            and old_body.get("required") != new_body.get("required")
        ):
            _append(
                changes,
                "breaking",
                f"{location}.requestBody.required",
                "request body required flag changed",
            )
        _compare_content(
            old_body,
            new_body,
            location=f"{location}.requestBody.content",
            changes=changes,
        )

    old_responses = old_operation.get("responses", {})
    new_responses = new_operation.get("responses", {})
    if isinstance(old_responses, dict) and isinstance(new_responses, dict):
        for status in sorted(old_responses.keys() - new_responses.keys()):
            _append(
                changes,
                "breaking",
                f"{location}.responses.{status}",
                "response removed",
            )
        for status in sorted(new_responses.keys() - old_responses.keys()):
            _append(
                changes,
                "additive",
                f"{location}.responses.{status}",
                "response added",
            )
        for status in sorted(old_responses.keys() & new_responses.keys()):
            _compare_content(
                old_responses[status],
                new_responses[status],
                location=f"{location}.responses.{status}.content",
                changes=changes,
            )


def compare_openapi(
    old: dict[str, Any],
    new: dict[str, Any],
) -> list[Change]:
    changes: list[Change] = []
    old_paths = old.get("paths", {})
    new_paths = new.get("paths", {})
    if not isinstance(old_paths, dict) or not isinstance(new_paths, dict):
        return [Change("breaking", "paths", "OpenAPI paths must be objects")]

    old_operations: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    new_operations: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}

    for document, paths, output in (
        (old, old_paths, old_operations),
        (new, new_paths, new_operations),
    ):
        del document
        for path, raw_item in paths.items():
            if not isinstance(path, str) or not isinstance(raw_item, dict):
                continue
            for method in HTTP_METHODS:
                operation = raw_item.get(method)
                if isinstance(operation, dict):
                    output[(path, method)] = (raw_item, operation)

    for key in sorted(old_operations.keys() - new_operations.keys()):
        _append(
            changes,
            "breaking",
            f"{key[1].upper()} {key[0]}",
            "operation removed",
        )
    for key in sorted(new_operations.keys() - old_operations.keys()):
        severity = "additive" if key[0].startswith("/v1/") else "breaking"
        detail = (
            "operation added"
            if severity == "additive"
            else "public operation outside /v1 is prohibited"
        )
        _append(changes, severity, f"{key[1].upper()} {key[0]}", detail)

    for key in sorted(old_operations.keys() & new_operations.keys()):
        old_path_item, old_operation = old_operations[key]
        new_path_item, new_operation = new_operations[key]
        _compare_operation(
            old,
            new,
            old_path_item,
            new_path_item,
            old_operation,
            new_operation,
            location=f"{key[1].upper()} {key[0]}",
            changes=changes,
        )

    old_components = old.get("components", {})
    new_components = new.get("components", {})
    old_schemas = (
        old_components.get("schemas", {})
        if isinstance(old_components, dict)
        else {}
    )
    new_schemas = (
        new_components.get("schemas", {})
        if isinstance(new_components, dict)
        else {}
    )
    if isinstance(old_schemas, dict) and isinstance(new_schemas, dict):
        for name in sorted(old_schemas.keys() - new_schemas.keys()):
            _append(
                changes,
                "breaking",
                f"components.schemas.{name}",
                "schema removed",
            )
        for name in sorted(new_schemas.keys() - old_schemas.keys()):
            _append(
                changes,
                "additive",
                f"components.schemas.{name}",
                "schema added",
            )
        for name in sorted(old_schemas.keys() & new_schemas.keys()):
            compare_schema(
                old_schemas[name],
                new_schemas[name],
                location=f"components.schemas.{name}",
                changes=changes,
            )

    return sorted(set(changes))


def compare_json_schema(
    old: dict[str, Any],
    new: dict[str, Any],
) -> list[Change]:
    changes: list[Change] = []
    compare_schema(old, new, location="$", changes=changes)
    return sorted(set(changes))


def _classification(changes: list[Change]) -> str:
    if any(item.severity == "breaking" for item in changes):
        return "breaking"
    if any(item.severity == "additive" for item in changes):
        return "additive"
    return "compatible"


def _fingerprint(
    *,
    contract: str,
    old: dict[str, Any],
    new: dict[str, Any],
    changes: list[Change],
) -> str:
    payload = {
        "contract": contract,
        "old_sha256": _sha256(old),
        "new_sha256": _sha256(new),
        "breaking": [
            {
                "location": item.location,
                "detail": item.detail,
            }
            for item in changes
            if item.severity == "breaking"
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _approved(
    *,
    approvals_path: Path,
    contract: str,
    fingerprint: str,
) -> tuple[bool, str | None]:
    data = _load(approvals_path)
    approvals = data.get("approvals", [])
    if not isinstance(approvals, list):
        raise ValueError("approvals must be an array")
    for raw in approvals:
        if not isinstance(raw, dict):
            continue
        if raw.get("contract") != contract or raw.get("fingerprint") != fingerprint:
            continue
        issue = raw.get("issue")
        reason = raw.get("reason")
        if (
            isinstance(issue, str)
            and APPROVAL_ISSUE.fullmatch(issue)
            and isinstance(reason, str)
            and len(reason.strip()) >= 12
        ):
            return True, issue
    return False, None


def check(
    *,
    kind: str,
    old_path: Path,
    new_path: Path,
    contract: str,
    approvals_path: Path,
) -> int:
    old = _load(old_path)
    new = _load(new_path)
    changes = (
        compare_openapi(old, new)
        if kind == "openapi"
        else compare_json_schema(old, new)
    )
    classification = _classification(changes)
    print(
        f"contract={contract} classification={classification} "
        f"changes={len(changes)}"
    )
    for item in changes:
        print(f"- {item.severity}: {item.location}: {item.detail}")

    if classification != "breaking":
        return 0

    fingerprint = _fingerprint(
        contract=contract,
        old=old,
        new=new,
        changes=changes,
    )
    print(f"breaking_fingerprint={fingerprint}")
    approved, issue = _approved(
        approvals_path=approvals_path,
        contract=contract,
        fingerprint=fingerprint,
    )
    if approved:
        print(f"breaking_approval={issue}")
        return 0
    print("breaking_approval=missing")
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("check", choices=("check",))
    parser.add_argument("--kind", choices=("openapi", "json-schema"), required=True)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--approvals", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return check(
        kind=args.kind,
        old_path=args.old,
        new_path=args.new,
        contract=args.contract,
        approvals_path=args.approvals,
    )


if __name__ == "__main__":
    raise SystemExit(main())
