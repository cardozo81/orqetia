"""Just-in-time provider adapter composition from durable credential metadata."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from orqetia.control_plane.provider_credentials import (
    ProviderCredentialRepository,
    ProviderCredentialStatus,
    ProviderSecretStore,
)
from orqetia.execution import ProviderAttempt
from orqetia.providers import (
    ORQETIA_TEST_PROVIDER,
    AnthropicMessagesAdapter,
    CohereChatV2Adapter,
    DeepSeekResponsesAdapter,
    DeterministicTestProvider,
    GeminiInteractionsAdapter,
    GitHubCopilotAdapter,
    KimiChatCompletionsAdapter,
    MiMoResponsesAdapter,
    MistralChatCompletionsAdapter,
    OpenAIResponsesAdapter,
    OutputKind,
    ProviderAdapter,
    ProviderAttemptRequest,
    ProviderAttemptResult,
    ProviderCredential,
    ProviderRequestPayloadReader,
    ProviderResponsePayloadWriter,
    ProviderTarget,
    ProviderUsage,
    QwenChatCompletionsAdapter,
    SimulatorFixture,
    SimulatorScenario,
    SimulatorStep,
    StructuredOutputValidator,
    XAIResponsesAdapter,
)


class ProviderArtifactPorts(
    ProviderRequestPayloadReader,
    ProviderResponsePayloadWriter,
    Protocol,
):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


Clock = Callable[[], datetime]


class _DurableLocalTestProvider:
    """Persist deterministic simulator output through the canonical artifact port."""

    def __init__(
        self,
        *,
        delegate: DeterministicTestProvider,
        artifacts: ProviderArtifactPorts,
    ) -> None:
        self._delegate = delegate
        self._artifacts = artifacts

    async def invoke(self, request: ProviderAttemptRequest) -> ProviderAttemptResult:
        result = await self._delegate.invoke(request)
        if result.output_kind not in {OutputKind.TEXT, OutputKind.STRUCTURED}:
            return result

        content = (
            "{\"provider\":\"ORQETIA_TEST_PROVIDER\","
            f"\"attempt_id\":\"{request.attempt_id}\","
            "\"status\":\"success\"}"
            if result.output_kind is OutputKind.STRUCTURED
            else (
                "ORQETIA_TEST_PROVIDER deterministic success "
                f"for attempt {request.attempt_id}"
            )
        )
        reference = await self._artifacts.store(
            attempt_id=request.attempt_id,
            target=request.target,
            output_kind=result.output_kind,
            content=content,
        )
        return replace(result, response_reference=reference)


class DurableProviderAdapterResolver:
    """Resolve secret material only for the exact PREPARED attempt being dispatched."""

    def __init__(
        self,
        *,
        credentials: ProviderCredentialRepository,
        secrets: ProviderSecretStore,
        artifacts: ProviderArtifactPorts,
        structured_validator: StructuredOutputValidator | None = None,
        clock: Clock = _utc_now,
        allow_test_provider: bool = False,
    ) -> None:
        self._credentials = credentials
        self._secrets = secrets
        self._artifacts = artifacts
        self._structured_validator = structured_validator
        self._clock = clock
        self._allow_test_provider = allow_test_provider

    async def __call__(self, attempt: ProviderAttempt) -> ProviderAdapter:
        credential_id = attempt.provider_credential_id
        account_id = attempt.provider_account_id
        if credential_id is None or account_id is None:
            raise LookupError("provider attempt has no credential provenance")
        metadata = await self._credentials.get(credential_id)
        if metadata is None:
            raise LookupError("provider credential metadata not found")
        if metadata.status is not ProviderCredentialStatus.ACTIVE:
            raise PermissionError("provider credential is not active")
        if metadata.provider_id != attempt.target.provider_id:
            raise PermissionError("provider credential provider mismatch")
        if metadata.provider_account_id != account_id:
            raise PermissionError("provider credential account mismatch")
        now = self._clock()
        if metadata.expires_at is not None and metadata.expires_at <= now:
            raise PermissionError("provider credential is expired")

        provider_id = attempt.target.provider_id
        if provider_id == ORQETIA_TEST_PROVIDER:
            if not self._allow_test_provider:
                raise PermissionError(
                    "ORQETIA_TEST_PROVIDER is disabled outside local/test composition"
                )
            return self._build_local_test_provider(attempt)

        secret = await self._secrets.get(metadata.secret_reference)
        credential = ProviderCredential(secret.reveal())
        if provider_id == "openai":
            return OpenAIResponsesAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "deepseek":
            return DeepSeekResponsesAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "mimo":
            return MiMoResponsesAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "xai":
            return XAIResponsesAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "qwen":
            return QwenChatCompletionsAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "mistral":
            return MistralChatCompletionsAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "kimi":
            return KimiChatCompletionsAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "gemini":
            return GeminiInteractionsAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "anthropic":
            return AnthropicMessagesAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "cohere":
            return CohereChatV2Adapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        if provider_id == "copilot":
            return GitHubCopilotAdapter(
                credential=credential,
                payload_reader=self._artifacts,
                response_writer=self._artifacts,
                structured_validator=self._structured_validator,
            )
        raise LookupError("provider has no synchronous runtime adapter")

    def _build_local_test_provider(
        self,
        attempt: ProviderAttempt,
    ) -> ProviderAdapter:
        target = ProviderTarget(
            provider_id=attempt.target.provider_id,
            model_id=attempt.target.model_id,
            reasoning_profile=attempt.target.reasoning_profile,
        )
        fixture = SimulatorFixture(
            fixture_id=f"local-runtime-{attempt.attempt_id}",
            seed="orqetia-local-runtime-v1",
            candidates=(),
            steps=(
                SimulatorStep(
                    scenario=(
                        SimulatorScenario.REQUIREMENT_NOT_SATISFIED
                        if attempt.operation == "LOCAL_ESCALATION"
                        and attempt.target.model_id == "economy"
                        else SimulatorScenario.SUCCESS
                    ),
                    expected_cycle=attempt.cycle,
                    expected_attempt_index=attempt.attempt_index,
                    expected_target=target,
                    simulated_latency_ms=1,
                    usage=ProviderUsage(input_tokens=12, output_tokens=8),
                ),
            ),
        )
        return _DurableLocalTestProvider(
            delegate=DeterministicTestProvider(fixture),
            artifacts=self._artifacts,
        )
