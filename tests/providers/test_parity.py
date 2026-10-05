from __future__ import annotations

import unittest
from dataclasses import fields

from orqetia.providers import (
    CANONICAL_PROVIDER_PORT_PLAN,
    INITIAL_PROVIDER_TAXONOMY,
    MANDATORY_ADAPTER_PARITY_GATES,
    AdapterParityGate,
    AdapterTransportFamily,
    ProviderPortPlan,
    provider_port_plan,
)


class ProviderAdapterParityPlanTests(unittest.TestCase):
    def test_plan_covers_initial_provider_taxonomy_exactly_once(self) -> None:
        display_names = tuple(plan.display_name for plan in CANONICAL_PROVIDER_PORT_PLAN)
        self.assertEqual(len(display_names), len(INITIAL_PROVIDER_TAXONOMY))
        self.assertEqual(set(display_names), set(INITIAL_PROVIDER_TAXONOMY))

        provider_ids = tuple(plan.provider_id for plan in CANONICAL_PROVIDER_PORT_PLAN)
        self.assertEqual(len(provider_ids), len(set(provider_ids)))

    def test_port_order_is_total_and_deterministic(self) -> None:
        self.assertEqual(
            tuple(plan.order for plan in CANONICAL_PROVIDER_PORT_PLAN),
            tuple(range(1, len(CANONICAL_PROVIDER_PORT_PLAN) + 1)),
        )

    def test_responses_family_is_ported_first_for_reuse(self) -> None:
        first_family = CANONICAL_PROVIDER_PORT_PLAN[:4]
        self.assertEqual(
            tuple(plan.provider_id for plan in first_family),
            ("openai", "deepseek", "mimo", "xai"),
        )
        self.assertTrue(
            all(
                plan.transport_family is AdapterTransportFamily.RESPONSES_HTTP
                for plan in first_family
            )
        )

    def test_chat_completions_family_is_grouped_after_responses(self) -> None:
        chat_family = CANONICAL_PROVIDER_PORT_PLAN[4:7]
        self.assertEqual(
            tuple(plan.provider_id for plan in chat_family),
            ("qwen", "mistral", "kimi"),
        )
        self.assertTrue(
            all(
                plan.transport_family is AdapterTransportFamily.CHAT_COMPLETIONS_HTTP
                for plan in chat_family
            )
        )

    def test_native_and_sdk_transports_remain_explicit(self) -> None:
        expected = {
            "gemini": AdapterTransportFamily.GEMINI_INTERACTIONS_HTTP,
            "anthropic": AdapterTransportFamily.ANTHROPIC_MESSAGES_HTTP,
            "cohere": AdapterTransportFamily.COHERE_CHAT_V2_HTTP,
            "copilot": AdapterTransportFamily.COPILOT_SDK,
        }
        self.assertEqual(
            {
                plan.provider_id: plan.transport_family
                for plan in CANONICAL_PROVIDER_PORT_PLAN
                if plan.provider_id in expected
            },
            expected,
        )

    def test_every_provider_requires_complete_parity_gate(self) -> None:
        for plan in CANONICAL_PROVIDER_PORT_PLAN:
            with self.subTest(provider=plan.provider_id):
                self.assertEqual(plan.required_gates, MANDATORY_ADAPTER_PARITY_GATES)

    def test_mandatory_gate_preserves_single_attempt_orchestration_boundary(self) -> None:
        self.assertIn(AdapterParityGate.SINGLE_ATTEMPT, MANDATORY_ADAPTER_PARITY_GATES)
        self.assertIn(
            AdapterParityGate.NO_RETRY_ORCHESTRATION,
            MANDATORY_ADAPTER_PARITY_GATES,
        )
        self.assertIn(
            AdapterParityGate.AMBIGUOUS_EFFECT_BOUNDARY,
            MANDATORY_ADAPTER_PARITY_GATES,
        )

    def test_mandatory_gate_keeps_ci_offline_and_secret_safe(self) -> None:
        self.assertIn(
            AdapterParityGate.OFFLINE_CONTRACT_TESTS,
            MANDATORY_ADAPTER_PARITY_GATES,
        )
        self.assertIn(AdapterParityGate.NO_PAID_CI, MANDATORY_ADAPTER_PARITY_GATES)
        self.assertIn(
            AdapterParityGate.NO_SECRET_CAPTURE,
            MANDATORY_ADAPTER_PARITY_GATES,
        )

    def test_parity_plan_contains_no_runtime_credentials_endpoints_or_costs(self) -> None:
        names = {field.name for field in fields(ProviderPortPlan)}
        forbidden = {
            "api_key",
            "credential",
            "secret",
            "endpoint",
            "balance",
            "cost",
            "price",
            "pricing",
        }
        self.assertTrue(names.isdisjoint(forbidden))

    def test_lookup_is_canonical_and_case_insensitive(self) -> None:
        self.assertEqual(provider_port_plan(" OpenAI ").provider_id, "openai")
        self.assertEqual(provider_port_plan("COPILOT").order, 11)

    def test_unknown_provider_plan_fails_closed(self) -> None:
        with self.assertRaisesRegex(KeyError, "unknown provider port plan"):
            provider_port_plan("perplexity")

    def test_plan_rejects_missing_mandatory_gate(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing mandatory parity gates"):
            ProviderPortPlan(
                order=1,
                provider_id="test",
                display_name="Test",
                transport_family=AdapterTransportFamily.RESPONSES_HTTP,
                required_gates=frozenset({AdapterParityGate.SINGLE_ATTEMPT}),
            )


if __name__ == "__main__":
    unittest.main()
