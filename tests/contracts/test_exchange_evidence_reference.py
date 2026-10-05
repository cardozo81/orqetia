from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from hashlib import sha256

from tests.contracts.exchange_evidence_reference import (
    ExchangeRecord,
    ProviderIdentity,
    forbidden_client_fields,
    html_transport,
    humanize_status,
    provider_display,
    raw_api_dto,
    recover_html_transport,
    sanitize_for_persistence,
)


class ExchangeEvidenceCarryoverTests(unittest.TestCase):
    def _exchange(self) -> ExchangeRecord:
        request = sanitize_for_persistence(
            '{"token":"SECRET-123","profile":"SEMANTIC_READINESS","raw":"<raw>"}',
            secrets=("SECRET-123",),
        )
        response = sanitize_for_persistence(
            '{"result":"SEMANTIC_READINESS"}',
        )
        return ExchangeRecord(
            exchange_id="exchange-1",
            attempt_id="attempt-1",
            provider=ProviderIdentity("OPENAI", "OpenAI"),
            operation="TASK_EXECUTION",
            status="SUCCEEDED",
            request=request,
            response=response,
        )

    def test_secret_is_sanitized_before_persistence(self) -> None:
        exchange = self._exchange()
        self.assertNotIn("SECRET-123", exchange.request.body)
        self.assertIn("[REDACTED]", exchange.request.body)

    def test_hash_is_over_sanitized_persisted_body(self) -> None:
        exchange = self._exchange()
        expected = sha256(exchange.request.body.encode("utf-8")).hexdigest()
        self.assertEqual(exchange.request.sha256_hex, expected)

    def test_raw_api_body_is_exactly_the_persisted_sanitized_body(self) -> None:
        exchange = self._exchange()
        dto = raw_api_dto(exchange)
        request = dto["request_evidence"]
        assert isinstance(request, dict)
        self.assertEqual(request["sanitized_raw_body"], exchange.request.body)

    def test_metadata_humanization_does_not_rewrite_raw_token(self) -> None:
        exchange = self._exchange()
        dto = raw_api_dto(exchange)
        metadata = dto["metadata"]
        request = dto["request_evidence"]
        assert isinstance(metadata, dict)
        assert isinstance(request, dict)
        self.assertEqual(metadata["status_label"], "Succeeded")
        self.assertIn("SEMANTIC_READINESS", str(request["sanitized_raw_body"]))
        self.assertNotIn("Unknown status", str(request["sanitized_raw_body"]))

    def test_html_escaping_round_trips_to_same_persisted_body(self) -> None:
        exchange = self._exchange()
        rendered = html_transport(exchange.request)
        self.assertIn("&lt;raw&gt;", rendered)
        self.assertEqual(recover_html_transport(rendered), exchange.request.body)
        self.assertIn("SEMANTIC_READINESS", recover_html_transport(rendered))

    def test_provider_id_is_canonical_domain_identity(self) -> None:
        exchange = self._exchange()
        self.assertEqual(exchange.provider.provider_id, "OPENAI")
        self.assertEqual(provider_display(exchange.provider), "OpenAI")

    def test_generic_status_humanizer_is_not_provider_fallback(self) -> None:
        exchange = self._exchange()
        self.assertEqual(humanize_status(exchange.provider.provider_id), "Unknown status")
        self.assertEqual(provider_display(exchange.provider), "OpenAI")
        self.assertNotEqual(provider_display(exchange.provider), humanize_status(exchange.provider.provider_id))

    def test_attempt_identity_is_preserved_in_exchange_and_api(self) -> None:
        exchange = self._exchange()
        dto = raw_api_dto(exchange)
        self.assertEqual(exchange.attempt_id, "attempt-1")
        self.assertEqual(dto["attempt_id"], "attempt-1")

    def test_persisted_evidence_object_is_immutable(self) -> None:
        exchange = self._exchange()
        with self.assertRaises(FrozenInstanceError):
            exchange.request.body = "rewritten"  # type: ignore[misc]

    def test_client_evidence_dto_contains_no_provider_financial_or_secret_fields(self) -> None:
        dto = raw_api_dto(self._exchange())
        self.assertFalse(forbidden_client_fields(dto))

    def test_provider_name_is_display_metadata_separate_from_id(self) -> None:
        provider = ProviderIdentity("ANTHROPIC", "Anthropic")
        self.assertEqual(provider.provider_id, "ANTHROPIC")
        self.assertEqual(provider.provider_name, "Anthropic")
        self.assertNotEqual(provider.provider_id, provider.provider_name)


if __name__ == "__main__":
    unittest.main()
