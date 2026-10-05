"""Provider-neutral Chat Completions HTTP single-attempt adapter."""

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

_ERROR_TOKEN = re.compile(r"[^A-Za-z0-9_.-]+")


class ChatCompletionsHttpAdapter(ProviderAdapter):
    """Execute one exact OpenAI-compatible Chat Completions HTTP attempt."""

    provider_id: str
    error_prefix: str
    endpoint: str
    strict_json_schema = False
    credit_error_markers = ("credit", "balance", "arrearage")
    quota_error_markers = ("quota_exhausted", "freetieronly")
    auth_error_markers = (
        "auth",
        "permission",
        "forbidden",
        "api_key",
        "apikey",
        "accessdenied",
        "not_authorized",
    )

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
        selected_endpoint = endpoint_override or self.endpoint
        if not selected_endpoint.startswith("https://"):
            raise ValueError("provider endpoint must use https")
        self._credential = credential
        self._payload_reader = payload_reader
        self._response_writer = response_writer
        self._transport = transport or HttpxProviderHttpTransport()
        self._structured_validator = structured_validator
        self._timeout_seconds = float(timeout_seconds)
        self._endpoint = selected_endpoint

    async def invoke(self, request: ProviderAttemptRequest) -> ProviderAttemptResult:
        started = time.perf_counter()
        if request.target.provider_id != self.provider_id:
            return self._failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class=f"{self.error_prefix}_TARGET_PROVIDER_MISMATCH",
                started=started,
            )

        try:
            materialized = await self._payload_reader.load(
                request_reference=request.request_reference,
                request_fingerprint=request.request_fingerprint,
            )
        except Exception as exc:
            return self._failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class=f"REQUEST_PAYLOAD_{type(exc).__name__.upper()}",
                started=started,
            )

        if materialized.structured_output is not None and self._structured_validator is None:
            return self._failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class="STRUCTURED_VALIDATOR_UNAVAILABLE",
                started=started,
            )

        wire_body = self._wire_body(request, materialized)
        headers = {
            "Authorization": f"Bearer {self._credential.secret_value}",
            "Content-Type": "application/json",
        }
        try:
            response = await self._transport.post_json(
                url=self._endpoint,
                headers=headers,
                body=wire_body,
                timeout_seconds=self._timeout_seconds,
            )
        except ProviderDispatchAmbiguousError:
            raise
        except Exception as exc:
            raise ProviderDispatchAmbiguousError(
                f"provider transport outcome ambiguous: {type(exc).__name__}"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            return self._http_failure(request, response, started=started)

        decoded = _decode_json_object(response.body)
        if decoded is None:
            return self._failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class=f"{self.error_prefix}_INVALID_JSON_RESPONSE",
                started=started,
            )

        provider_failure = self._provider_failure(request, decoded, started=started)
        if provider_failure is not None:
            return provider_failure

        choice = _first_choice(decoded)
        if choice is None:
            return self._failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class=f"{self.error_prefix}_OUTPUT_TEXT_MISSING",
                started=started,
                usage=_usage(decoded),
            )

        finish_reason = _safe_token(choice.get("finish_reason"))
        if finish_reason is not None and finish_reason.casefold() != "stop":
            return self._failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class=f"{self.error_prefix}_RESPONSE_INCOMPLETE",
                started=started,
                usage=_usage(decoded),
            )

        output_text = _choice_text(choice)
        if output_text is None:
            return self._failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class=f"{self.error_prefix}_OUTPUT_TEXT_MISSING",
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
                return self._failure(
                    request,
                    ProviderOutcome.MALFORMED_OUTPUT,
                    error_class=f"{self.error_prefix}_STRUCTURED_OUTPUT_INVALID",
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
        self,
        request: ProviderAttemptRequest,
        materialized: ProviderInvocationPayload,
    ) -> dict[str, object]:
        messages: list[dict[str, str]] = []
        if materialized.instructions is not None:
            messages.append({"role": "system", "content": materialized.instructions})
        messages.append({"role": "user", "content": materialized.input_text})
        body: dict[str, object] = {
            "model": request.target.model_id,
            "messages": messages,
        }
        structured = materialized.structured_output
        if structured is not None:
            schema = json.loads(structured.schema_json)
            schema_config: dict[str, object] = {
                "name": structured.name,
                "schema": schema,
            }
            if self.strict_json_schema:
                schema_config["strict"] = True
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": schema_config,
            }
        return body

    def _http_failure(
        self,
        request: ProviderAttemptRequest,
        response: ProviderHttpResponse,
        *,
        started: float,
    ) -> ProviderAttemptResult:
        error_type, error_code = _error_tokens(response.body)
        token = " ".join(item.casefold() for item in (error_type, error_code) if item)
        if any(marker in token for marker in self.credit_error_markers):
            outcome = ProviderOutcome.CREDIT_EXHAUSTED
        elif any(marker in token for marker in self.quota_error_markers):
            outcome = ProviderOutcome.QUOTA_EXHAUSTED
        elif response.status_code in {401, 403} or any(
            marker in token for marker in self.auth_error_markers
        ):
            outcome = ProviderOutcome.AUTH_FAILURE
        elif response.status_code == 429:
            outcome = ProviderOutcome.RATE_LIMITED
        elif response.status_code == 408:
            outcome = ProviderOutcome.TIMEOUT
        elif response.status_code >= 500:
            outcome = ProviderOutcome.TRANSIENT_ERROR
        else:
            outcome = ProviderOutcome.TERMINAL_ERROR

        suffix = error_code or error_type or f"HTTP_{response.status_code}"
        return self._failure(
            request,
            outcome,
            error_class=f"{self.error_prefix}_{suffix}",
            started=started,
            retry_after_seconds=_retry_after_seconds(response.headers),
        )

    def _provider_failure(
        self,
        request: ProviderAttemptRequest,
        decoded: Mapping[str, object],
        *,
        started: float,
    ) -> ProviderAttemptResult | None:
        error_type, error_code = _decoded_error_tokens(decoded)
        if error_type is None and error_code is None:
            return None
        token = " ".join(item.casefold() for item in (error_type, error_code) if item)
        if any(marker in token for marker in self.credit_error_markers):
            outcome = ProviderOutcome.CREDIT_EXHAUSTED
        elif any(marker in token for marker in self.quota_error_markers):
            outcome = ProviderOutcome.QUOTA_EXHAUSTED
        elif any(marker in token for marker in self.auth_error_markers):
            outcome = ProviderOutcome.AUTH_FAILURE
        else:
            outcome = ProviderOutcome.TERMINAL_ERROR
        return self._failure(
            request,
            outcome,
            error_class=f"{self.error_prefix}_{error_code or error_type or 'PROVIDER_FAILED'}",
            started=started,
            usage=_usage(decoded),
        )

    def _failure(
        self,
        request: ProviderAttemptRequest,
        outcome: ProviderOutcome,
        *,
        error_class: str,
        started: float,
        retry_after_seconds: int | None = None,
        usage: ProviderUsage | None = None,
    ) -> ProviderAttemptResult:
        normalized_usage = usage if usage is not None else ProviderUsage()
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
            error_class=_safe_token(error_class) or f"{self.error_prefix}_PROVIDER_ERROR",
            retry_after_seconds=retry_after_seconds,
            usage=normalized_usage,
        )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _decode_json_object(raw: bytes) -> Mapping[str, object] | None:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return cast(Mapping[str, object], decoded)


def _first_choice(decoded: Mapping[str, object]) -> Mapping[str, object] | None:
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return None
    return choice


def _choice_text(choice: Mapping[str, object]) -> str | None:
    message = choice.get("message")
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    return content.strip()


def _usage(decoded: Mapping[str, object]) -> ProviderUsage:
    raw = decoded.get("usage")
    if not isinstance(raw, Mapping):
        return ProviderUsage()
    input_tokens = _nonnegative_int(raw.get("prompt_tokens"))
    output_tokens = _nonnegative_int(raw.get("completion_tokens"))
    native: list[NativeUsage] = []

    input_details = raw.get("prompt_tokens_details")
    if isinstance(input_details, Mapping):
        cached = _nonnegative_int(input_details.get("cached_tokens"))
        if cached:
            native.append(NativeUsage("cached_input_tokens", Decimal(cached), "tokens"))

    output_details = raw.get("completion_tokens_details")
    if isinstance(output_details, Mapping):
        reasoning = _nonnegative_int(output_details.get("reasoning_tokens"))
        if reasoning:
            native.append(NativeUsage("reasoning_output_tokens", Decimal(reasoning), "tokens"))

    total = _nonnegative_int(raw.get("total_tokens"))
    if total:
        native.append(NativeUsage("provider_total_tokens", Decimal(total), "tokens"))

    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        native=tuple(native),
    )


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _decoded_error_tokens(
    decoded: Mapping[str, object],
) -> tuple[str | None, str | None]:
    error = decoded.get("error")
    if isinstance(error, Mapping):
        return _safe_token(error.get("type")), _safe_token(error.get("code"))
    return _safe_token(decoded.get("type")), _safe_token(decoded.get("code"))


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
    if value is None:
        return None
    stripped = value.strip()
    if not stripped.isdigit():
        return None
    return int(stripped)
