"""Provider-neutral invocation I/O ports shared by concrete adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from .protocol import OutputKind, ProviderTarget


@dataclass(frozen=True)
class StructuredOutputSpec:
    """Immutable structured-output contract carried by a provider request reference."""

    name: str
    schema_json: str

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 100:
            raise ValueError("structured output name must contain 1..100 characters")
        if not self.schema_json.strip():
            raise ValueError("schema_json is required")
        try:
            schema = json.loads(self.schema_json)
        except json.JSONDecodeError as exc:
            raise ValueError("schema_json must contain valid JSON") from exc
        if not isinstance(schema, dict):
            raise ValueError("structured output schema root must be a JSON object")


@dataclass(frozen=True)
class ProviderInvocationPayload:
    """Provider-neutral materialized request content.

    Durable execution carries only a request reference/fingerprint. The adapter loads
    content through a port immediately before the single provider call.
    """

    input_text: str
    instructions: str | None = None
    structured_output: StructuredOutputSpec | None = None

    def __post_init__(self) -> None:
        if not self.input_text.strip():
            raise ValueError("input_text is required")
        if self.instructions is not None and not self.instructions.strip():
            raise ValueError("instructions cannot be blank when present")


@dataclass(frozen=True, repr=False)
class ProviderCredential:
    """Ephemeral in-memory provider credential with redacted string representation."""

    secret_value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.secret_value.strip():
            raise ValueError("provider credential cannot be blank")

    def __repr__(self) -> str:
        return "ProviderCredential(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


class ProviderRequestPayloadReader(Protocol):
    async def load(
        self,
        *,
        request_reference: str,
        request_fingerprint: str,
    ) -> ProviderInvocationPayload:
        """Load already-authorized request content without exposing it on queue work."""
        ...


class ProviderResponsePayloadWriter(Protocol):
    async def store(
        self,
        *,
        attempt_id: UUID,
        target: ProviderTarget,
        output_kind: OutputKind,
        content: str,
    ) -> str:
        """Persist sanitized provider output and return a durable response reference."""
        ...


class StructuredOutputValidator(Protocol):
    def validate(self, *, schema_json: str, value: object) -> None:
        """Raise ValueError when the decoded provider value violates the local schema."""
        ...
