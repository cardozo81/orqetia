from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from uuid import uuid7

from orqetia.providers import (
    MISTRAL_CHAT_COMPLETIONS_ENDPOINT,
    MistralChatCompletionsAdapter,
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
        return "response://durable/mistral/1"


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


class MistralChatCompletionsAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.secret = "fixture-credential-must-never-persist"
        self.target = ProviderTarget(
            "mistral",
            "mistral-small-fixture",
            "PROVIDER_DEFAULT",
        )
        self.request = ProviderAttemptRequest(
            attempt_id=uuid7(),
            operation="GENERATE",
            target=self.target,
            cycle=1,
            attempt_index=1,
            request_reference="request://durable/1",
            request_fingerprint="a" * 64,
            missing_requirements=("answer",),
        )

    async def test_text_success_preserves_target_and_standard_tier(self) -> None:
        writer = FakeResponseWriter()
        transport = FakeTransport(
            _response(
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "provider answer"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 8,
                        "total_tokens": 20,
                    },
                }
            )
        )

        result = await self._adapter(writer=writer, transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.TEXT)
        self.assertEqual(result.response_reference, "response://durable/mistral/1")
        self.assertEqual(result.usage.input_tokens, 12)
        self.assertEqual(result.usage.output_tokens, 8)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], MISTRAL_CHAT_COMPLETIONS_ENDPOINT)
        body = call["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "mistral-small-fixture")
        self.assertEqual(body["service_tier"], "standard_only")
        self.assertNotIn("response_format", body)
        self.assertEqual(writer.calls[0]["target"], self.target)

    async def test_structured_success_uses_strict_schema_and_local_validation(self) -> None:
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
                input_text="Return the answer.",
                structured_output=StructuredOutputSpec(
                    name="orqetia_answer",
                    schema_json=schema_json,
                ),
            )
        )
        writer = FakeResponseWriter()
        validator = FakeStructuredValidator()
        transport = FakeTransport(_success('{"answer":"ok"}'))

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
            validator=validator,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.STRUCTURED)
        self.assertEqual(len(validator.calls), 1)
        body = transport.calls[0]["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["service_tier"], "standard_only")
        response_format = body["response_format"]
        assert isinstance(response_format, dict)
        self.assertEqual(response_format["type"], "json_schema")
        wire_schema = response_format["json_schema"]
        assert isinstance(wire_schema, dict)
        self.assertEqual(wire_schema["strict"], True)
        self.assertEqual(wire_schema["schema"], json.loads(schema_json))

    async def test_structured_validation_failure_is_not_persisted(self) -> None:
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
        transport = FakeTransport(_success('{"wrong":true}'))

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
            validator=FakeStructuredValidator(fail=True),
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "MISTRAL_STRUCTURED_OUTPUT_INVALID")
        self.assertEqual(writer.calls, [])
        self.assertEqual(len(transport.calls), 1)

    async def test_malformed_and_incomplete_responses_fail_closed(self) -> None:
        malformed = await self._adapter(
            transport=FakeTransport(
                ProviderHttpResponse(status_code=200, headers={}, body=b"not-json")
            )
        ).invoke(self.request)
        self.assertEqual(malformed.outcome, ProviderOutcome.MALFORMED_OUTPUT)

        transport = FakeTransport(
            _response(
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "partial"},
                            "finish_reason": "length",
                        }
                    ]
                }
            )
        )
        incomplete = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(incomplete.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(incomplete.error_class, "MISTRAL_RESPONSE_INCOMPLETE")
        self.assertEqual(len(transport.calls), 1)

    async def test_http_failures_normalize_without_adapter_retry(self) -> None:
        cases = (
            (401, "authentication_error", ProviderOutcome.AUTH_FAILURE),
            (403, "permission_denied", ProviderOutcome.AUTH_FAILURE),
            (408, "request_timeout", ProviderOutcome.TIMEOUT),
            (500, "server_error", ProviderOutcome.TRANSIENT_ERROR),
        )
        for status, code, expected in cases:
            with self.subTest(status=status, code=code):
                transport = FakeTransport(
                    _response({"error": {"type": code, "code": code}}, status=status)
                )
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(len(transport.calls), 1)

    async def test_rate_limit_captures_retry_after_without_sleep_or_retry(self) -> None:
        transport = FakeTransport(
            _response(
                {"error": {"type": "rate_limit", "code": "rate_limit"}},
                status=429,
                headers={"Retry-After": "7"},
            )
        )
        result = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.RATE_LIMITED)
        self.assertEqual(result.retry_after_seconds, 7)
        self.assertEqual(len(transport.calls), 1)

    async def test_transport_ambiguity_is_never_silently_redispatched(self) -> None:
        transport = FakeTransport(error=OSError("connection reset"))
        with self.assertRaises(ProviderDispatchAmbiguousError):
            await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(len(transport.calls), 1)

    async def test_target_mismatch_fails_before_payload_or_network(self) -> None:
        request = ProviderAttemptRequest(
            attempt_id=self.request.attempt_id,
            operation=self.request.operation,
            target=ProviderTarget("qwen", "mistral-small-fixture", "PROVIDER_DEFAULT"),
            cycle=1,
            attempt_index=1,
            request_reference=self.request.request_reference,
            request_fingerprint=self.request.request_fingerprint,
            missing_requirements=self.request.missing_requirements,
        )
        reader = FakePayloadReader(ProviderInvocationPayload(input_text="request"))
        transport = FakeTransport(_success("x"))

        result = await self._adapter(reader=reader, transport=transport).invoke(request)

        self.assertEqual(result.outcome, ProviderOutcome.TERMINAL_ERROR)
        self.assertEqual(result.error_class, "MISTRAL_TARGET_PROVIDER_MISMATCH")
        self.assertEqual(reader.calls, [])
        self.assertEqual(transport.calls, [])

    async def test_credential_is_ephemeral_and_redacted(self) -> None:
        writer = FakeResponseWriter()
        transport = FakeTransport(_success("ok"))
        credential = ProviderCredential(self.secret)
        adapter = MistralChatCompletionsAdapter(
            credential=credential,
            payload_reader=FakePayloadReader(
                ProviderInvocationPayload(input_text="request")
            ),
            response_writer=writer,
            transport=transport,
        )

        result = await adapter.invoke(self.request)

        self.assertNotIn(self.secret, repr(credential))
        self.assertNotIn(self.secret, str(credential))
        self.assertNotIn(self.secret, repr(adapter))
        self.assertNotIn(self.secret, repr(result))
        self.assertNotIn(self.secret, repr(writer.calls))
        headers = transport.calls[0]["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["Authorization"], f"Bearer {self.secret}")

    def _adapter(
        self,
        *,
        reader: FakePayloadReader | None = None,
        writer: FakeResponseWriter | None = None,
        transport: FakeTransport | None = None,
        validator: FakeStructuredValidator | None = None,
    ) -> MistralChatCompletionsAdapter:
        return MistralChatCompletionsAdapter(
            credential=ProviderCredential(self.secret),
            payload_reader=reader
            or FakePayloadReader(ProviderInvocationPayload(input_text="request")),
            response_writer=writer or FakeResponseWriter(),
            transport=transport or FakeTransport(_success("ok")),
            structured_validator=validator,
            timeout_seconds=30,
        )


def _success(content: str) -> ProviderHttpResponse:
    return _response(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ]
        }
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
