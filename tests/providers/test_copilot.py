from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import patch
from uuid import uuid7

from orqetia.providers import (
    CopilotSdkAuthenticationError,
    CopilotSdkRateLimitError,
    CopilotSdkTimeoutError,
    CopilotSdkUnavailableError,
    GitHubCopilotAdapter,
    GithubCopilotSdkTransport,
    OutputKind,
    ProviderAttemptRequest,
    ProviderCredential,
    ProviderDispatchAmbiguousError,
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
        return "response://durable/copilot/1"


class Transport:
    def __init__(
        self,
        content: str = "provider answer",
        *,
        error: Exception | None = None,
    ) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def send(
        self,
        *,
        github_token: str,
        model: str,
        prompt: str,
        timeout_seconds: float,
    ) -> str:
        self.calls.append(
            {
                "github_token": github_token,
                "model": model,
                "prompt": prompt,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.content


class Validator:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def validate(self, *, schema_json: str, value: object) -> None:
        self.calls.append({"schema_json": schema_json, "value": value})
        if self.fail:
            raise ValueError("fixture schema mismatch")


class GitHubCopilotAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.secret = "fixture-credential-must-never-persist"
        self.target = ProviderTarget("copilot", "auto", "PROVIDER_DEFAULT")
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

    async def test_text_success_uses_exact_target_and_no_native_usage(self) -> None:
        writer = Writer()
        transport = Transport("provider answer")

        result = await self._adapter(
            writer=writer,
            transport=transport,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.TEXT)
        self.assertEqual(result.response_reference, "response://durable/copilot/1")
        self.assertEqual(result.usage.input_tokens, 0)
        self.assertEqual(result.usage.output_tokens, 0)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["model"], "auto")
        self.assertEqual(call["github_token"], self.secret)
        prompt = call["prompt"]
        assert isinstance(prompt, str)
        self.assertIn("Never browse", prompt)
        self.assertIn('"input_text":"request"', prompt)
        self.assertNotIn(self.secret, prompt)
        self.assertEqual(writer.calls[0]["target"], self.target)
        self.assertEqual(writer.calls[0]["content"], "provider answer")

    async def test_structured_prompt_is_inert_and_locally_validated(self) -> None:
        schema_json = json.dumps(
            {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            sort_keys=True,
        )
        reader = Reader(
            ProviderInvocationPayload(
                input_text="Evidence.",
                instructions="Return JSON only.",
                structured_output=StructuredOutputSpec(
                    name="answer",
                    schema_json=schema_json,
                ),
            )
        )
        validator = Validator()
        fence = chr(96) * 3
        transport = Transport(f'{fence}json\n{{"answer":"ok"}}\n{fence}')
        writer = Writer()

        result = await self._adapter(
            reader=reader,
            writer=writer,
            transport=transport,
            validator=validator,
        ).invoke(self.request)

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.STRUCTURED)
        self.assertEqual(validator.calls[0]["schema_json"], schema_json)
        self.assertEqual(writer.calls[0]["content"], '{"answer":"ok"}')
        prompt = transport.calls[0]["prompt"]
        assert isinstance(prompt, str)
        self.assertIn("structured_output", prompt)
        self.assertIn("additionalProperties", prompt)
        self.assertIn("call tools", prompt)

    async def test_invalid_structured_and_empty_output_fail_closed(self) -> None:
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
        invalid = await self._adapter(
            reader=reader,
            writer=writer,
            transport=Transport('{"wrong":true}'),
            validator=Validator(fail=True),
        ).invoke(self.request)
        self.assertEqual(invalid.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(invalid.error_class, "COPILOT_STRUCTURED_OUTPUT_INVALID")
        self.assertEqual(writer.calls, [])

        empty = await self._adapter(transport=Transport("   ")).invoke(self.request)
        self.assertEqual(empty.outcome, ProviderOutcome.MALFORMED_OUTPUT)
        self.assertEqual(empty.error_class, "COPILOT_OUTPUT_TEXT_MISSING")

    async def test_known_sdk_failures_are_normalized_without_retry(self) -> None:
        cases = (
            (CopilotSdkAuthenticationError("auth"), ProviderOutcome.AUTH_FAILURE),
            (CopilotSdkRateLimitError("rate"), ProviderOutcome.RATE_LIMITED),
            (CopilotSdkTimeoutError("timeout"), ProviderOutcome.TIMEOUT),
            (CopilotSdkUnavailableError("sdk"), ProviderOutcome.TERMINAL_ERROR),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                transport = Transport(error=error)
                result = await self._adapter(transport=transport).invoke(self.request)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(len(transport.calls), 1)

    async def test_ambiguous_sdk_failure_is_not_silently_redispatched(self) -> None:
        transport = Transport(error=OSError("connection reset"))
        with self.assertRaises(ProviderDispatchAmbiguousError):
            await self._adapter(transport=transport).invoke(self.request)
        self.assertEqual(len(transport.calls), 1)

    async def test_target_mismatch_fails_before_payload_or_sdk(self) -> None:
        request = ProviderAttemptRequest(
            attempt_id=self.request.attempt_id,
            operation=self.request.operation,
            target=ProviderTarget("cohere", "auto", "PROVIDER_DEFAULT"),
            cycle=1,
            attempt_index=1,
            request_reference=self.request.request_reference,
            request_fingerprint=self.request.request_fingerprint,
            missing_requirements=self.request.missing_requirements,
        )
        reader = Reader(ProviderInvocationPayload(input_text="request"))
        transport = Transport()

        result = await self._adapter(
            reader=reader,
            transport=transport,
        ).invoke(request)

        self.assertEqual(result.outcome, ProviderOutcome.TERMINAL_ERROR)
        self.assertEqual(result.error_class, "COPILOT_TARGET_PROVIDER_MISMATCH")
        self.assertEqual(reader.calls, [])
        self.assertEqual(transport.calls, [])

    async def test_secret_is_not_persisted_or_rendered(self) -> None:
        credential = ProviderCredential(self.secret)
        writer = Writer()
        transport = Transport()
        adapter = GitHubCopilotAdapter(
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

    async def test_official_sdk_shape_disables_implicit_auth_and_tools(self) -> None:
        captured: dict[str, object] = {}

        class FakeSession:
            async def send_and_wait(self, prompt: str, timeout: float) -> object:
                captured["prompt"] = prompt
                captured["timeout"] = timeout
                return types.SimpleNamespace(
                    data=types.SimpleNamespace(content='{"ok":true}')
                )

            async def disconnect(self) -> None:
                captured["disconnected"] = True

        class FakeCopilotClient:
            def __init__(self, options: object) -> None:
                captured["client_options"] = options

            async def start(self) -> None:
                captured["started"] = True

            async def create_session(self, **kwargs: object) -> FakeSession:
                captured["session_kwargs"] = kwargs
                return FakeSession()

            async def stop(self) -> None:
                captured["stopped"] = True

        fake_module = types.ModuleType("copilot")
        fake_module.CopilotClient = FakeCopilotClient  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"copilot": fake_module}):
            result = await GithubCopilotSdkTransport().send(
                github_token="fixture-token",
                model="auto",
                prompt="Return JSON only",
                timeout_seconds=17,
            )

        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(
            {
                "github_token": "fixture-token",
                "use_logged_in_user": False,
            },
            captured["client_options"],
        )
        self.assertEqual(
            {"model": "auto", "available_tools": []},
            captured["session_kwargs"],
        )
        self.assertEqual("Return JSON only", captured["prompt"])
        self.assertEqual(17.0, captured["timeout"])
        self.assertTrue(captured["started"])
        self.assertTrue(captured["disconnected"])
        self.assertTrue(captured["stopped"])

    def _adapter(
        self,
        *,
        reader: Reader | None = None,
        writer: Writer | None = None,
        transport: Transport | None = None,
        validator: Validator | None = None,
    ) -> GitHubCopilotAdapter:
        return GitHubCopilotAdapter(
            credential=ProviderCredential(self.secret),
            payload_reader=reader
            or Reader(ProviderInvocationPayload(input_text="request")),
            response_writer=writer or Writer(),
            transport=transport or Transport(),
            structured_validator=validator,
            timeout_seconds=30,
        )


if __name__ == "__main__":
    unittest.main()
