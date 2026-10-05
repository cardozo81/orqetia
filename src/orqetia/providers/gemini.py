"""Gemini Interactions HTTP single-attempt adapter."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from decimal import Decimal
from typing import cast

from .io import (
    ProviderCredential,
    ProviderInvocationPayload,
    ProviderRequestPayloadReader,
    ProviderResponsePayloadWriter,
    StructuredOutputValidator,
)
from .protocol import (
    NativeUsage,
    OutputKind,
    ProviderAdapter,
    ProviderAttemptRequest,
    ProviderAttemptResult,
    ProviderOutcome,
    ProviderUsage,
)
from .responses import (
    HttpxProviderHttpTransport,
    ProviderDispatchAmbiguousError,
    ProviderHttpResponse,
    ProviderHttpTransport,
)

GEMINI_INTERACTIONS_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/interactions"
)

_ERROR_TOKEN = re.compile(r"[^A-Za-z0-9_.-]+")
_GEMINI_SCHEMA_KEYWORDS = frozenset(
    {
        "$id",
        "$defs",
        "$ref",
        "$anchor",
        "type",
        "format",
        "title",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "oneOf",
        "properties",
        "additionalProperties",
        "required",
        "propertyOrdering",
    }
)


class GeminiInteractionsAdapter(ProviderAdapter):
    """Execute one exact Gemini Interactions API attempt."""

    def __init__(
        self,
        *,
        credential: ProviderCredential,
        payload_reader: ProviderRequestPayloadReader,
        response_writer: ProviderResponsePayloadWriter,
        transport: ProviderHttpTransport | None = None,
        structured_validator: StructuredOutputValidator | None = None,
        timeout_seconds: float = 180.0,
        endpoint_override: str | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        endpoint = endpoint_override or GEMINI_INTERACTIONS_ENDPOINT
        if not endpoint.startswith("https://"):
            raise ValueError("provider endpoint must use https")
        self._credential = credential
        self._payload_reader = payload_reader
        self._response_writer = response_writer
        self._transport = transport or HttpxProviderHttpTransport()
        self._structured_validator = structured_validator
        self._timeout_seconds = float(timeout_seconds)
        self._endpoint = endpoint

    async def invoke(self, request: ProviderAttemptRequest) -> ProviderAttemptResult:
        started = time.perf_counter()
        if request.target.provider_id != "gemini":
            return _failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class="GEMINI_TARGET_PROVIDER_MISMATCH",
                started=started,
            )

        try:
            materialized = await self._payload_reader.load(
                request_reference=request.request_reference,
                request_fingerprint=request.request_fingerprint,
            )
        except Exception as exc:
            return _failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class=f"REQUEST_PAYLOAD_{type(exc).__name__.upper()}",
                started=started,
            )

        if materialized.structured_output is not None and self._structured_validator is None:
            return _failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class="STRUCTURED_VALIDATOR_UNAVAILABLE",
                started=started,
            )

        body = _wire_body(request, materialized)
        headers = {
            "x-goog-api-key": self._credential.secret_value,
            "Content-Type": "application/json",
        }
        try:
            response = await self._transport.post_json(
                url=self._endpoint,
                headers=headers,
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        except ProviderDispatchAmbiguousError:
            raise
        except Exception as exc:
            raise ProviderDispatchAmbiguousError(
                f"provider transport outcome ambiguous: {type(exc).__name__}"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            return _http_failure(request, response, started=started)

        decoded = _decode_json_object(response.body)
        if decoded is None:
            return _failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class="GEMINI_INVALID_JSON_RESPONSE",
                started=started,
            )

        provider_failure = _provider_failure(request, decoded, started=started)
        if provider_failure is not None:
            return provider_failure

        output_text = _output_text(decoded)
        if output_text is None:
            return _failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class="GEMINI_OUTPUT_TEXT_MISSING",
                started=started,
                usage=_usage(decoded),
            )

        output_kind = OutputKind.TEXT
        durable_content = output_text
        structured = materialized.structured_output
        if structured is not None:
            try:
                structured_value = json.loads(output_text)
                validator = cast(StructuredOutputValidator, self._structured_validator)
                validator.validate(
                    schema_json=structured.schema_json,
                    value=structured_value,
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                return _failure(
                    request,
                    ProviderOutcome.MALFORMED_OUTPUT,
                    error_class="GEMINI_STRUCTURED_OUTPUT_INVALID",
                    started=started,
                    usage=_usage(decoded),
                )
            output_kind = OutputKind.STRUCTURED
            durable_content = json.dumps(
                structured_value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        response_reference = await self._response_writer.store(
            attempt_id=request.attempt_id,
            target=request.target,
            output_kind=output_kind,
            content=durable_content,
        )
        return ProviderAttemptResult(
            attempt_id=request.attempt_id,
            outcome=ProviderOutcome.SUCCESS,
            output_kind=output_kind,
            accepted_requirements=request.missing_requirements,
            missing_requirements=(),
            simulated_latency_ms=_elapsed_ms(started),
            response_reference=response_reference,
            usage=_usage(decoded),
        )


def _wire_body(
    request: ProviderAttemptRequest,
    materialized: ProviderInvocationPayload,
) -> dict[str, object]:
    parts: list[str] = []
    if materialized.instructions is not None:
        parts.append(materialized.instructions)
    parts.append(materialized.input_text)
    body: dict[str, object] = {
        "model": request.target.model_id,
        "input": "\n\n".join(parts),
    }
    structured = materialized.structured_output
    if structured is not None:
        schema = json.loads(structured.schema_json)
        if not isinstance(schema, Mapping):
            raise ValueError("structured schema root must be an object")
        body["response_format"] = {
            "type": "text",
            "mime_type": "application/json",
            "schema": _gemini_wire_schema(schema),
        }
    return body


def _gemini_wire_schema(schema: Mapping[str, object]) -> dict[str, object]:
    projected = _project_schema_value(schema)
    if not isinstance(projected, dict):
        raise TypeError("Gemini wire schema must remain an object")
    return projected


def _project_schema_value(value: object, parent: str | None = None) -> object:
    if isinstance(value, list):
        return [_project_schema_value(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    if parent in {"properties", "$defs"}:
        return {
            str(key): _project_schema_value(item)
            for key, item in value.items()
        }
    return {
        str(key): _project_schema_value(item, str(key))
        for key, item in value.items()
        if str(key) in _GEMINI_SCHEMA_KEYWORDS
    }


def _output_text(decoded: Mapping[str, object]) -> str | None:
    direct = decoded.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    steps = decoded.get("steps")
    if not isinstance(steps, list):
        return None
    for step in reversed(steps):
        if not isinstance(step, Mapping) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, Mapping) or item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _http_failure(
    request: ProviderAttemptRequest,
    response: ProviderHttpResponse,
    *,
    started: float,
) -> ProviderAttemptResult:
    error_type, error_code = _error_tokens(response.body)
    token = " ".join(item.casefold() for item in (error_type, error_code) if item)
    auth = any(
        marker in token
        for marker in ("unauthenticated", "permission_denied", "api_key", "forbidden")
    )
    quota = any(marker in token for marker in ("quota", "resource_exhausted"))
    if response.status_code == 429:
        outcome = ProviderOutcome.RATE_LIMITED
    elif response.status_code in {401, 403} or auth:
        outcome = ProviderOutcome.AUTH_FAILURE
    elif quota:
        outcome = ProviderOutcome.QUOTA_EXHAUSTED
    elif response.status_code == 408:
        outcome = ProviderOutcome.TIMEOUT
    elif response.status_code >= 500:
        outcome = ProviderOutcome.TRANSIENT_ERROR
    else:
        outcome = ProviderOutcome.TERMINAL_ERROR
    suffix = error_code or error_type or f"HTTP_{response.status_code}"
    return _failure(
        request,
        outcome,
        error_class=f"GEMINI_{suffix}",
        started=started,
        retry_after_seconds=_retry_after_seconds(response.headers),
    )


def _provider_failure(
    request: ProviderAttemptRequest,
    decoded: Mapping[str, object],
    *,
    started: float,
) -> ProviderAttemptResult | None:
    error_type, error_code = _decoded_error_tokens(decoded)
    if error_type is None and error_code is None:
        return None
    token = " ".join(item.casefold() for item in (error_type, error_code) if item)
    if any(marker in token for marker in ("unauthenticated", "permission_denied")):
        outcome = ProviderOutcome.AUTH_FAILURE
    elif any(marker in token for marker in ("quota", "resource_exhausted")):
        outcome = ProviderOutcome.QUOTA_EXHAUSTED
    else:
        outcome = ProviderOutcome.TERMINAL_ERROR
    return _failure(
        request,
        outcome,
        error_class=f"GEMINI_{error_code or error_type or 'PROVIDER_FAILED'}",
        started=started,
        usage=_usage(decoded),
    )


def _usage(decoded: Mapping[str, object]) -> ProviderUsage:
    raw = decoded.get("usage")
    if not isinstance(raw, Mapping):
        raw = decoded.get("usage_metadata")
    if not isinstance(raw, Mapping):
        return ProviderUsage()
    input_tokens = _first_int(raw, "prompt_tokens", "prompt_token_count")
    output_tokens = _first_int(raw, "completion_tokens", "candidates_token_count")
    cached = _first_int(raw, "cached_content_token_count", "cached_tokens")
    reasoning = _first_int(raw, "thoughts_token_count", "reasoning_tokens")
    total = _first_int(raw, "total_tokens", "total_token_count")
    native: list[NativeUsage] = []
    if cached:
        native.append(NativeUsage("cached_input_tokens", Decimal(cached), "tokens"))
    if reasoning:
        native.append(NativeUsage("reasoning_output_tokens", Decimal(reasoning), "tokens"))
    if total:
        native.append(NativeUsage("provider_total_tokens", Decimal(total), "tokens"))
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        native=tuple(native),
    )


def _first_int(raw: Mapping[str, object], primary: str, alternate: str) -> int:
    value = raw.get(primary)
    if value is None:
        value = raw.get(alternate)
    return _nonnegative_int(value)


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _decode_json_object(raw: bytes) -> Mapping[str, object] | None:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return cast(Mapping[str, object], decoded)


def _decoded_error_tokens(
    decoded: Mapping[str, object],
) -> tuple[str | None, str | None]:
    error = decoded.get("error")
    if not isinstance(error, Mapping):
        return None, None
    return _safe_token(error.get("status")), _safe_token(error.get("code"))


def _error_tokens(raw: bytes) -> tuple[str | None, str | None]:
    decoded = _decode_json_object(raw[:65536])
    if decoded is None:
        return None, None
    return _decoded_error_tokens(decoded)


def _safe_token(value: object) -> str | None:
    if value is None:
        return None
    token = _ERROR_TOKEN.sub("_", str(value).strip())[:96].strip("_")
    return token or None


def _retry_after_seconds(headers: Mapping[str, str]) -> int | None:
    value = next(
        (item for key, item in headers.items() if key.casefold() == "retry-after"),
        None,
    )
    if value is None or not value.strip().isdigit():
        return None
    return int(value.strip())


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _failure(
    request: ProviderAttemptRequest,
    outcome: ProviderOutcome,
    *,
    error_class: str,
    started: float,
    retry_after_seconds: int | None = None,
    usage: ProviderUsage | None = None,
) -> ProviderAttemptResult:
    return ProviderAttemptResult(
        attempt_id=request.attempt_id,
        outcome=outcome,
        output_kind=(
            OutputKind.MALFORMED
            if outcome is ProviderOutcome.MALFORMED_OUTPUT
            else OutputKind.NONE
        ),
        accepted_requirements=(),
        missing_requirements=request.missing_requirements,
        simulated_latency_ms=_elapsed_ms(started),
        error_class=_safe_token(error_class) or "GEMINI_PROVIDER_ERROR",
        retry_after_seconds=retry_after_seconds,
        usage=usage if usage is not None else ProviderUsage(),
    )
