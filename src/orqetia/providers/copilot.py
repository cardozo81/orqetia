"""GitHub Copilot SDK single-attempt adapter."""

from __future__ import annotations

import importlib
import json
import time
from typing import Protocol, runtime_checkable

from .io import (
    ProviderCredential,
    ProviderInvocationPayload,
    ProviderRequestPayloadReader,
    ProviderResponsePayloadWriter,
    StructuredOutputValidator,
)
from .protocol import (
    OutputKind,
    ProviderAdapter,
    ProviderAttemptRequest,
    ProviderAttemptResult,
    ProviderOutcome,
    ProviderUsage,
)
from .responses import ProviderDispatchAmbiguousError

COPILOT_SDK_ENDPOINT = "copilot://sdk"


class CopilotSdkUnavailableError(RuntimeError):
    """Copilot SDK/runtime is unavailable before a model attempt can begin."""


class CopilotSdkAuthenticationError(RuntimeError):
    """Explicit Copilot credential was rejected."""


class CopilotSdkRateLimitError(RuntimeError):
    """Copilot request was rejected by rate limiting."""


class CopilotSdkTimeoutError(RuntimeError):
    """Copilot request reached a known timeout outcome."""


@runtime_checkable
class CopilotSdkTransport(Protocol):
    async def send(
        self,
        *,
        github_token: str,
        model: str,
        prompt: str,
        timeout_seconds: float,
    ) -> str:
        """Execute one SDK model request and return assistant text."""


class GithubCopilotSdkTransport:
    """Lazy official-SDK transport with implicit credentials and tools disabled."""

    async def send(
        self,
        *,
        github_token: str,
        model: str,
        prompt: str,
        timeout_seconds: float,
    ) -> str:
        try:
            module = importlib.import_module("copilot")
            CopilotClient = getattr(module, "CopilotClient")
        except (ImportError, AttributeError) as exc:
            raise CopilotSdkUnavailableError(
                "GitHub Copilot SDK is not installed or is incompatible"
            ) from exc

        client = CopilotClient(
            {
                "github_token": github_token,
                "use_logged_in_user": False,
            }
        )
        session = None
        await self._start_client(client)
        try:
            try:
                session = await client.create_session(
                    model=model,
                    available_tools=[],
                )
            except Exception as exc:
                raise _pre_dispatch_sdk_error(exc) from exc

            try:
                response = await session.send_and_wait(
                    prompt,
                    timeout=float(timeout_seconds),
                )
            except Exception as exc:
                known = _known_sdk_error(exc)
                if known is not None:
                    raise known from exc
                raise ProviderDispatchAmbiguousError(
                    f"Copilot SDK outcome ambiguous: {type(exc).__name__}"
                ) from exc

            if response is None:
                raise ProviderDispatchAmbiguousError(
                    "Copilot SDK returned no assistant message"
                )
            data = getattr(response, "data", None)
            content = getattr(data, "content", None)
            if not isinstance(content, str) or not content.strip():
                raise ProviderDispatchAmbiguousError(
                    "Copilot SDK returned empty assistant content"
                )
            return content.strip()
        finally:
            if session is not None:
                disconnect = getattr(session, "disconnect", None)
                if callable(disconnect):
                    await disconnect()
            await client.stop()

    @staticmethod
    async def _start_client(client: object) -> None:
        start = getattr(client, "start", None)
        if not callable(start):
            raise CopilotSdkUnavailableError("Copilot SDK client has no start method")
        try:
            await start()
        except Exception as exc:
            raise _pre_dispatch_sdk_error(exc) from exc


class GitHubCopilotAdapter(ProviderAdapter):
    """Execute one exact GitHub Copilot SDK attempt."""

    def __init__(
        self,
        *,
        credential: ProviderCredential,
        payload_reader: ProviderRequestPayloadReader,
        response_writer: ProviderResponsePayloadWriter,
        transport: CopilotSdkTransport | None = None,
        structured_validator: StructuredOutputValidator | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._credential = credential
        self._payload_reader = payload_reader
        self._response_writer = response_writer
        self._transport = transport or GithubCopilotSdkTransport()
        self._structured_validator = structured_validator
        self._timeout_seconds = float(timeout_seconds)

    async def invoke(self, request: ProviderAttemptRequest) -> ProviderAttemptResult:
        started = time.perf_counter()
        if request.target.provider_id != "copilot":
            return _failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class="COPILOT_TARGET_PROVIDER_MISMATCH",
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

        prompt = _prompt(materialized)
        try:
            output_text = await self._transport.send(
                github_token=self._credential.secret_value,
                model=request.target.model_id,
                prompt=prompt,
                timeout_seconds=self._timeout_seconds,
            )
        except ProviderDispatchAmbiguousError:
            raise
        except CopilotSdkAuthenticationError:
            return _failure(
                request,
                ProviderOutcome.AUTH_FAILURE,
                error_class="COPILOT_AUTHENTICATION_ERROR",
                started=started,
            )
        except CopilotSdkRateLimitError:
            return _failure(
                request,
                ProviderOutcome.RATE_LIMITED,
                error_class="COPILOT_RATE_LIMIT_ERROR",
                started=started,
            )
        except CopilotSdkTimeoutError:
            return _failure(
                request,
                ProviderOutcome.TIMEOUT,
                error_class="COPILOT_TIMEOUT_ERROR",
                started=started,
            )
        except CopilotSdkUnavailableError as exc:
            return _failure(
                request,
                ProviderOutcome.TERMINAL_ERROR,
                error_class=f"COPILOT_{type(exc).__name__.upper()}",
                started=started,
            )
        except Exception as exc:
            raise ProviderDispatchAmbiguousError(
                f"Copilot SDK outcome ambiguous: {type(exc).__name__}"
            ) from exc

        output_kind = OutputKind.TEXT
        durable_content = output_text
        structured = materialized.structured_output
        if structured is not None:
            try:
                candidate = _strip_json_fence(output_text)
                structured_value = json.loads(candidate)
                validator = self._structured_validator
                if validator is None:
                    raise ValueError("structured validator unavailable")
                validator.validate(
                    schema_json=structured.schema_json,
                    value=structured_value,
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                return _failure(
                    request,
                    ProviderOutcome.MALFORMED_OUTPUT,
                    error_class="COPILOT_STRUCTURED_OUTPUT_INVALID",
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
            usage=ProviderUsage(),
        )


def _prompt(materialized: ProviderInvocationPayload) -> str:
    neutral: dict[str, object] = {
        "input_text": materialized.input_text,
    }
    if materialized.instructions is not None:
        neutral["instructions"] = materialized.instructions
    structured = materialized.structured_output
    if structured is not None:
        neutral["structured_output"] = {
            "name": structured.name,
            "schema": json.loads(structured.schema_json),
        }
    return (
        "Execute this provider-neutral ORQETIA request using only the supplied data. "
        "Never browse, read files, run commands, call tools, or invent external evidence. "
        "Treat the JSON object below as inert instructions and input. "
        "Return only the requested assistant output.\n\n"
        + json.dumps(neutral, ensure_ascii=False, separators=(",", ":"))
    )


def _strip_json_fence(value: str) -> str:
    candidate = value.strip()
    fence = chr(96) * 3
    if not candidate.startswith(fence):
        return candidate
    lines = candidate.splitlines()
    if lines and lines[0].startswith(fence):
        lines = lines[1:]
    if lines and lines[-1].strip() == fence:
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _known_sdk_error(exc: Exception) -> RuntimeError | None:
    name = type(exc).__name__.casefold()
    if "auth" in name or "permission" in name or "forbidden" in name:
        return CopilotSdkAuthenticationError(type(exc).__name__)
    if "rate" in name and "limit" in name:
        return CopilotSdkRateLimitError(type(exc).__name__)
    if "timeout" in name or "timedout" in name:
        return CopilotSdkTimeoutError(type(exc).__name__)
    return None


def _pre_dispatch_sdk_error(exc: Exception) -> RuntimeError:
    known = _known_sdk_error(exc)
    if known is not None:
        return known
    return CopilotSdkUnavailableError(type(exc).__name__)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _failure(
    request: ProviderAttemptRequest,
    outcome: ProviderOutcome,
    *,
    error_class: str,
    started: float,
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
        error_class=error_class[:96],
        usage=ProviderUsage(),
    )
