"""Typed request/error models for the Phase 1 HTTP shell."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(StrictModel):
    field: str | None = None
    reason: str


class ErrorEnvelope(StrictModel):
    code: str
    message: str
    correlation_id: str
    details: list[ErrorDetail] = Field(default_factory=list)


class SessionCreateRequest(StrictModel):
    external_reference: str | None = Field(default=None, max_length=200)


class ExplicitExecutionTarget(StrictModel):
    provider: str = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_profile: str | None = Field(default=None, min_length=1, max_length=100)


class AutoExecution(StrictModel):
    mode: Literal["AUTO"] = "AUTO"


class ExplicitExecution(StrictModel):
    mode: Literal["EXPLICIT_TARGET"]
    target: ExplicitExecutionTarget


ExecutionSelection = Annotated[
    AutoExecution | ExplicitExecution,
    Field(discriminator="mode"),
]


class TaskCreateRequest(StrictModel):
    operation: str = Field(min_length=1, max_length=100, pattern=r"^[A-Z][A-Z0-9_]*$")
    input: dict[str, Any]
    execution: ExecutionSelection | None = None
    external_reference: str | None = Field(default=None, max_length=200)


class EstimateTarget(StrictModel):
    provider_id: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=200)
    reasoning_profile: str = Field(min_length=1, max_length=100)


class EstimateAutoExecution(StrictModel):
    mode: Literal["AUTO"] = "AUTO"


class EstimateExplicitExecution(StrictModel):
    mode: Literal["EXPLICIT_TARGET"]
    target: EstimateTarget


EstimateExecutionSelection = Annotated[
    EstimateAutoExecution | EstimateExplicitExecution,
    Field(discriminator="mode"),
]


class EstimateRequest(StrictModel):
    operation: str = Field(min_length=1, max_length=100, pattern=r"^[A-Z][A-Z0-9_]*$")
    input: dict[str, Any]
    reference_scope: Literal["CLIENT_ONLY", "GLOBAL_PUBLIC"] = "CLIENT_ONLY"
    execution: EstimateExecutionSelection | None = None


class EstimateResponse(StrictModel):
    estimated_input_tokens: int = Field(ge=0)
    estimated_cached_input_tokens: int | None = Field(default=None, ge=0)
    estimated_output_tokens: int | None = Field(default=None, ge=0)
    estimated_reasoning_tokens: int | None = Field(default=None, ge=0)
    estimated_total_tokens: int | None = Field(default=None, ge=0)
    requested_execution_mode: Literal["AUTO", "EXPLICIT_TARGET"]
    effective_execution_mode: Literal["AUTO", "EXPLICIT_TARGET"]
    effective_target: EstimateTarget
    reference_scope: Literal["CLIENT_ONLY", "GLOBAL_PUBLIC"]
    estimation_method: str = Field(min_length=1, max_length=200)
    methodology_version: str = Field(min_length=1, max_length=100)
    benchmark_version: str = Field(min_length=1, max_length=200)
    as_of: datetime
    sample_size: int = Field(ge=0)
    cohort_size: int = Field(ge=0)
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    fallback_available: Literal["CLIENT_ONLY", "GLOBAL_PUBLIC"] | None = None
    limitations: list[str] = Field(default_factory=list, max_length=20)



CredentialScope = Annotated[
    str,
    Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9:_-]*$"),
]


class CredentialCreateRequest(StrictModel):
    display_label: str = Field(min_length=1, max_length=200)
    scopes: list[CredentialScope] = Field(min_length=1, max_length=50)
    expires_at: datetime | None = None


class CredentialMetadataView(StrictModel):
    credential_id: UUID
    display_label: str
    scopes: list[str]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$")
    key_version: int = Field(ge=1)
    status: Literal["ACTIVE", "REVOKED"]
    created_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


class CredentialIssueResponse(StrictModel):
    credential: CredentialMetadataView
    secret: str | None = None
    secret_available: bool
    replayed: bool


class CredentialListResponse(StrictModel):
    items: list[CredentialMetadataView]



class NativeUsageView(StrictModel):
    unit: str = Field(min_length=1, max_length=100)
    quantity: float = Field(ge=0)


class TechnicalUsageView(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    native_usage: list[NativeUsageView] = Field(default_factory=list)


class UsageItemView(StrictModel):
    period_start: datetime
    period_end: datetime
    usage: TechnicalUsageView


class UsagePageResponse(StrictModel):
    items: list[UsageItemView]
    next_cursor: str | None = None
    as_of: datetime | None = None



class TargetView(StrictModel):
    provider_id: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=200)
    reasoning_profile: str = Field(min_length=1, max_length=100)


class AttemptSummaryView(StrictModel):
    attempt_id: UUID
    operation: str
    provider_id: str
    model_id: str
    status: str


class SessionView(StrictModel):
    session_id: UUID
    status: Literal["ACTIVE", "EXPIRED", "CANCELLED", "CLOSED"]
    policy_version_id: UUID
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class TaskView(StrictModel):
    task_id: UUID
    session_id: UUID
    operation: str
    status: Literal[
        "CREATED",
        "QUEUED",
        "RUNNING",
        "PARTIAL",
        "COMPLETE",
        "UNAVAILABLE",
        "FAILED",
        "CANCELLING",
        "CANCELLED",
    ]
    requested_execution_mode: Literal["AUTO", "EXPLICIT_TARGET"]
    requested_target: TargetView | None = None
    effective_target: TargetView | None = None
    attempts: list[AttemptSummaryView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None


class TaskResultView(StrictModel):
    task_id: UUID
    status: str
    result: dict[str, Any]
    accepted: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    attempt_ids: list[UUID] = Field(default_factory=list)


class ProviderIdentityView(StrictModel):
    provider_id: str = Field(min_length=1, max_length=100)
    provider_name: str = Field(min_length=1, max_length=200)


class AttemptView(StrictModel):
    attempt_id: UUID
    operation: str
    task_id: UUID | None = None
    session_id: UUID | None = None
    provider: ProviderIdentityView
    model_id: str = Field(min_length=1, max_length=200)
    reasoning_profile: str | None = Field(default=None, max_length=100)
    status: str
    cycle: int | None = Field(default=None, ge=1)
    attempt_index: int | None = Field(default=None, ge=1)
    retry_of_attempt_id: UUID | None = None
    fallback_from_attempt_id: UUID | None = None
    usage: TechnicalUsageView = Field(default_factory=TechnicalUsageView)
    started_at: datetime
    finished_at: datetime | None = None


class AttemptListResponse(StrictModel):
    items: list[AttemptView]
    next_cursor: str | None = None


class SanitizedEvidenceView(StrictModel):
    media_type: str = Field(min_length=1, max_length=200)
    sanitized_raw_body: str
    sanitized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truncated: bool


class ExchangeEvidenceView(StrictModel):
    exchange_id: UUID
    attempt_id: UUID
    provider: ProviderIdentityView
    operation: str
    status: str | None = None
    status_label: str | None = None
    request_evidence: SanitizedEvidenceView
    response_evidence: SanitizedEvidenceView | None = None


class ExchangeListResponse(StrictModel):
    items: list[ExchangeEvidenceView]


class ProviderPublicView(StrictModel):
    provider_id: str
    provider_name: str
    capabilities: list[str]


class ModelPublicView(StrictModel):
    provider_id: str
    model_id: str
    model_name: str
    capabilities: list[str]
    reasoning_profiles: list[str]
