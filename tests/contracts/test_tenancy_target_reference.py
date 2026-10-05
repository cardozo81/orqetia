from __future__ import annotations

import unittest

from tests.contracts.tenancy_target_reference import (
    ClientEnvelope,
    Mode,
    Target,
    resolve_selection,
)


class TenancyTargetEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.envelope = ClientEnvelope(
            tenant_id="tenant-a",
            client_id="client-a",
            policy_version_id="policy-v7",
            explicit_target_enabled=True,
            allowed_providers=frozenset({"OPENAI"}),
            allowed_models=frozenset({("OPENAI", "gpt-approved")}),
            allowed_profiles=frozenset({("OPENAI", "gpt-approved", "MEDIUM")}),
            default_model_by_provider={"OPENAI": "gpt-approved"},
            default_profile_by_model={("OPENAI", "gpt-approved"): "MEDIUM"},
            max_cycles=3,
            timeout_seconds=60,
        )

    def test_omitted_target_defaults_to_auto(self) -> None:
        result = resolve_selection(
            {},
            authenticated_tenant_id="tenant-a",
            authenticated_client_id="client-a",
            envelope=self.envelope,
        )
        self.assertEqual(result.requested_execution_mode, Mode.AUTO)
        self.assertIsNone(result.requested_target)
        self.assertIsNone(result.effective_target)

    def test_explicit_partial_target_resolves_authorized_defaults(self) -> None:
        result = resolve_selection(
            {"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}}},
            authenticated_tenant_id="tenant-a",
            authenticated_client_id="client-a",
            envelope=self.envelope,
        )
        self.assertEqual(result.requested_execution_mode, Mode.EXPLICIT_TARGET)
        self.assertEqual(result.effective_target, Target("OPENAI", "gpt-approved", "MEDIUM"))

    def test_provider_outside_envelope_is_denied(self) -> None:
        with self.assertRaises(PermissionError):
            resolve_selection(
                {"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "ANTHROPIC"}}},
                authenticated_tenant_id="tenant-a",
                authenticated_client_id="client-a",
                envelope=self.envelope,
            )

    def test_model_outside_envelope_is_denied(self) -> None:
        with self.assertRaises(PermissionError):
            resolve_selection(
                {"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI", "model": "gpt-other"}}},
                authenticated_tenant_id="tenant-a",
                authenticated_client_id="client-a",
                envelope=self.envelope,
            )

    def test_profile_outside_envelope_is_denied(self) -> None:
        with self.assertRaises(PermissionError):
            resolve_selection(
                {"execution": {"mode": "EXPLICIT_TARGET", "target": {
                    "provider": "OPENAI",
                    "model": "gpt-approved",
                    "reasoning_profile": "HIGH",
                }}},
                authenticated_tenant_id="tenant-a",
                authenticated_client_id="client-a",
                envelope=self.envelope,
            )

    def test_cross_tenant_or_client_scope_is_denied(self) -> None:
        with self.assertRaises(PermissionError):
            resolve_selection(
                {},
                authenticated_tenant_id="tenant-b",
                authenticated_client_id="client-a",
                envelope=self.envelope,
            )

    def test_client_cannot_override_admin_cycles_or_timeout(self) -> None:
        for key, value in (("max_cycles", 99), ("timeout_seconds", 999)):
            with self.subTest(key=key):
                with self.assertRaises(PermissionError):
                    resolve_selection(
                        {key: value},
                        authenticated_tenant_id="tenant-a",
                        authenticated_client_id="client-a",
                        envelope=self.envelope,
                    )

    def test_explicit_target_keeps_administrative_limits(self) -> None:
        result = resolve_selection(
            {"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}}},
            authenticated_tenant_id="tenant-a",
            authenticated_client_id="client-a",
            envelope=self.envelope,
        )
        self.assertEqual(result.policy_version_id, "policy-v7")
        self.assertEqual(result.max_cycles, 3)
        self.assertEqual(result.timeout_seconds, 60)

    def test_explicit_target_can_be_disabled_by_policy(self) -> None:
        disabled = ClientEnvelope(
            **{**self.envelope.__dict__, "explicit_target_enabled": False}
        )
        with self.assertRaises(PermissionError):
            resolve_selection(
                {"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}}},
                authenticated_tenant_id="tenant-a",
                authenticated_client_id="client-a",
                envelope=disabled,
            )

    def test_later_default_change_does_not_mutate_existing_snapshot(self) -> None:
        first = resolve_selection(
            {"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}}},
            authenticated_tenant_id="tenant-a",
            authenticated_client_id="client-a",
            envelope=self.envelope,
        )
        self.assertEqual(first.effective_target, Target("OPENAI", "gpt-approved", "MEDIUM"))
        self.assertEqual(first.policy_version_id, "policy-v7")


if __name__ == "__main__":
    unittest.main()
