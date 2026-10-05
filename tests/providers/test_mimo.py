from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from uuid import uuid7

from orqetia.providers import (
    MIMO_RESPONSES_ENDPOINT,
    MiMoResponsesAdapter,
    OutputKind,
    ProviderAttemptRequest,
    ProviderCredential,
    ProviderDispatchAmbiguousError,
    ProviderHttpResponse,
    ProviderInvocationPayload,
    ProviderOutcome,
    ProviderTarget,
    StructuredOutputSpec,
)


class FakePayloadReader:
    def __init__(
        self,
        payload: ProviderInvocationPayload,
        *,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def load(
        self,
        *,
        request_reference: str,
        request_fingerprint: str,
    ) -> ProviderInvocationPayload:
        self.calls.append((request_reference, request_fingerprint))
        if self.error is not None:
            raise self.error
        return self.payload


class FakeResponseWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def store(
        self,
        *,
        attempt_id: object,
        target: ProviderTarget,
        output_kind: OutputKind,
        content: str,
    ) -> str:
        self.calls.append(
            {
                "attempt_id": attempt_id,
                "target": target,
                "output_kind": output_kind,
                "content": content,
            }
        )
        return "response://durable/mimo/1"


class FakeTransport:
    def __init__(
        self,
        response: ProviderHttpResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, object],
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": dict(body),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake transport response not configured")
        return self.response


class FakeStructuredValidator:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def validate(self, *, schema_json: str, value: object) -> None:
        self.calls.append({"schema_json": schema_json, "value": value})
        if self.fail:
            raise ValueError("fixture schema mismatch")


class MiMoResponsesAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.secret = "fixture-credential-must-never-persist"
        self.target = ProviderTarget("mimo", "mimo-v2.6-fixture", "HIGH")
        self.request = ProviderAttemptRequest(
            attempt_id=uuid7(),
            operation="GENERATE",
            target=self.target,
            cycle=1,
            attempt_index=1,
            request_reference="request://durable/1",
            request_fingerprint="a" * 64,
            missing_requirements=("answer", "citations"),
        )

    async def test_text_success_preserves_exact_target_and_normalizes_usage(self) -> None:
        reader = FakePayloadReader(
            ProviderInvocationPayload(
                input_text="Materialized client request",
                instructions="Return a concise answer.",
            )
        )
        writer = FakeResponseWriter()
        transport = FakeTransport(
            _response(
                {
                    "id": "resp_fixture",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "provider answer",
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 12,
                        "input_tokens_details": {"cached_tokens": 4},
                        "output_tokens": 8,
                        "output_tokens_details": {"reasoning_tokens": 3},
                        "total_tokens": 20,
                    },
                }
            )
        )
        adapter = self._adapter(reader=reader, writer=writer, transport=transport)

        result = await adapter.invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.TEXT)
        self.assertEqual(result.accepted_requirements, self.request.missing_requirements)
        self.assertEqual(result.missing_requirements, ())
        self.assertEqual(result.response_reference, "response://durable/mimo/1")
        self.assertEqual(result.usage.input_tokens, 12)
        self.assertEqual(result.usage.output_tokens, 8)
        self.assertEqual(
            tuple(item.name for item in result.usage.native),
            (
                "cached_input_tokens",
                "reasoning_output_tokens",
                "provider_total_tokens",
            ),
        )
        self.assertGreaterEqual(result.latency_ms, 0)

        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], MIMO_RESPONSES_ENDPOINT)
        body = call["body"]
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "mimo-v2.6-fixture")
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertNotIn("service_tier", body)
        self.assertNotIn("store", body)
        self.assertNotIn("tools", body)
        headers = call["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["api-key"], self.secret)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(writer.calls[0]["target"], self.target)
        self.assertEqual(writer.calls[0]["content"], "provider answer")

    async def test_structured_success_uses_json_object_and_local_validation(self) -> None:
        schema_json = json.dumps(
            {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            sort_keys=True,
        )
        reader = FakePayloadReader(
            ProviderInvocationPayload(
                input_text="Return JSON.",
                structured_output=StructuredOutputSpec(
                    name="orqetia_answer",
                    schema_json=schema_json,
                ),
            )
        )
        writer = FakeResponseWriter()
        validator = FakeStructuredValidator()
        transport = FakeTransport(
            _response(
                {
                    "status": "completed",
                    "output_text": '{"answer":"ok"}',
                }
            )
        )
        adapter = self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
            validator=validator,
        )

        result = await adapter.invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.STRUCTURED)
        self.assertEqual(validator.calls[0]["value"], {"answer": "ok"})
        self.assertEqual(writer.calls[0]["content"], '{"answer":"ok"}')

        body = transport.calls[0]["body"]
        assert isinstance(body, dict)
        text_config = body["text"]
        assert isinstance(text_config, dict)
        output_format = text_config["format"]
        assert isinstance(output_format, dict)
        self.assertEqual(output_format, {"type": "json_object"})
        instructions = body["instructions"]
        assert isinstance(instructions, str)
        canonical_schema = json.dumps(
            json.loads(schema_json),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertIn(canonical_schema, instructions)

    async def test_structured_output_validation_failure_is_malformed_and_not_persisted(
        self,
    ) -> None:
        reader = FakePayloadReader(
            ProviderInvocationPayload(
                input_text="Return JSON.",
                structured_output=StructuredOutputSpec(
                    name="answer",
                    schema_json='{"type":"object"}',
                ),
            )
        )
        writer = FakeResponseWriter()
        validator = FakeStructuredValidator(fail=True)
        transport = FakeTransport(
            _response({"status": "completed", "output_text": '{"wrong":true}'})
        )

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
            validator=validator,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.output_kind, OutputKind.MALFORMED)
        self.assertEqual(result.error_class, "MIMO_STRUCTURED_OUTPUT_INVALID")
        self.assertEqual(writer.calls, [])
        self.assertEqual(len(transport.calls), 1)

    async def test_structured_output_fails_closed_without_local_validator(self) -> None:
        reader = FakePayloadReader(
            ProviderInvocationPayload(
                input_text="Return JSON.",
                structured_output=StructuredOutputSpec(
                    name="answer",
                    schema_json='{"type":"object"}',
                ),
            )
        )
        writer = FakeResponseWriter()
        transport = FakeTransport(_response({"status": "completed", "output_text": "{}"}))

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.TERMINAL_ERROR)
        self.assertEqual(result.error_class, "STRUCTURED_VALIDATOR_UNAVAILABLE")
        self.assertEqual(transport.calls, [])
        self.assertEqual(writer.calls, [])

    async def test_invalid_json_response_is_malformed(self) -> None:
        transport = FakeTransport(
            ProviderHttpResponse(status_code=200, headers={}, body=b"not-json")
        )

        result = await self._adapter(transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "MIMO_INVALID_JSON_RESPONSE")

    async def test_completed_response_without_output_text_is_malformed(self) -> None:
        transport = FakeTransport(_response({"status": "completed", "output": []}))

        result = await self._adapter(transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "MIMO_OUTPUT_TEXT_MISSING")

    async def test_incomplete_response_is_malformed_without_internal_retry(self) -> None:
        transport = FakeTransport(
            _response(
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                }
            )
        )

        result = await self._adapter(transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "MIMO_RESPONSE_INCOMPLETE")
        self.assertEqual(len(transport.calls), 1)

    async def test_auth_and_permission_http_failures_normalize_to_auth_failure(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                transport = FakeTransport(
                    _response(
                        {"error": {"type": "authentication_error", "code": "invalid_api_key"}},
                        status=status,
                    )
                )
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, ProviderOutcome.AUTH_FAILURE)
                self.assertEqual(len(transport.calls), 1)

    async def test_rate_limit_preserves_retry_after_without_retrying(self) -> None:
        transport = FakeTransport(
            _response(
                {"error": {"type": "rate_limit_error", "code": "rate_limit_exceeded"}},
                status=429,
                headers={"Retry-After": "17"},
            )
        )

        result = await self._adapter(transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.RATE_LIMITED)
        self.assertEqual(result.retry_after_seconds, 17)
        self.assertEqual(len(transport.calls), 1)

    async def test_quota_429_is_not_misclassified_as_generic_rate_limit(self) -> None:
        transport = FakeTransport(
            _response(
                {"error": {"type": "token_plan_quota_exhausted", "code": "token_plan_quota_exhausted"}},
                status=429,
            )
        )

        result = await self._adapter(transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.QUOTA_EXHAUSTED)
        self.assertEqual(len(transport.calls), 1)

    async def test_credit_402_and_server_500_are_normalized(self) -> None:
        cases = (
            (
                _response(
                    {"error": {"type": "billing_error", "code": "credit_exhausted"}},
                    status=402,
                ),
                ProviderOutcome.CREDIT_EXHAUSTED,
            ),
            (
                _response(
                    {"error": {"type": "server_error", "code": "internal_error"}},
                    status=500,
                ),
                ProviderOutcome.TRANSIENT_ERROR,
            ),
        )
        for response, expected in cases:
            with self.subTest(expected=expected):
                transport = FakeTransport(response)
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(len(transport.calls), 1)

    async def test_http_408_is_known_timeout_outcome(self) -> None:
        transport = FakeTransport(
            _response(
                {"error": {"type": "timeout", "code": "request_timeout"}},
                status=408,
            )
        )

        result = await self._adapter(transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.TIMEOUT)
        self.assertEqual(len(transport.calls), 1)

    async def test_transport_ambiguity_propagates_for_durable_terminalization(self) -> None:
        transport = FakeTransport(error=ProviderDispatchAmbiguousError("ambiguous"))

        with self.assertRaises(ProviderDispatchAmbiguousError):
            await self._adapter(transport=transport).invoke(self.request)

        self.assertEqual(len(transport.calls), 1)

    async def test_unknown_transport_exception_is_wrapped_as_ambiguous(self) -> None:
        transport = FakeTransport(error=OSError("connection reset"))

        with self.assertRaises(ProviderDispatchAmbiguousError):
            await self._adapter(transport=transport).invoke(self.request)

        self.assertEqual(len(transport.calls), 1)

    async def test_target_mismatch_fails_before_payload_or_network(self) -> None:
        request = ProviderAttemptRequest(
            attempt_id=self.request.attempt_id,
            operation=self.request.operation,
            target=ProviderTarget("openai", "gpt-fixture", "HIGH"),
            cycle=self.request.cycle,
            attempt_index=self.request.attempt_index,
            request_reference=self.request.request_reference,
            request_fingerprint=self.request.request_fingerprint,
            missing_requirements=self.request.missing_requirements,
        )
        reader = FakePayloadReader(ProviderInvocationPayload(input_text="request"))
        transport = FakeTransport(_response({"status": "completed", "output_text": "x"}))

        result = await self._adapter(
            reader=reader,
            transport=transport,
        ).invoke(request)

        self.assertEqual(result.outcome, ProviderOutcome.TERMINAL_ERROR)
        self.assertEqual(result.error_class, "MIMO_TARGET_PROVIDER_MISMATCH")
        self.assertEqual(reader.calls, [])
        self.assertEqual(transport.calls, [])

    async def test_payload_failure_never_dispatches_provider(self) -> None:
        reader = FakePayloadReader(
            ProviderInvocationPayload(input_text="request"),
            error=LookupError("missing durable request"),
        )
        transport = FakeTransport(_response({"status": "completed", "output_text": "x"}))

        result = await self._adapter(
            reader=reader,
            transport=transport,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.TERMINAL_ERROR)
        self.assertEqual(result.error_class, "REQUEST_PAYLOAD_LOOKUPERROR")
        self.assertEqual(transport.calls, [])

    async def test_provider_credential_is_redacted_from_representations_and_results(self) -> None:
        reader = FakePayloadReader(ProviderInvocationPayload(input_text="request"))
        writer = FakeResponseWriter()
        transport = FakeTransport(_response({"status": "completed", "output_text": "ok"}))
        credential = ProviderCredential(self.secret)
        adapter = MiMoResponsesAdapter(
            credential=credential,
            payload_reader=reader,
            response_writer=writer,
            transport=transport,
        )

        result = await adapter.invoke(self.request)

        self.assertNotIn(self.secret, repr(credential))
        self.assertNotIn(self.secret, str(credential))
        self.assertNotIn(self.secret, repr(adapter))
        self.assertNotIn(self.secret, repr(result))
        self.assertNotIn(self.secret, repr(writer.calls))
        self.assertNotIn(self.secret, writer.calls[0]["content"])
        headers = transport.calls[0]["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["api-key"], self.secret)
        self.assertNotIn("Authorization", headers)

    async def test_provider_default_profile_is_not_invented_as_reasoning_effort(self) -> None:
        request = ProviderAttemptRequest(
            attempt_id=self.request.attempt_id,
            operation=self.request.operation,
            target=ProviderTarget("mimo", "mimo-v2.6-fixture", "PROVIDER_DEFAULT"),
            cycle=1,
            attempt_index=1,
            request_reference=self.request.request_reference,
            request_fingerprint=self.request.request_fingerprint,
            missing_requirements=("answer",),
        )
        transport = FakeTransport(_response({"status": "completed", "output_text": "ok"}))

        result = await self._adapter(transport=transport).invoke(request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        body = transport.calls[0]["body"]
        assert isinstance(body, dict)
        self.assertNotIn("reasoning", body)

    def _adapter(
        self,
        *,
        reader: FakePayloadReader | None = None,
        writer: FakeResponseWriter | None = None,
        transport: FakeTransport | None = None,
        validator: FakeStructuredValidator | None = None,
    ) -> MiMoResponsesAdapter:
        return MiMoResponsesAdapter(
            credential=ProviderCredential(self.secret),
            payload_reader=reader
            or FakePayloadReader(ProviderInvocationPayload(input_text="request")),
            response_writer=writer or FakeResponseWriter(),
            transport=transport
            or FakeTransport(_response({"status": "completed", "output_text": "ok"})),
            structured_validator=validator,
            timeout_seconds=30,
        )


def _response(
    payload: object,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> ProviderHttpResponse:
    return ProviderHttpResponse(
        status_code=status,
        headers=headers or {},
        body=json.dumps(payload).encode("utf-8"),
    )


if __name__ == "__main__":
    unittest.main()
