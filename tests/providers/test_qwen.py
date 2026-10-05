from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from uuid import uuid7

from orqetia.providers import (
    QWEN_CHAT_COMPLETIONS_ENDPOINT,
    OutputKind,
    ProviderAttemptRequest,
    ProviderCredential,
    ProviderDispatchAmbiguousError,
    ProviderHttpResponse,
    ProviderInvocationPayload,
    ProviderOutcome,
    ProviderTarget,
    StructuredOutputSpec,
    QwenChatCompletionsAdapter,
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
        return "response://durable/qwen/1"


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


class QwenChatCompletionsAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.secret = "fixture-credential-must-never-persist"
        self.target = ProviderTarget("qwen", "qwen-fixture", "PROVIDER_DEFAULT")
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

    async def test_text_success_preserves_target_and_normalizes_usage(self) -> None:
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
                    "id": "chatcmpl_fixture",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "provider answer"},
                            "finish_reason": "stop",
                            "index": 0,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "prompt_tokens_details": {"cached_tokens": 4},
                        "completion_tokens": 8,
                        "completion_tokens_details": {"reasoning_tokens": 3},
                        "total_tokens": 20,
                    },
                }
            )
        )

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.TEXT)
        self.assertEqual(result.accepted_requirements, self.request.missing_requirements)
        self.assertEqual(result.response_reference, "response://durable/qwen/1")
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
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], QWEN_CHAT_COMPLETIONS_ENDPOINT)
        body = call["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "qwen-fixture")
        self.assertNotIn("reasoning", body)
        self.assertNotIn("response_format", body)
        self.assertEqual(
            body["messages"],
            [
                {"role": "system", "content": "Return a concise answer."},
                {"role": "user", "content": "Materialized client request"},
            ],
        )
        self.assertEqual(writer.calls[0]["target"], self.target)
        self.assertEqual(writer.calls[0]["content"], "provider answer")

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
        self.assertEqual(validator.calls[0]["value"], {"answer": "ok"})
        self.assertEqual(writer.calls[0]["content"], '{"answer":"ok"}')
        body = transport.calls[0]["body"]
        assert isinstance(body, dict)
        response_format = body["response_format"]
        assert isinstance(response_format, dict)
        self.assertEqual(response_format["type"], "json_schema")
        wire_schema = response_format["json_schema"]
        assert isinstance(wire_schema, dict)
        self.assertEqual(wire_schema["name"], "orqetia_answer")
        self.assertEqual(wire_schema["strict"], True)
        self.assertEqual(wire_schema["schema"], json.loads(schema_json))

    async def test_structured_validation_failure_is_malformed_and_not_persisted(self) -> None:
        reader = FakePayloadReader(
            ProviderInvocationPayload(
                input_text="Return the answer.",
                structured_output=StructuredOutputSpec(
                    name="answer",
                    schema_json='{"type":"object"}',
                ),
            )
        )
        writer = FakeResponseWriter()
        validator = FakeStructuredValidator(fail=True)
        transport = FakeTransport(_success('{"wrong":true}'))

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
            validator=validator,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "QWEN_STRUCTURED_OUTPUT_INVALID")
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
        transport = FakeTransport(_success("{}"))

        result = await self._adapter(reader=reader, transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.TERMINAL_ERROR)
        self.assertEqual(result.error_class, "STRUCTURED_VALIDATOR_UNAVAILABLE")
        self.assertEqual(transport.calls, [])

    async def test_invalid_json_response_is_malformed(self) -> None:
        transport = FakeTransport(
            ProviderHttpResponse(status_code=200, headers={}, body=b"not-json")
        )
        result = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "QWEN_INVALID_JSON_RESPONSE")

    async def test_missing_choice_content_is_malformed(self) -> None:
        transport = FakeTransport(_response({"choices": []}))
        result = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "QWEN_OUTPUT_TEXT_MISSING")

    async def test_incomplete_finish_reason_is_malformed_without_retry(self) -> None:
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
        result = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "QWEN_RESPONSE_INCOMPLETE")
        self.assertEqual(len(transport.calls), 1)

    async def test_auth_and_permission_failures_are_normalized(self) -> None:
        cases = (
            (401, "InvalidApiKey"),
            (403, "AccessDenied"),
        )
        for status, code in cases:
            with self.subTest(status=status, code=code):
                transport = FakeTransport(_response({"code": code}, status=status))
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, ProviderOutcome.AUTH_FAILURE)
                self.assertEqual(len(transport.calls), 1)

    async def test_arrearage_is_credit_exhausted(self) -> None:
        transport = FakeTransport(_response({"code": "Arrearage"}, status=400))
        result = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.CREDIT_EXHAUSTED)

    async def test_free_tier_exhaustion_is_quota_exhausted(self) -> None:
        transport = FakeTransport(
            _response({"code": "AllocationQuota.FreeTierOnly"}, status=403)
        )
        result = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.QUOTA_EXHAUSTED)

    async def test_throttling_quota_429_remains_rate_limited(self) -> None:
        transport = FakeTransport(
            _response(
                {"code": "Throttling.AllocationQuota"},
                status=429,
                headers={"Retry-After": "9"},
            )
        )
        result = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.RATE_LIMITED)
        self.assertEqual(result.retry_after_seconds, 9)
        self.assertEqual(len(transport.calls), 1)

    async def test_server_and_timeout_failures_are_normalized(self) -> None:
        cases = (
            (500, "InternalError", ProviderOutcome.TRANSIENT_ERROR),
            (408, "RequestTimeout", ProviderOutcome.TIMEOUT),
        )
        for status, code, expected in cases:
            with self.subTest(status=status, code=code):
                transport = FakeTransport(_response({"code": code}, status=status))
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(len(transport.calls), 1)

    async def test_transport_ambiguity_propagates(self) -> None:
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
            target=ProviderTarget("xai", "qwen-fixture", "PROVIDER_DEFAULT"),
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
        self.assertEqual(result.error_class, "QWEN_TARGET_PROVIDER_MISMATCH")
        self.assertEqual(reader.calls, [])
        self.assertEqual(transport.calls, [])

    async def test_payload_failure_never_dispatches_provider(self) -> None:
        reader = FakePayloadReader(
            ProviderInvocationPayload(input_text="request"),
            error=LookupError("missing durable request"),
        )
        transport = FakeTransport(_success("x"))

        result = await self._adapter(reader=reader, transport=transport).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.TERMINAL_ERROR)
        self.assertEqual(result.error_class, "REQUEST_PAYLOAD_LOOKUPERROR")
        self.assertEqual(transport.calls, [])

    async def test_credential_is_redacted_and_only_used_in_request_header(self) -> None:
        reader = FakePayloadReader(ProviderInvocationPayload(input_text="request"))
        writer = FakeResponseWriter()
        transport = FakeTransport(_success("ok"))
        credential = ProviderCredential(self.secret)
        adapter = QwenChatCompletionsAdapter(
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
    ) -> QwenChatCompletionsAdapter:
        return QwenChatCompletionsAdapter(
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
                    "index": 0,
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
