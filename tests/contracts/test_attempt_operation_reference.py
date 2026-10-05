from __future__ import annotations

import unittest

from tests.contracts.attempt_operation_reference import (
    AttemptFactory,
    LegacyCandidate,
    OperationContract,
    bind_exchange,
    diagnostic_for,
    pricing_for,
    reconcile_legacy_by_exact_fingerprint,
    usage_for,
)


class AttemptOperationCarryoverTests(unittest.TestCase):
    def test_one_dispatch_has_one_stable_attempt_id(self) -> None:
        factory = AttemptFactory(("attempt-1",))
        attempt = factory.create(
            contract=OperationContract("TASK_EXECUTION"),
            provider_id="OPENAI",
            model_id="gpt-test",
            request_fingerprint="sha256:same",
            task_id="task-1",
            session_id="session-1",
        )
        self.assertEqual(attempt.attempt_id, "attempt-1")
        self.assertEqual(bind_exchange(attempt, exchange_id="exchange-1").attempt_id, "attempt-1")

    def test_usage_pricing_diagnostic_share_attempt_id(self) -> None:
        factory = AttemptFactory(("attempt-2",))
        attempt = factory.create(
            contract=OperationContract("TASK_EXECUTION"),
            provider_id="OPENAI",
            model_id="gpt-test",
            request_fingerprint="sha256:x",
            task_id="task-1",
            session_id="session-1",
        )
        self.assertEqual(usage_for(attempt, total_tokens=15).attempt_id, attempt.attempt_id)
        self.assertEqual(pricing_for(attempt, basis="ESTIMATED").attempt_id, attempt.attempt_id)
        self.assertEqual(diagnostic_for(attempt, error_class=None).attempt_id, attempt.attempt_id)

    def test_retry_same_payload_gets_new_attempt_id(self) -> None:
        factory = AttemptFactory(("attempt-1", "attempt-2"))
        contract = OperationContract("TASK_EXECUTION")
        first = factory.create(
            contract=contract,
            provider_id="OPENAI",
            model_id="gpt-test",
            request_fingerprint="sha256:identical",
            task_id="task-1",
            session_id="session-1",
        )
        retry = factory.create(
            contract=contract,
            provider_id="OPENAI",
            model_id="gpt-test",
            request_fingerprint=first.request_fingerprint,
            task_id="task-1",
            session_id="session-1",
            retry_of_attempt_id=first.attempt_id,
        )
        self.assertEqual(first.request_fingerprint, retry.request_fingerprint)
        self.assertNotEqual(first.attempt_id, retry.attempt_id)
        self.assertEqual(retry.retry_of_attempt_id, first.attempt_id)

    def test_fallback_gets_new_attempt_id(self) -> None:
        factory = AttemptFactory(("attempt-a", "attempt-b"))
        contract = OperationContract("TASK_EXECUTION")
        first = factory.create(
            contract=contract,
            provider_id="OPENAI",
            model_id="gpt-test",
            request_fingerprint="sha256:same",
            task_id="task-1",
            session_id="session-1",
        )
        fallback = factory.create(
            contract=contract,
            provider_id="ANTHROPIC",
            model_id="claude-test",
            request_fingerprint="sha256:same",
            task_id="task-1",
            session_id="session-1",
            fallback_from_attempt_id=first.attempt_id,
        )
        self.assertNotEqual(first.attempt_id, fallback.attempt_id)
        self.assertEqual(fallback.fallback_from_attempt_id, first.attempt_id)

    def test_taskless_operation_is_valid_when_contract_allows_it(self) -> None:
        factory = AttemptFactory(("attempt-taskless",))
        attempt = factory.create(
            contract=OperationContract(
                "PROVIDER_OPERATION",
                task_required=False,
                session_required=False,
            ),
            provider_id="GEMINI",
            model_id="gemini-test",
            request_fingerprint="sha256:taskless",
        )
        self.assertEqual(attempt.operation, "PROVIDER_OPERATION")
        self.assertIsNone(attempt.task_id)
        self.assertIsNone(attempt.session_id)

    def test_taskless_attempt_requires_explicit_operation(self) -> None:
        with self.assertRaises(ValueError):
            OperationContract("", task_required=False, session_required=False)

    def test_task_required_contract_rejects_missing_task(self) -> None:
        factory = AttemptFactory(("attempt-x",))
        with self.assertRaises(ValueError):
            factory.create(
                contract=OperationContract("TASK_EXECUTION"),
                provider_id="OPENAI",
                model_id="gpt-test",
                request_fingerprint="sha256:x",
                session_id="session-1",
            )

    def test_new_exchange_binding_uses_explicit_attempt_not_metadata_guessing(self) -> None:
        factory = AttemptFactory(("attempt-1", "attempt-2"))
        contract = OperationContract("TASK_EXECUTION")
        first = factory.create(
            contract=contract,
            provider_id="OPENAI",
            model_id="gpt-test",
            request_fingerprint="sha256:identical",
            task_id="task-1",
            session_id="session-1",
        )
        second = factory.create(
            contract=contract,
            provider_id="OPENAI",
            model_id="gpt-test",
            request_fingerprint="sha256:identical",
            task_id="task-1",
            session_id="session-1",
            retry_of_attempt_id=first.attempt_id,
        )
        self.assertEqual(bind_exchange(first, exchange_id="e1").attempt_id, "attempt-1")
        self.assertEqual(bind_exchange(second, exchange_id="e2").attempt_id, "attempt-2")

    def test_ambiguous_legacy_fingerprint_stays_unresolved(self) -> None:
        candidates = (
            LegacyCandidate("attempt-1", "sha256:same"),
            LegacyCandidate("attempt-2", "sha256:same"),
        )
        self.assertIsNone(reconcile_legacy_by_exact_fingerprint("sha256:same", candidates))

    def test_unique_legacy_fingerprint_is_import_only_fallback(self) -> None:
        candidates = (
            LegacyCandidate("attempt-1", "sha256:one"),
            LegacyCandidate("attempt-2", "sha256:two"),
        )
        self.assertEqual(
            reconcile_legacy_by_exact_fingerprint("sha256:two", candidates),
            "attempt-2",
        )

    def test_generic_core_has_no_round_requirement(self) -> None:
        factory = AttemptFactory(("attempt-no-round",))
        attempt = factory.create(
            contract=OperationContract("TASK_EXECUTION"),
            provider_id="MISTRAL",
            model_id="mistral-test",
            request_fingerprint="sha256:x",
            task_id="task-1",
            session_id="session-1",
        )
        self.assertFalse(hasattr(attempt, "round_id"))


if __name__ == "__main__":
    unittest.main()
