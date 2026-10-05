from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json"
EXECUTION_SCHEMA_PATH = ROOT / "contracts" / "execution" / "task-execution-selection.schema.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def property_names(schema: object) -> set[str]:
    names: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                names.update(str(key) for key in properties)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(schema)
    return names


class OpenApiV1ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = load_json(OPENAPI_PATH)
        cls.execution = load_json(EXECUTION_SCHEMA_PATH)
        cls.schemas = cls.spec["components"]["schemas"]

    def test_openapi_is_versioned_client_contract(self) -> None:
        self.assertEqual(self.spec["openapi"], "3.1.0")
        self.assertEqual(self.spec["info"]["title"], "ORQETIA Client API")
        self.assertIn("development", self.spec["info"]["version"])

    def test_required_v1_paths_exist(self) -> None:
        required = {
            "/v1/sessions",
            "/v1/sessions/{session_id}",
            "/v1/sessions/{session_id}/tasks",
            "/v1/tasks/{task_id}",
            "/v1/tasks/{task_id}/result",
            "/v1/tasks/{task_id}/cancel",
            "/v1/tasks/{task_id}/attempts",
            "/v1/tasks/{task_id}/attempts/{attempt_id}/exchanges",
            "/v1/estimates",
            "/v1/providers",
            "/v1/models",
            "/v1/usage",
        }
        self.assertTrue(required.issubset(self.spec["paths"]))

    def test_client_spec_contains_no_admin_surface(self) -> None:
        self.assertFalse(any(path.startswith("/admin/") for path in self.spec["paths"]))
        self.assertNotIn("/v1/operations", self.spec["paths"])

    def test_task_execution_is_optional_and_auto_default_is_canonical(self) -> None:
        task = self.schemas["TaskCreateRequest"]
        self.assertNotIn("execution", task["required"])
        self.assertEqual(
            task["properties"]["execution"]["$ref"],
            "#/components/schemas/ExecutionSelection",
        )
        self.assertEqual(
            self.schemas["ExecutionSelection"]["$ref"],
            "../execution/task-execution-selection.schema.json",
        )
        modes = {
            branch["properties"]["mode"]["const"]
            for branch in self.execution["oneOf"]
        }
        self.assertIn("AUTO", modes)

    def test_explicit_target_requires_conditional_scope(self) -> None:
        op = self.spec["paths"]["/v1/sessions/{session_id}/tasks"]["post"]
        self.assertEqual(op["x-orqetia-required-scopes"], ["tasks:write"])
        self.assertIn(
            {"when": "execution.mode == EXPLICIT_TARGET", "required": ["tasks:target"]},
            op["x-orqetia-conditional-scopes"],
        )
        self.assertIn("tenant/client", op["x-orqetia-object-ownership"])

    def test_task_create_is_async_and_idempotent(self) -> None:
        op = self.spec["paths"]["/v1/sessions/{session_id}/tasks"]["post"]
        refs = [item.get("$ref") for item in op["parameters"] if isinstance(item, dict)]
        self.assertIn("#/components/parameters/IdempotencyKey", refs)
        self.assertIn("202", op["responses"])
        self.assertIn("Location", op["responses"]["202"]["headers"])

    def test_attempt_identity_is_first_class_and_task_can_be_null(self) -> None:
        attempt = self.schemas["AttemptView"]
        self.assertIn("attempt_id", attempt["required"])
        self.assertIn("operation", attempt["required"])
        self.assertNotIn("task_id", attempt["required"])
        task_id = attempt["properties"]["task_id"]["oneOf"]
        self.assertIn({"type": "null"}, task_id)
        self.assertNotIn("request_fingerprint", attempt["required"])

    def test_task_result_exposes_attempt_ids_not_financials(self) -> None:
        result = self.schemas["TaskResultView"]
        self.assertIn("attempt_ids", result["required"])
        self.assertNotIn("provider_cost", result["properties"])
        self.assertNotIn("currency", result["properties"])

    def test_exchange_evidence_preserves_sanitized_raw_boundary(self) -> None:
        evidence = self.schemas["SanitizedEvidence"]
        self.assertIn("sanitized_raw_body", evidence["required"])
        self.assertIn("sanitized_sha256", evidence["required"])
        exchange = self.schemas["ExchangeEvidenceView"]
        self.assertIn("attempt_id", exchange["required"])
        self.assertIn("provider", exchange["required"])

    def test_provider_identity_is_separate_from_display_name(self) -> None:
        provider = self.schemas["ProviderIdentity"]
        self.assertEqual(provider["required"], ["provider_id", "provider_name"])
        self.assertIn("Provider Registry identity", provider["properties"]["provider_id"]["description"])

    def test_client_schema_has_no_provider_financial_or_secret_fields(self) -> None:
        forbidden = {
            "provider_cost",
            "observed_provider_cost",
            "estimated_provider_cost",
            "currency",
            "unit_price",
            "cost_per_token",
            "client_charge",
            "credit_balance",
            "provider_credential_id",
            "provider_credential_fingerprint",
            "provider_secret",
        }
        names = property_names(self.schemas)
        self.assertEqual(forbidden.intersection(names), set())

    def test_usage_is_technical_only(self) -> None:
        usage = self.schemas["TechnicalUsage"]["properties"]
        self.assertIn("total_tokens", usage)
        self.assertIn("native_usage", usage)
        self.assertNotIn("cost", usage)
        self.assertNotIn("currency", usage)

    def test_error_contract_covers_security_idempotency_limits_and_validation(self) -> None:
        task = self.spec["paths"]["/v1/sessions/{session_id}/tasks"]["post"]["responses"]
        for status in ("401", "403", "409", "413", "422", "429"):
            self.assertIn(status, task)
        error = self.schemas["ErrorEnvelope"]
        self.assertEqual(
            set(error["required"]),
            {"code", "message", "correlation_id"},
        )

    def test_catalog_endpoints_are_client_envelope_filtered(self) -> None:
        providers = self.spec["paths"]["/v1/providers"]["get"]
        models = self.spec["paths"]["/v1/models"]["get"]
        self.assertIn("visible envelope", providers["x-orqetia-object-ownership"])
        self.assertIn("visible envelope", models["x-orqetia-object-ownership"])

    def test_raw_evidence_path_is_task_owned(self) -> None:
        op = self.spec["paths"]["/v1/tasks/{task_id}/attempts/{attempt_id}/exchanges"]["get"]
        self.assertEqual(op["x-orqetia-required-scopes"], ["tasks:read"])
        self.assertIn("task/attempt", op["x-orqetia-object-ownership"])


if __name__ == "__main__":
    unittest.main()
