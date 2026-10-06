from __future__ import annotations

import json
from dataclasses import MISSING, fields
from pathlib import Path

from orqetia.shared.messaging import DataClassification, EventEnvelope

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "events" / "envelope" / "v1.schema.json"


def test_event_envelope_schema_matches_runtime_shape() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = set(schema["properties"])
    runtime_fields = {item.name for item in fields(EventEnvelope)}

    assert schema["additionalProperties"] is False
    assert properties == runtime_fields

    runtime_required = {
        item.name
        for item in fields(EventEnvelope)
        if item.default is MISSING and item.default_factory is MISSING
    }
    assert set(schema["required"]) == runtime_required


def test_event_envelope_schema_preserves_security_and_version_invariants() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    classifications = set(
        schema["properties"]["data_classification"]["enum"]
    )

    assert "SECRET" not in classifications
    assert classifications == {
        item.value
        for item in DataClassification
        if item is not DataClassification.SECRET
    }
    assert schema["properties"]["event_version"]["minimum"] == 1

    client_private = schema["allOf"][0]
    assert client_private["if"]["properties"]["data_classification"]["const"] == (
        "CLIENT_PRIVATE"
    )
    assert set(client_private["then"]["required"]) == {
        "tenant_id",
        "client_id",
    }
