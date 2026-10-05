from __future__ import annotations

import unittest

from tests.contracts.attempt_operation_reference import (
    AttemptFactory,
    OperationContract,
    bind_exchange,
    pricing_for,
    usage_for,
)
from tests.contracts.exchange_evidence_reference import (
    ExchangeRecord,
    ProviderIdentity,
    raw_api_dto,
    sanitize_for_persistence,
)
from tests.contracts.execution_target_reference import (
    Candidate,
    Target,
    rank_auto,
)


class CanonicalRevalidationIntegrationTests(unittest.TestCase):
    def test_explicit_retry_keeps_target_but_uses_new_attempt_identity(self) -> None:
        target = Target("OPENAI", "gpt-approved", "MEDIUM")
        factory = AttemptFactory(("attempt-1", "attempt-2"))
        contract = OperationContract("TASK_EXECUTION")

        first = factory.create(
            contract=contract,
            provider_id=target.provider,
            model_id=target.model,
            request_fingerprint="sha256:same",
            task_id="task-1",
            session_id="session-1",
        )
        retry = factory.create(
            contract=contract,
            provider_id=target.provider,
            model_id=target.model,
            request_fingerprint="sha256:same",
            task_id="task-1",
            session_id="session-1",
            retry_of_attempt_id=first.attempt_id,
        )

        self.assertEqual((first.provider_id, first.model_id), (retry.provider_id, retry.model_id))
        self.assertNotEqual(first.attempt_id, retry.attempt_id)
        self.assertEqual(first.request_fingerprint, retry.request_fingerprint)
        self.assertEqual(retry.retry_of_attempt_id, first.attempt_id)

    def test_auto_fallback_uses_ranked_targets_and_distinct_attempt_ids(self) -> None:
        cheap = Candidate(
            Target("OPENAI", "cheap", "MEDIUM"),
            "MONEY:USD",
            10,
            0.10,
            "USD",
            10,
        )
        expensive = Candidate(
            Target("ANTHROPIC", "expensive", "DEFAULT"),
            "MONEY:USD",
            10,
            0.30,
            "USD",
            20,
        )
        ranked = rank_auto((expensive, cheap))
        self.assertEqual(tuple(c.target.provider for c in ranked), ("OPENAI", "ANTHROPIC"))

        factory = AttemptFactory(("attempt-cheap", "attempt-fallback"))
        contract = OperationContract("TASK_EXECUTION")
        first = factory.create(
            contract=contract,
            provider_id=ranked[0].target.provider,
            model_id=ranked[0].target.model,
            request_fingerprint="sha256:same",
            task_id="task-1",
            session_id="session-1",
        )
        fallback = factory.create(
            contract=contract,
            provider_id=ranked[1].target.provider,
            model_id=ranked[1].target.model,
            request_fingerprint="sha256:same",
            task_id="task-1",
            session_id="session-1",
            fallback_from_attempt_id=first.attempt_id,
        )

        self.assertNotEqual(first.attempt_id, fallback.attempt_id)
        self.assertEqual(fallback.fallback_from_attempt_id, first.attempt_id)
        self.assertEqual(first.request_fingerprint, fallback.request_fingerprint)

    def test_attempt_exchange_usage_pricing_share_identity_across_boundaries(self) -> None:
        factory = AttemptFactory(("attempt-correlated",))
        attempt = factory.create(
            contract=OperationContract("TASK_EXECUTION"),
            provider_id="OPENAI",
            model_id="gpt-approved",
            request_fingerprint="sha256:payload",
            task_id="task-1",
            session_id="session-1",
        )

        exchange = bind_exchange(attempt, exchange_id="exchange-1")
        usage = usage_for(attempt, total_tokens=25)
        pricing = pricing_for(attempt, basis="ESTIMATED")

        self.assertEqual(
            {exchange.attempt_id, usage.attempt_id, pricing.attempt_id},
            {attempt.attempt_id},
        )

    def test_sanitized_evidence_exposes_same_attempt_and_raw_semantics(self) -> None:
        request = sanitize_for_persistence(
            '{"token":"SECRET","operation":"SEMANTIC_READINESS"}',
            secrets=("SECRET",),
        )
        exchange = ExchangeRecord(
            exchange_id="exchange-1",
            attempt_id="attempt-1",
            provider=ProviderIdentity("OPENAI", "OpenAI"),
            operation="TASK_EXECUTION",
            status="SUCCEEDED",
            request=request,
            response=None,
        )

        dto = raw_api_dto(exchange)
        evidence = dto["request_evidence"]
        assert isinstance(evidence, dict)

        self.assertEqual(dto["attempt_id"], "attempt-1")
        self.assertEqual(evidence["sanitized_raw_body"], request.body)
        self.assertIn("SEMANTIC_READINESS", str(evidence["sanitized_raw_body"]))
        self.assertNotIn('"SECRET"', str(evidence["sanitized_raw_body"]))

    def test_taskless_operation_remains_governed_and_correlated(self) -> None:
        factory = AttemptFactory(("attempt-taskless",))
        attempt = factory.create(
            contract=OperationContract(
                "PROVIDER_OPERATION",
                task_required=False,
                session_required=False,
            ),
            provider_id="MISTRAL",
            model_id="mistral-approved",
            request_fingerprint="sha256:taskless",
        )

        exchange = bind_exchange(attempt, exchange_id="exchange-taskless")
        usage = usage_for(attempt, total_tokens=7)

        self.assertEqual(attempt.operation, "PROVIDER_OPERATION")
        self.assertIsNone(attempt.task_id)
        self.assertIsNone(attempt.session_id)
        self.assertEqual(exchange.attempt_id, attempt.attempt_id)
        self.assertEqual(usage.attempt_id, attempt.attempt_id)


if __name__ == "__main__":
    unittest.main()
