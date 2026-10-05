"""Typed request/error models for the Phase 1 HTTP shell."""

from __future__ import annotations

from typing import Annotated, Any, Literal

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


class EstimateRequest(StrictModel):
    operation: str = Field(min_length=1, max_length=100, pattern=r"^[A-Z][A-Z0-9_]*$")
    input: dict[str, Any]
    target: EstimateTarget | None = None
