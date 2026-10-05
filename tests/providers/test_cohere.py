from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from uuid import uuid7

from orqetia.providers import (
    COHERE_CHAT_ENDPOINT,
    CohereChatV2Adapter,
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

    async def load(
        self,
        *,
        request_reference: str,
        request_fingerprint: str,
    ) -> ProviderInvocationPayload:
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
        return "response://durable/cohere/1"


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


class CohereChatV2AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.secret = "fixture-credential-must-never-persist"
        self.target = ProviderTarget(
            "cohere",
            "command-a-fixture",
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

    async def test_success_prefers_billed_units_and_keeps_wire_minimal(self) -> None:
        writer = Writer()
        transport = Transport(
            _response(
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "provider answer"},
                        ]
                    },
                    "finish_reason": "COMPLETE",
                    "usage": {
                        "billed_units": {
                            "input_tokens": 5,
                            "output_tokens": 7,
                        },
                        "tokens": {
                            "input_tokens": 50,
                            "output_tokens": 70,
                        },
                    },
                }
            )
        )

        result = await self._adapter(
            writer=writer,
            transport=transport,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.TEXT)
        self.assertEqual(result.usage.input_tokens, 5)
        self.assertEqual(result.usage.output_tokens, 7)
        self.assertEqual(
            tuple(item.name for item in result.usage.native),
            ("provider_total_tokens",),
        )
        call = transport.calls[0]
        self.assertEqual(call["url"], COHERE_CHAT_ENDPOINT)
        headers = call["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["Authorization"], f"Bearer {self.secret}")
        body = call["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "command-a-fixture")
        self.assertNotIn("tools", body)
        self.assertNotIn("documents", body)
        self.assertNotIn("commercial_mode", body)
        self.assertEqual(writer.calls[0]["content"], "provider answer")

    async def test_structured_wire_projection_preserves_local_schema(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "answer": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                },
                "choice": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"},
                    ]
                },
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": ["a", "b"]},
                },
            },
            "required": ["answer", "choice", "items"],
        }
        schema_json = json.dumps(schema, sort_keys=True)
        reader = Reader(
            ProviderInvocationPayload(
                input_text="Evidence.",
                structured_output=StructuredOutputSpec(
                    name="answer",
                    schema_json=schema_json,
                ),
            )
        )
        validator = Validator()
        transport = Transport(
            _response(
                {
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": '{"answer":"ok","choice":null,"items":["a"]}',
                            }
                        ]
                    }
                }
            )
        )

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
        self.assertEqual(response_format["type"], "json_object")
        wire = response_format["schema"]
        assert isinstance(wire, dict)
        properties = wire["properties"]
        assert isinstance(properties, dict)
        answer = properties["answer"]
        choice = properties["choice"]
        items = properties["items"]
        assert isinstance(answer, dict)
        assert isinstance(choice, dict)
        assert isinstance(items, dict)
        self.assertNotIn("minLength", answer)
        self.assertNotIn("maxLength", answer)
        self.assertIn("anyOf", choice)
        self.assertNotIn("minItems", items)
        self.assertNotIn("maxItems", items)
        self.assertNotIn("uniqueItems", items)

    async def test_invalid_structured_output_is_not_persisted(self) -> None:
        reader = Reader(
            ProviderInvocationPayload(
                input_text="Evidence.",
                structured_output=StructuredOutputSpec(
                    name="answer",
                    schema_json='{"type":"object"}',
                ),
            )
        )
        writer = Writer()
        transport = Transport(
            _response(
                {
                    "message": {
                        "content": [{"type": "text", "text": '{"wrong":true}'}]
                    }
                }
            )
        )

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
            validator=Validator(fail=True),
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "COHERE_STRUCTURED_OUTPUT_INVALID")
        self.assertEqual(writer.calls, [])

    async def test_native_finish_reasons_fail_closed_single_attempt(self) -> None:
        cases = (
            ("TIMEOUT", ProviderOutcome.TIMEOUT, "COHERE_TIMEOUT"),
            ("ERROR", ProviderOutcome.TRANSIENT_ERROR, "COHERE_ERROR"),
            (
                "TOOL_CALL",
                ProviderOutcome.MALFORMED_OUTPUT,
                "COHERE_UNAUTHORIZED_TOOL_CALL",
            ),
        )
        for reason, expected, error_class in cases:
            with self.subTest(reason=reason):
                transport = Transport(
                    _response(
                        {
                            "message": {
                                "content": [{"type": "text", "text": "ignored"}]
                            },
                            "finish_reason": reason,
                        }
                    )
                )
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(result.error_class, error_class)
                self.assertEqual(len(transport.calls), 1)

    async def test_http_failures_and_retry_metadata_are_single_attempt(self) -> None:
        cases = (
            (401, "authentication_error", ProviderOutcome.AUTH_FAILURE),
            (403, "permission_error", ProviderOutcome.AUTH_FAILURE),
            (429, "rate_limit_error", ProviderOutcome.RATE_LIMITED),
            (500, "internal_error", ProviderOutcome.TRANSIENT_ERROR),
        )
        for status, error_type, expected in cases:
            with self.subTest(status=status):
                transport = Transport(
                    _response(
                        {"type": error_type},
                        status=status,
                        headers={"Retry-After": "3"},
                    )
                )
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(len(transport.calls), 1)

        rate = Transport(
            _response(
                {"type": "rate_limit_error"},
                status=429,
                headers={"Retry-After": "3"},
            )
        )
        result = await self._adapter(transport=rate).invoke(self.request)
        self.assertEqual(result.retry_after_seconds, 3)

    async def test_malformed_missing_output_and_ambiguity_fail_closed(self) -> None:
        malformed = await self._adapter(
            transport=Transport(
                ProviderHttpResponse(status_code=200, headers={}, body=b"not-json")
            )
        ).invoke(self.request)
        self.assertEqual(malformed.outcome, ProviderOutcome.MALFORMED_OUTPUT)

        missing = await self._adapter(
            transport=Transport(_response({"message": {"content": []}}))
        ).invoke(self.request)
        self.assertEqual(missing.outcome, ProviderOutcome.MALFORMED_OUTPUT)

        ambiguous = Transport(error=OSError("connection reset"))
        with self.assertRaises(ProviderDispatchAmbiguousError):
            await self._adapter(transport=ambiguous).invoke(self.request)
        self.assertEqual(len(ambiguous.calls), 1)

    async def test_target_mismatch_and_secret_boundary(self) -> None:
        request = ProviderAttemptRequest(
            attempt_id=self.request.attempt_id,
            operation=self.request.operation,
            target=ProviderTarget("anthropic", "command-a-fixture", "PROVIDER_DEFAULT"),
            cycle=1,
            attempt_index=1,
            request_reference=self.request.request_reference,
            request_fingerprint=self.request.request_fingerprint,
            missing_requirements=self.request.missing_requirements,
        )
        transport = Transport(
            _response(
                {
                    "message": {
                        "content": [{"type": "text", "text": "x"}]
                    }
                }
            )
        )
        result = await self._adapter(transport=transport).invoke(request)
        self.assertEqual(result.error_class, "COHERE_TARGET_PROVIDER_MISMATCH")
        self.assertEqual(transport.calls, [])

        credential = ProviderCredential(self.secret)
        writer = Writer()
        transport = Transport(
            _response(
                {
                    "message": {
                        "content": [{"type": "text", "text": "ok"}]
                    }
                }
            )
        )
        adapter = CohereChatV2Adapter(
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
    ) -> CohereChatV2Adapter:
        return CohereChatV2Adapter(
            credential=ProviderCredential(self.secret),
            payload_reader=reader
            or Reader(ProviderInvocationPayload(input_text="request")),
            response_writer=writer or Writer(),
            transport=transport
            or Transport(
                _response(
                    {
                        "message": {
                            "content": [{"type": "text", "text": "ok"}]
                        }
                    }
                )
            ),
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
