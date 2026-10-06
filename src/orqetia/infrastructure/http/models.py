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
