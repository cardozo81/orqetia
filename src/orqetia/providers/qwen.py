"""Alibaba Qwen Chat Completions single-attempt adapter."""

from .chat_completions import ChatCompletionsHttpAdapter

QWEN_CHAT_COMPLETIONS_ENDPOINT = (
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1/chat/completions"
)


class QwenChatCompletionsAdapter(ChatCompletionsHttpAdapter):
    """Execute one exact Alibaba Qwen Chat Completions attempt."""

    provider_id = "qwen"
    error_prefix = "QWEN"
    endpoint = QWEN_CHAT_COMPLETIONS_ENDPOINT
    strict_json_schema = True
    quota_error_markers = (
        "allocationquota.freetieronly",
        "free_tier",
        "freequota",
    )
