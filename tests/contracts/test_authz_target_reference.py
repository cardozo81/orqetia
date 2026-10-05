from __future__ import annotations

import unittest

from tests.contracts.authz_target_reference import (
    Principal,
    authorize_backoffice_action,
    authorize_task_create,
    client_task_view,
)
from tests.contracts.tenancy_target_reference import ClientEnvelope, Mode


class ExplicitTargetAuthorizationTests(unittest.TestCase):
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

    def principal(self, *scopes: str, tenant: str = "tenant-a", client: str = "client-a") -> Principal:
        return Principal("SERVICE_CLIENT", tenant, client, frozenset(scopes))

    def test_auto_requires_tasks_write_but_not_tasks_target(self) -> None:
        result = authorize_task_create(
            principal=self.principal("tasks:write"),
            envelope=self.envelope,
            request={},
        )
        self.assertEqual(result.requested_execution_mode, Mode.AUTO)

    def test_explicit_target_requires_tasks_target(self) -> None:
        with self.assertRaises(PermissionError):
            authorize_task_create(
                principal=self.principal("tasks:write"),
                envelope=self.envelope,
                request={"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}}},
            )

    def test_explicit_target_with_scope_and_entitlement_is_allowed(self) -> None:
        result = authorize_task_create(
            principal=self.principal("tasks:write", "tasks:target"),
            envelope=self.envelope,
            request={"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}}},
        )
        self.assertEqual(result.requested_execution_mode, Mode.EXPLICIT_TARGET)
        self.assertEqual(result.effective_target.provider_id, "OPENAI")  # type: ignore[union-attr]

    def test_tasks_target_does_not_replace_tasks_write(self) -> None:
        with self.assertRaises(PermissionError):
            authorize_task_create(
                principal=self.principal("tasks:target"),
                envelope=self.envelope,
                request={"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}}},
            )

    def test_scope_does_not_bypass_provider_entitlement(self) -> None:
        with self.assertRaises(PermissionError):
            authorize_task_create(
                principal=self.principal("tasks:write", "tasks:target"),
                envelope=self.envelope,
                request={"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "ANTHROPIC"}}},
            )

    def test_cross_tenant_bola_is_denied(self) -> None:
        with self.assertRaises(PermissionError):
            authorize_task_create(
                principal=self.principal("tasks:write", "tasks:target", tenant="tenant-b"),
                envelope=self.envelope,
                request={"execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}}},
            )

    def test_client_cannot_elevate_admin_limits(self) -> None:
        with self.assertRaises(PermissionError):
            authorize_task_create(
                principal=self.principal("tasks:write", "tasks:target"),
                envelope=self.envelope,
                request={
                    "max_cycles": 99,
                    "execution": {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}},
                },
            )

    def test_client_task_dto_has_no_provider_financial_or_secret_fields(self) -> None:
        view = client_task_view(
            task_id="task-1",
            requested_execution_mode="EXPLICIT_TARGET",
            requested_target={"provider": "OPENAI"},
            effective_target={"provider": "OPENAI", "model": "gpt-approved", "reasoning_profile": "MEDIUM"},
            attempt_id="attempt-1",
        )
        forbidden = {
            "provider_secret",
            "provider_credential_id",
            "provider_cost",
            "currency",
            "credit_balance",
        }
        self.assertTrue(forbidden.isdisjoint(view))

    def test_service_client_cannot_claim_backoffice_privilege(self) -> None:
        with self.assertRaises(PermissionError):
            authorize_backoffice_action(
                self.principal("tasks:write", "policies:admin"),
                "policies:admin",
            )


if __name__ == "__main__":
    unittest.main()
