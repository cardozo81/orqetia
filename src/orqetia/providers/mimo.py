"""Xiaomi MiMo Responses API single-attempt adapter."""

from .responses import ResponsesHttpAdapter

MIMO_RESPONSES_ENDPOINT = "https://api.xiaomimimo.com/v1/responses"


class MiMoResponsesAdapter(ResponsesHttpAdapter):
    """Execute one exact Xiaomi MiMo Responses API attempt."""

    provider_id = "mimo"
    error_prefix = "MIMO"
    endpoint = MIMO_RESPONSES_ENDPOINT
    credential_header = "api-key"
    credential_prefix = ""
    structured_wire_mode = "json_object"
