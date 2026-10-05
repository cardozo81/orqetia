"""OpenAI Responses API single-attempt adapter."""

from .responses import ResponsesHttpAdapter

OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"


class OpenAIResponsesAdapter(ResponsesHttpAdapter):
    """Execute one exact OpenAI Responses API attempt."""

    provider_id = "openai"
    error_prefix = "OPENAI"
    endpoint = OPENAI_RESPONSES_ENDPOINT
    include_service_tier = True
    include_store = True
    include_empty_tools = True
    strict_json_schema = True
