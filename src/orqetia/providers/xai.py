"""xAI / Grok Responses API single-attempt adapter."""

from .responses import ResponsesHttpAdapter

XAI_RESPONSES_ENDPOINT = "https://api.x.ai/v1/responses"


class XAIResponsesAdapter(ResponsesHttpAdapter):
    """Execute one exact xAI Responses API attempt."""

    provider_id = "xai"
    error_prefix = "XAI"
    endpoint = XAI_RESPONSES_ENDPOINT
    include_service_tier = True
    include_store = True
    strict_json_schema = True
