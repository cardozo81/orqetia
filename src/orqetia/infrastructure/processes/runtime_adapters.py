"""Just-in-time provider adapter composition from durable credential metadata."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from orqetia.control_plane.provider_credentials import (
    ProviderCredentialRepository,
    ProviderCredentialStatus,
    ProviderSecretStore,
)
from orqetia.execution import ProviderAttempt
from orqetia.providers import (
    AnthropicMessagesAdapter,
    CohereChatV2Adapter,
    DeepSeekResponsesAdapter,
    GeminiInteractionsAdapter,
    GitHubCopilotAdapter,
    KimiChatCompletionsAdapter,
    MiMoResponsesAdapter,
    MistralChatCompletionsAdapter,
    OpenAIResponsesAdapter,
    ProviderAdapter,
    ProviderCredential,
    ProviderRequestPayloadReader,
    ProviderResponsePayloadWriter,
    QwenChatCompletionsAdapter,
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
    ) -> None:
        self._credentials = credentials
        self._secrets = secrets
        self._artifacts = artifacts
        self._structured_validator = structured_validator
        self._clock = clock

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

        secret = await self._secrets.get(metadata.secret_reference)
        credential = ProviderCredential(secret.reveal())
        provider_id = attempt.target.provider_id
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
