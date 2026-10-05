"""Kimi / Moonshot Chat Completions single-attempt adapter."""

from __future__ import annotations

from collections.abc import Mapping

from .chat_completions import ChatCompletionsHttpAdapter

KIMI_CHAT_COMPLETIONS_ENDPOINT = "https://api.moonshot.ai/v1/chat/completions"

_KIMI_WIRE_DROPPED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }
)


class KimiChatCompletionsAdapter(ChatCompletionsHttpAdapter):
    """Execute one exact Kimi international Chat Completions attempt."""

    provider_id = "kimi"
    error_prefix = "KIMI"
    endpoint = KIMI_CHAT_COMPLETIONS_ENDPOINT
    strict_json_schema = True
    supports_reasoning_effort = True

    def _wire_schema(self, schema: Mapping[str, object]) -> dict[str, object]:
        projected = _project_wire_value(schema)
        if not isinstance(projected, dict):
            raise TypeError("projected Kimi schema must remain an object")
        return projected


def _project_wire_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _project_wire_value(item)
            for key, item in value.items()
            if str(key) not in _KIMI_WIRE_DROPPED_KEYWORDS
        }
    if isinstance(value, list):
        return [_project_wire_value(item) for item in value]
    return value
