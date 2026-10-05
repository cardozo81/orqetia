"""Mistral Chat Completions single-attempt adapter."""

from .chat_completions import ChatCompletionsHttpAdapter

MISTRAL_CHAT_COMPLETIONS_ENDPOINT = "https://api.mistral.ai/v1/chat/completions"


class MistralChatCompletionsAdapter(ChatCompletionsHttpAdapter):
    """Execute one exact Mistral Chat Completions attempt."""

    provider_id = "mistral"
    error_prefix = "MISTRAL"
    endpoint = MISTRAL_CHAT_COMPLETIONS_ENDPOINT
    strict_json_schema = True
    service_tier = "standard_only"
