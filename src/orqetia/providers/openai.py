"""OpenAI Responses API single-attempt adapter."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, cast

import httpx

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

OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
_ERROR_TOKEN = re.compile(r"[^A-Za-z0-9_.-]+")


class ProviderDispatchAmbiguousError(RuntimeError):
    """Transport ended without a trustworthy provider outcome after dispatch may have begun."""


@dataclass(frozen=True)
class ProviderHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        if self.status_code < 100 or self.status_code > 599:
            raise ValueError("HTTP status_code must be between 100 and 599")


class ProviderHttpTransport(Protocol):
    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, object],
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        """Perform exactly one HTTP request or raise ProviderDispatchAmbiguousError."""
        ...


class HttpxProviderHttpTransport:
    """Production HTTP transport. It never retries provider requests."""

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, object],
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_seconds),
                follow_redirects=False,
            ) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TransportError as exc:
            raise ProviderDispatchAmbiguousError(
                f"provider transport outcome ambiguous: {type(exc).__name__}"
            ) from exc
        return ProviderHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
        )


class OpenAIResponsesAdapter(ProviderAdapter):
    """Execute one exact OpenAI Responses API attempt.

    Request content and credentials are supplied through composition boundaries. The
    adapter performs no target selection, retry, fallback, health or pricing policy.
    """

    def __init__(
        self,
        *,
        credential: ProviderCredential,
        payload_reader: ProviderRequestPayloadReader,
        response_writer: ProviderResponsePayloadWriter,
        transport: ProviderHttpTransport | None = None,
        structured_validator: StructuredOutputValidator | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._credential = credential
        self._payload_reader = payload_reader
        self._response_writer = response_writer
        self._transport = transport or HttpxProviderHttpTransport()
        self._structured_validator = structured_validator
        self._timeout_seconds = float(timeout_seconds)

    async def invoke(self, request: ProviderAttemptRequest) -> ProviderAttemptResult:
        started = time.perf_counter()
        if request.target.provider_id != "openai":
            return self._failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class="OPENAI_TARGET_PROVIDER_MISMATCH",
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
                url=OPENAI_RESPONSES_ENDPOINT,
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
                error_class="OPENAI_INVALID_JSON_RESPONSE",
                started=started,
            )

        provider_failure = self._provider_failure(request, decoded, started=started)
        if provider_failure is not None:
            return provider_failure

        output_text = _extract_output_text(decoded)
        if output_text is None:
            return self._failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class="OPENAI_OUTPUT_TEXT_MISSING",
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
                    error_class="OPENAI_STRUCTURED_OUTPUT_INVALID",
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

    @staticmethod
    def _wire_body(
        request: ProviderAttemptRequest,
        materialized: ProviderInvocationPayload,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": request.target.model_id,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": materialized.input_text,
                        }
                    ],
                }
            ],
            "service_tier": "default",
            "store": False,
            "tools": [],
        }
        if materialized.instructions is not None:
            body["instructions"] = materialized.instructions

        profile = request.target.reasoning_profile.strip().casefold()
        if profile not in {"provider_default", "default"}:
            body["reasoning"] = {"effort": profile}

        structured = materialized.structured_output
        if structured is not None:
            schema = json.loads(structured.schema_json)
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": structured.name,
                    "schema": schema,
                    "strict": True,
                }
            }
        else:
            body["text"] = {"format": {"type": "text"}}
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

        if response.status_code in {401, 403}:
            outcome = ProviderOutcome.AUTH_FAILURE
        elif response.status_code == 402 or "credit" in token or "balance" in token:
            outcome = ProviderOutcome.CREDIT_EXHAUSTED
        elif "quota" in token:
            outcome = ProviderOutcome.QUOTA_EXHAUSTED
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
            error_class=f"OPENAI_{suffix}",
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
        status = str(decoded.get("status") or "").casefold()
        if status not in {"failed", "incomplete"} and decoded.get("error") is None:
            return None

        if status == "incomplete":
            return self._failure(
                request,
                ProviderOutcome.MALFORMED_OUTPUT,
                error_class="OPENAI_RESPONSE_INCOMPLETE",
                started=started,
                usage=_usage(decoded),
            )

        error = decoded.get("error")
        error_type: str | None = None
        error_code: str | None = None
        if isinstance(error, Mapping):
            error_type = _safe_token(error.get("type"))
            error_code = _safe_token(error.get("code"))
        token = " ".join(item.casefold() for item in (error_type, error_code) if item)
        if "credit" in token or "balance" in token:
            outcome = ProviderOutcome.CREDIT_EXHAUSTED
        elif "quota" in token:
            outcome = ProviderOutcome.QUOTA_EXHAUSTED
        elif any(item in token for item in ("auth", "permission", "forbidden", "api_key")):
            outcome = ProviderOutcome.AUTH_FAILURE
        else:
            outcome = ProviderOutcome.TERMINAL_ERROR
        return self._failure(
            request,
            outcome,
            error_class=f"OPENAI_{error_code or error_type or 'PROVIDER_FAILED'}",
            started=started,
            usage=_usage(decoded),
        )

    @staticmethod
    def _failure(
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
            error_class=_safe_token(error_class) or "OPENAI_PROVIDER_ERROR",
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


def _extract_output_text(decoded: Mapping[str, object]) -> str | None:
    direct = decoded.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    output = decoded.get("output")
    if not isinstance(output, list):
        return None
    parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    if not parts:
        return None
    return "\n".join(parts)


def _usage(decoded: Mapping[str, object]) -> ProviderUsage:
    raw = decoded.get("usage")
    if not isinstance(raw, Mapping):
        return ProviderUsage()

    input_tokens = _nonnegative_int(raw.get("input_tokens"))
    output_tokens = _nonnegative_int(raw.get("output_tokens"))
    native: list[NativeUsage] = []

    input_details = raw.get("input_tokens_details")
    if isinstance(input_details, Mapping):
        cached = _nonnegative_int(input_details.get("cached_tokens"))
        if cached:
            native.append(NativeUsage("cached_input_tokens", Decimal(cached), "tokens"))

    output_details = raw.get("output_tokens_details")
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


def _error_tokens(raw: bytes) -> tuple[str | None, str | None]:
    decoded = _decode_json_object(raw[:65536])
    if decoded is None:
        return None, None
    error = decoded.get("error")
    if not isinstance(error, Mapping):
        return None, None
    return _safe_token(error.get("type")), _safe_token(error.get("code"))


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
