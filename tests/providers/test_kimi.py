from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from uuid import uuid7

from orqetia.providers import (
    KIMI_CHAT_COMPLETIONS_ENDPOINT,
    KimiChatCompletionsAdapter,
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


class Reader:
    def __init__(self, payload: ProviderInvocationPayload) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def load(
        self,
        *,
        request_reference: str,
        request_fingerprint: str,
    ) -> ProviderInvocationPayload:
        self.calls.append((request_reference, request_fingerprint))
        return self.payload


class Writer:
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
        return "response://durable/kimi/1"


class Transport:
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
            raise AssertionError("response fixture missing")
        return self.response


class Validator:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def validate(self, *, schema_json: str, value: object) -> None:
        self.calls.append({"schema_json": schema_json, "value": value})
        if self.fail:
            raise ValueError("fixture schema mismatch")


class KimiAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.secret = "fixture-credential-must-never-persist"
        self.target = ProviderTarget("kimi", "kimi-k3-fixture", "LOW")
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

    async def test_success_forwards_exact_reasoning_and_usage(self) -> None:
        writer = Writer()
        transport = Transport(
            _response(
                {
                    "choices": [
                        {
                            "message": {
                                "reasoning_content": "provider reasoning",
                                "content": "provider answer",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 120,
                        "prompt_tokens_details": {
                            "cached_tokens": 30,
                            "cache_write_tokens": 20,
                        },
                        "completion_tokens": 40,
                        "total_tokens": 160,
                    },
                }
            )
        )

        result = await self._adapter(
            writer=writer,
            transport=transport,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.usage.input_tokens, 120)
        self.assertEqual(result.usage.output_tokens, 40)
        self.assertEqual(
            tuple(item.name for item in result.usage.native),
            ("cached_input_tokens", "provider_total_tokens"),
        )
        call = transport.calls[0]
        self.assertEqual(call["url"], KIMI_CHAT_COMPLETIONS_ENDPOINT)
        body = call["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertNotIn("tools", body)
        self.assertEqual(writer.calls[0]["content"], "provider answer")
        self.assertNotIn("provider reasoning", repr(writer.calls))

    async def test_provider_default_does_not_invent_reasoning_effort(self) -> None:
        request = ProviderAttemptRequest(
            attempt_id=self.request.attempt_id,
            operation=self.request.operation,
            target=ProviderTarget("kimi", "kimi-k3-fixture", "PROVIDER_DEFAULT"),
            cycle=1,
            attempt_index=1,
            request_reference=self.request.request_reference,
            request_fingerprint=self.request.request_fingerprint,
            missing_requirements=self.request.missing_requirements,
        )
        transport = Transport(_success("ok"))

        result = await self._adapter(transport=transport).invoke(request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        body = transport.calls[0]["body"]
        assert isinstance(body, dict)
        self.assertNotIn("reasoning_effort", body)

    async def test_wire_schema_projection_keeps_local_schema_canonical(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "answer": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                },
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": ["a", "b"]},
                },
            },
            "required": ["answer", "items"],
        }
        schema_json = json.dumps(schema, sort_keys=True)
        reader = Reader(
            ProviderInvocationPayload(
                input_text="Return JSON.",
                structured_output=StructuredOutputSpec(
                    name="orqetia_answer",
                    schema_json=schema_json,
                ),
            )
        )
        validator = Validator()
        transport = Transport(_success('{"answer":"ok","items":["a"]}'))

        result = await self._adapter(
            reader=reader,
            transport=transport,
            validator=validator,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(validator.calls[0]["schema_json"], schema_json)
        body = transport.calls[0]["body"]
        assert isinstance(body, dict)
        response_format = body["response_format"]
        assert isinstance(response_format, dict)
        json_schema = response_format["json_schema"]
        assert isinstance(json_schema, dict)
        self.assertEqual(json_schema["strict"], True)
        wire = json_schema["schema"]
        assert isinstance(wire, dict)
        properties = wire["properties"]
        assert isinstance(properties, dict)
        answer = properties["answer"]
        items = properties["items"]
        assert isinstance(answer, dict)
        assert isinstance(items, dict)
        self.assertNotIn("minLength", answer)
        self.assertNotIn("maxLength", answer)
        self.assertNotIn("minItems", items)
        self.assertNotIn("maxItems", items)
        self.assertNotIn("uniqueItems", items)

    async def test_invalid_structured_output_is_not_persisted(self) -> None:
        reader = Reader(
            ProviderInvocationPayload(
                input_text="Return JSON.",
                structured_output=StructuredOutputSpec(
                    name="answer",
                    schema_json='{"type":"object"}',
                ),
            )
        )
        writer = Writer()
        transport = Transport(_success('{"wrong":true}'))

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
            validator=Validator(fail=True),
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "KIMI_STRUCTURED_OUTPUT_INVALID")
        self.assertEqual(writer.calls, [])
        self.assertEqual(len(transport.calls), 1)

    async def test_failures_are_single_attempt_and_ambiguity_propagates(self) -> None:
        cases = (
            (401, ProviderOutcome.AUTH_FAILURE),
            (408, ProviderOutcome.TIMEOUT),
            (500, ProviderOutcome.TRANSIENT_ERROR),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                transport = Transport(
                    _response(
                        {"error": {"type": "provider_error"}},
                        status=status,
                    )
                )
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(len(transport.calls), 1)

        rate = Transport(
            _response(
                {"error": {"type": "rate_limit"}},
                status=429,
                headers={"Retry-After": "6"},
            )
        )
        result = await self._adapter(transport=rate).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.RATE_LIMITED)
        self.assertEqual(result.retry_after_seconds, 6)
        self.assertEqual(len(rate.calls), 1)

        ambiguous = Transport(error=OSError("connection reset"))
        with self.assertRaises(ProviderDispatchAmbiguousError):
            await self._adapter(transport=ambiguous).invoke(self.request)
        self.assertEqual(len(ambiguous.calls), 1)

    async def test_target_mismatch_and_secret_boundary(self) -> None:
        request = ProviderAttemptRequest(
            attempt_id=self.request.attempt_id,
            operation=self.request.operation,
            target=ProviderTarget("mistral", "kimi-k3-fixture", "LOW"),
            cycle=1,
            attempt_index=1,
            request_reference=self.request.request_reference,
            request_fingerprint=self.request.request_fingerprint,
            missing_requirements=self.request.missing_requirements,
        )
        transport = Transport(_success("x"))
        result = await self._adapter(transport=transport).invoke(request)
        self.assertEqual(result.error_class, "KIMI_TARGET_PROVIDER_MISMATCH")
        self.assertEqual(transport.calls, [])

        credential = ProviderCredential(self.secret)
        writer = Writer()
        transport = Transport(_success("ok"))
        adapter = KimiChatCompletionsAdapter(
            credential=credential,
            payload_reader=Reader(
                ProviderInvocationPayload(input_text="request")
            ),
            response_writer=writer,
            transport=transport,
        )
        result = await adapter.invoke(self.request)
        self.assertNotIn(self.secret, repr(credential))
        self.assertNotIn(self.secret, repr(adapter))
        self.assertNotIn(self.secret, repr(result))
        self.assertNotIn(self.secret, repr(writer.calls))

    def _adapter(
        self,
        *,
        reader: Reader | None = None,
        writer: Writer | None = None,
        transport: Transport | None = None,
        validator: Validator | None = None,
    ) -> KimiChatCompletionsAdapter:
        return KimiChatCompletionsAdapter(
            credential=ProviderCredential(self.secret),
            payload_reader=reader
            or Reader(ProviderInvocationPayload(input_text="request")),
            response_writer=writer or Writer(),
            transport=transport or Transport(_success("ok")),
            structured_validator=validator,
            timeout_seconds=30,
        )


def _success(content: str) -> ProviderHttpResponse:
    return _response(
        {
            "choices": [
                {
                    "message": {"content": content},
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
