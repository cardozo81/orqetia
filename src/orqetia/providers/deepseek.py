"""DeepSeek Responses API single-attempt adapter."""

from .responses import ResponsesHttpAdapter

DEEPSEEK_RESPONSES_ENDPOINT = "https://api.deepseek.com/responses"


class DeepSeekResponsesAdapter(ResponsesHttpAdapter):
    """Execute one exact DeepSeek Responses API attempt."""

    provider_id = "deepseek"
    error_prefix = "DEEPSEEK"
    endpoint = DEEPSEEK_RESPONSES_ENDPOINT
