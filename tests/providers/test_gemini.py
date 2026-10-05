from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from uuid import uuid7

from orqetia.providers import (
    GEMINI_INTERACTIONS_ENDPOINT,
    GeminiInteractionsAdapter,
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
        return "response://durable/gemini/1"


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


class GeminiInteractionsAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.secret = "fixture-credential-must-never-persist"
        self.target = ProviderTarget(
            "gemini",
            "gemini-fixture",
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

    async def test_text_success_uses_api_key_and_normalizes_usage(self) -> None:
        writer = Writer()
        transport = Transport(
            _response(
                {
                    "output_text": "provider answer",
                    "usage_metadata": {
                        "prompt_token_count": 11,
                        "candidates_token_count": 7,
                        "cached_content_token_count": 3,
                        "thoughts_token_count": 2,
                        "total_token_count": 20,
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
        self.assertEqual(result.usage.input_tokens, 11)
        self.assertEqual(result.usage.output_tokens, 7)
        self.assertEqual(
            tuple(item.name for item in result.usage.native),
            (
                "cached_input_tokens",
                "reasoning_output_tokens",
                "provider_total_tokens",
            ),
        )
        call = transport.calls[0]
        self.assertEqual(call["url"], GEMINI_INTERACTIONS_ENDPOINT)
        headers = call["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["x-goog-api-key"], self.secret)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(writer.calls[0]["content"], "provider answer")

    async def test_steps_model_output_is_accepted(self) -> None:
        transport = Transport(
            _response(
                {
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [
                                {"type": "text", "text": "older"},
                            ],
                        },
                        {
                            "type": "model_output",
                            "content": [
                                {"type": "text", "text": "final"},
                            ],
                        },
                    ]
                }
            )
        )

        writer = Writer()
        result = await self._adapter(
            writer=writer,
            transport=transport,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(writer.calls[0]["content"], "final")

    async def test_structured_output_projects_wire_and_validates_canonical(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "pattern": "^ok$",
                    "minLength": 2,
                }
            },
            "required": ["answer"],
            "additionalProperties": False,
            "unevaluatedProperties": False,
        }
        schema_json = json.dumps(schema, sort_keys=True)
        reader = Reader(
            ProviderInvocationPayload(
                input_text="Evidence.",
                instructions="Return JSON.",
                structured_output=StructuredOutputSpec(
                    name="answer",
                    schema_json=schema_json,
                ),
            )
        )
        validator = Validator()
        transport = Transport(_response({"output_text": '{"answer":"ok"}'}))

        result = await self._adapter(
            reader=reader,
            transport=transport,
            validator=validator,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(validator.calls[0]["schema_json"], schema_json)
        body = transport.calls[0]["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "gemini-fixture")
        response_format = body["response_format"]
        assert isinstance(response_format, dict)
        self.assertEqual(response_format["type"], "text")
        self.assertEqual(response_format["mime_type"], "application/json")
        wire = response_format["schema"]
        assert isinstance(wire, dict)
        self.assertNotIn("unevaluatedProperties", wire)
        properties = wire["properties"]
        assert isinstance(properties, dict)
        answer = properties["answer"]
        assert isinstance(answer, dict)
        self.assertNotIn("pattern", answer)
        self.assertIn("minLength", answer)

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
        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=Transport(_response({"output_text": '{"wrong":true}'})),
            validator=Validator(fail=True),
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(result.error_class, "GEMINI_STRUCTURED_OUTPUT_INVALID")
        self.assertEqual(writer.calls, [])

    async def test_http_outcomes_are_normalized_single_attempt(self) -> None:
        cases = (
            (
                401,
                {"error": {"status": "UNAUTHENTICATED"}},
                ProviderOutcome.AUTH_FAILURE,
            ),
            (
                429,
                {"error": {"status": "RESOURCE_EXHAUSTED"}},
                ProviderOutcome.RATE_LIMITED,
            ),
            (
                400,
                {"error": {"status": "QUOTA_EXHAUSTED"}},
                ProviderOutcome.QUOTA_EXHAUSTED,
            ),
            (
                500,
                {"error": {"status": "INTERNAL"}},
                ProviderOutcome.TRANSIENT_ERROR,
            ),
        )
        for status, payload, expected in cases:
            with self.subTest(status=status):
                transport = Transport(_response(payload, status=status))
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(len(transport.calls), 1)

    async def test_rate_limit_keeps_retry_after_as_metadata(self) -> None:
        transport = Transport(
            _response(
                {"error": {"status": "RESOURCE_EXHAUSTED"}},
                status=429,
                headers={"Retry-After": "5"},
            )
        )
        result = await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(result.outcome, ProviderOutcome.RATE_LIMITED)
        self.assertEqual(result.retry_after_seconds, 5)
        self.assertEqual(len(transport.calls), 1)

    async def test_malformed_missing_output_and_ambiguity_fail_closed(self) -> None:
        malformed = await self._adapter(
            transport=Transport(
                ProviderHttpResponse(status_code=200, headers={}, body=b"not-json")
            )
        ).invoke(self.request)
        self.assertEqual(malformed.outcome, ProviderOutcome.MALFORMED_OUTPUT)

        missing = await self._adapter(
            transport=Transport(_response({"steps": []}))
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
            target=ProviderTarget("kimi", "gemini-fixture", "PROVIDER_DEFAULT"),
            cycle=1,
            attempt_index=1,
            request_reference=self.request.request_reference,
            request_fingerprint=self.request.request_fingerprint,
            missing_requirements=self.request.missing_requirements,
        )
        transport = Transport(_response({"output_text": "x"}))
        result = await self._adapter(transport=transport).invoke(request)
        self.assertEqual(result.error_class, "GEMINI_TARGET_PROVIDER_MISMATCH")
        self.assertEqual(transport.calls, [])

        credential = ProviderCredential(self.secret)
        writer = Writer()
        transport = Transport(_response({"output_text": "ok"}))
        adapter = GeminiInteractionsAdapter(
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
    ) -> GeminiInteractionsAdapter:
        return GeminiInteractionsAdapter(
            credential=ProviderCredential(self.secret),
            payload_reader=reader
            or Reader(ProviderInvocationPayload(input_text="request")),
            response_writer=writer or Writer(),
            transport=transport
            or Transport(_response({"output_text": "ok"})),
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
