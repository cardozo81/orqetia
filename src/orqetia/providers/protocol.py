"""Provider single-attempt protocol shared by runtime adapters and the test provider."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

_OPERATION = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProviderOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    REQUIREMENT_NOT_SATISFIED = "REQUIREMENT_NOT_SATISFIED"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    TERMINAL_ERROR = "TERMINAL_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    CREDIT_EXHAUSTED = "CREDIT_EXHAUSTED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_FAILURE = "AUTH_FAILURE"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    UNAVAILABLE = "UNAVAILABLE"


class OutputKind(StrEnum):
    NONE = "NONE"
    TEXT = "TEXT"
    STRUCTURED = "STRUCTURED"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True)
class ProviderTarget:
    provider_id: str
    model_id: str
    reasoning_profile: str

    def __post_init__(self) -> None:
        values = (
            ("provider_id", self.provider_id, 100),
            ("model_id", self.model_id, 200),
            ("reasoning_profile", self.reasoning_profile, 100),
        )
        for field, value, maximum in values:
            if not value.strip():
                raise ValueError(f"{field} is required")
            if len(value) > maximum:
                raise ValueError(f"{field} exceeds {maximum} characters")


@dataclass(frozen=True)
class ProviderAttemptRequest:
    attempt_id: UUID
    operation: str
    target: ProviderTarget
    cycle: int
    attempt_index: int
    request_reference: str
    request_fingerprint: str
    missing_requirements: tuple[str, ...]
    task_id: UUID | None = None
    session_id: UUID | None = None

    def __post_init__(self) -> None:
        if not _OPERATION.fullmatch(self.operation):
            raise ValueError("operation must use canonical uppercase code format")
        if self.cycle < 1:
            raise ValueError("cycle must be positive")
        if self.attempt_index < 1:
            raise ValueError("attempt_index must be positive")
        if not self.request_reference.strip() or len(self.request_reference) > 500:
            raise ValueError("request_reference must contain 1..500 characters")
        if not _SHA256.fullmatch(self.request_fingerprint):
            raise ValueError("request_fingerprint must be lowercase SHA-256 hex")
        if len(set(self.missing_requirements)) != len(self.missing_requirements):
            raise ValueError("missing_requirements must not contain duplicates")
        if any(not item.strip() or len(item) > 200 for item in self.missing_requirements):
            raise ValueError("missing_requirements contain an invalid requirement")


@dataclass(frozen=True)
class NativeUsage:
    name: str
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 100:
            raise ValueError("native usage name must contain 1..100 characters")
        if self.value < 0:
            raise ValueError("native usage value cannot be negative")
        if not self.unit.strip() or len(self.unit) > 100:
            raise ValueError("native usage unit must contain 1..100 characters")


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    native: tuple[NativeUsage, ...] = ()

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token usage cannot be negative")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ProviderCostMetadata:
    amount: Decimal | None
    comparison_group: str
    currency: str | None = None
    native_unit: str | None = None

    def __post_init__(self) -> None:
        if not self.comparison_group.strip() or len(self.comparison_group) > 100:
            raise ValueError("comparison_group must contain 1..100 characters")
        if self.amount is not None and self.amount < 0:
            raise ValueError("cost amount cannot be negative")
        if self.amount is None and self.comparison_group != "UNPRICED":
            raise ValueError("missing amount requires UNPRICED comparison_group")
        if self.amount is not None and self.comparison_group == "UNPRICED":
            raise ValueError("UNPRICED cannot contain a numeric amount")
        if self.currency is not None and len(self.currency) > 12:
            raise ValueError("currency exceeds 12 characters")
        if self.native_unit is not None and len(self.native_unit) > 100:
            raise ValueError("native_unit exceeds 100 characters")


@dataclass(frozen=True)
class ProviderAttemptResult:
    attempt_id: UUID
    outcome: ProviderOutcome
    output_kind: OutputKind
    accepted_requirements: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    simulated_latency_ms: int
    response_reference: str | None = None
    error_class: str | None = None
    retry_after_seconds: int | None = None
    usage: ProviderUsage = ProviderUsage()
    cost: ProviderCostMetadata | None = None

    def __post_init__(self) -> None:
        if self.simulated_latency_ms < 0:
            raise ValueError("simulated_latency_ms cannot be negative")
        if self.response_reference is not None and len(self.response_reference) > 500:
            raise ValueError("response_reference exceeds 500 characters")
        if self.error_class is not None and len(self.error_class) > 200:
            raise ValueError("error_class exceeds 200 characters")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds cannot be negative")
        if set(self.accepted_requirements) & set(self.missing_requirements):
            raise ValueError("accepted and missing requirements must be disjoint")


class ProviderAdapter(Protocol):
    async def invoke(self, request: ProviderAttemptRequest) -> ProviderAttemptResult:
        """Execute exactly one provider attempt without orchestration retries."""
