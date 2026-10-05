"""Anthropic Messages HTTP single-attempt adapter."""

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

ANTHROPIC_MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 16384

_ERROR_TOKEN = re.compile(r"[^A-Za-z0-9_.-]+")


class AnthropicMessagesAdapter(ProviderAdapter):
    """Execute one exact Anthropic Messages API attempt."""

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
        endpoint = endpoint_override or ANTHROPIC_MESSAGES_ENDPOINT
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
        if request.target.provider_id != "anthropic":
            return _failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class="ANTHROPIC_TARGET_PROVIDER_MISMATCH",
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
            "x-api-key": self._credential.secret_value,
            "anthropic-version": ANTHROPIC_VERSION,
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
                error_class="ANTHROPIC_INVALID_JSON_RESPONSE",
                started=started,
            )

        provider_failure = _provider_failure(request, decoded, started=started)
        if provider_failure is not None:
            return provider_failure

        stop_reason = _safe_token(decoded.get("stop_reason"))
        if stop_reason is not None and stop_reason.casefold() in {"refusal", "max_tokens"}:
            suffix = "REFUSAL" if stop_reason.casefold() == "refusal" else "RESPONSE_INCOMPLETE"
            return _failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class=f"ANTHROPIC_{suffix}",
                started=started,
                usage=_usage(decoded),
            )

        output_text = _output_text(decoded)
        if output_text is None:
            return _failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class="ANTHROPIC_OUTPUT_TEXT_MISSING",
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
                    error_class="ANTHROPIC_STRUCTURED_OUTPUT_INVALID",
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
    body: dict[str, object] = {
        "model": request.target.model_id,
        "max_tokens": ANTHROPIC_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": materialized.input_text,
            }
        ],
    }
    if materialized.instructions is not None:
        body["system"] = materialized.instructions
    structured = materialized.structured_output
    if structured is not None:
        schema = json.loads(structured.schema_json)
        if not isinstance(schema, Mapping):
            raise ValueError("structured schema root must be an object")
        body["output_config"] = {
            "format": {
                "type": "json_schema",
                "schema": dict(schema),
            }
        }
    return body


def _output_text(decoded: Mapping[str, object]) -> str | None:
    content = decoded.get("content")
    if not isinstance(content, list):
        return None
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
    error_type = _error_type(response.body)
    token = error_type.casefold() if error_type is not None else ""
    if response.status_code in {401, 403} or any(
        marker in token for marker in ("authentication", "permission", "forbidden")
    ):
        outcome = ProviderOutcome.AUTH_FAILURE
    elif response.status_code == 429 or "rate_limit" in token:
        outcome = ProviderOutcome.RATE_LIMITED
    elif response.status_code == 408:
        outcome = ProviderOutcome.TIMEOUT
    elif response.status_code >= 500 or "overloaded" in token:
        outcome = ProviderOutcome.TRANSIENT_ERROR
    else:
        outcome = ProviderOutcome.TERMINAL_ERROR
    suffix = error_type or f"HTTP_{response.status_code}"
    return _failure(
        request,
        outcome,
        error_class=f"ANTHROPIC_{suffix}",
        started=started,
        retry_after_seconds=_retry_after_seconds(response.headers),
    )


def _provider_failure(
    request: ProviderAttemptRequest,
    decoded: Mapping[str, object],
    *,
    started: float,
) -> ProviderAttemptResult | None:
    if decoded.get("type") != "error":
        return None
    error = decoded.get("error")
    error_type = _safe_token(error.get("type")) if isinstance(error, Mapping) else None
    token = error_type.casefold() if error_type is not None else ""
    if any(marker in token for marker in ("authentication", "permission")):
        outcome = ProviderOutcome.AUTH_FAILURE
    elif "rate_limit" in token:
        outcome = ProviderOutcome.RATE_LIMITED
    elif "overloaded" in token:
        outcome = ProviderOutcome.TRANSIENT_ERROR
    else:
        outcome = ProviderOutcome.TERMINAL_ERROR
    return _failure(
        request,
        outcome,
        error_class=f"ANTHROPIC_{error_type or 'PROVIDER_FAILED'}",
        started=started,
        usage=_usage(decoded),
    )


def _usage(decoded: Mapping[str, object]) -> ProviderUsage:
    raw = decoded.get("usage")
    if not isinstance(raw, Mapping):
        return ProviderUsage()
    base_input = _nonnegative_int(raw.get("input_tokens"))
    cache_creation = _nonnegative_int(raw.get("cache_creation_input_tokens"))
    cache_read = _nonnegative_int(raw.get("cache_read_input_tokens"))
    output = _nonnegative_int(raw.get("output_tokens"))
    total_input = base_input + cache_creation + cache_read
    native: list[NativeUsage] = []
    if cache_read:
        native.append(NativeUsage("cached_input_tokens", Decimal(cache_read), "tokens"))
    total = total_input + output
    if total:
        native.append(NativeUsage("provider_total_tokens", Decimal(total), "tokens"))
    return ProviderUsage(
        input_tokens=total_input,
        output_tokens=output,
        native=tuple(native),
    )


def _error_type(raw: bytes) -> str | None:
    decoded = _decode_json_object(raw[:65536])
    if decoded is None:
        return None
    error = decoded.get("error")
    if not isinstance(error, Mapping):
        return None
    return _safe_token(error.get("type"))


def _decode_json_object(raw: bytes) -> Mapping[str, object] | None:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return cast(Mapping[str, object], decoded)


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


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
        error_class=_safe_token(error_class) or "ANTHROPIC_PROVIDER_ERROR",
        retry_after_seconds=retry_after_seconds,
        usage=usage if usage is not None else ProviderUsage(),
    )
