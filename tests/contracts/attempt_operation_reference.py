"""Test-only oracle for ORQETIA attempt/operation carry-over contracts.

Origin requirement: RASAi PR #201 / main 8008a3e24550f7c4b199beb0adeefa9c4e618538.
This module is independent ORQETIA contract code and is not production runtime.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class OperationContract:
    operation: str
    task_required: bool = True
    session_required: bool = True

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("operation must be explicit")


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    operation: str
    provider_id: str
    model_id: str
    request_fingerprint: str
    task_id: str | None = None
    session_id: str | None = None
    retry_of_attempt_id: str | None = None
    fallback_from_attempt_id: str | None = None


@dataclass(frozen=True)
class Exchange:
    exchange_id: str
    attempt_id: str
    request_fingerprint: str


@dataclass(frozen=True)
class Usage:
    attempt_id: str
    total_tokens: int


@dataclass(frozen=True)
class Pricing:
    attempt_id: str
    basis: str


@dataclass(frozen=True)
class Diagnostic:
    attempt_id: str
    error_class: str | None


class AttemptFactory:
    def __init__(self, ids: Iterable[str]) -> None:
        self._ids = iter(ids)

    def create(
        self,
        *,
        contract: OperationContract,
        provider_id: str,
        model_id: str,
        request_fingerprint: str,
        task_id: str | None = None,
        session_id: str | None = None,
        retry_of_attempt_id: str | None = None,
        fallback_from_attempt_id: str | None = None,
    ) -> Attempt:
        if contract.task_required and not task_id:
            raise ValueError("task_id required by operation contract")
        if contract.session_required and not session_id:
            raise ValueError("session_id required by operation contract")
        if not contract.operation.strip():
            raise ValueError("operation must be explicit")
        attempt_id = next(self._ids)
        if not attempt_id.strip():
            raise ValueError("attempt_id must be explicit")
        return Attempt(
            attempt_id=attempt_id,
            operation=contract.operation,
            provider_id=provider_id,
            model_id=model_id,
            request_fingerprint=request_fingerprint,
            task_id=task_id,
            session_id=session_id,
            retry_of_attempt_id=retry_of_attempt_id,
            fallback_from_attempt_id=fallback_from_attempt_id,
        )


def bind_exchange(attempt: Attempt, *, exchange_id: str) -> Exchange:
    return Exchange(
        exchange_id=exchange_id,
        attempt_id=attempt.attempt_id,
        request_fingerprint=attempt.request_fingerprint,
    )


def usage_for(attempt: Attempt, *, total_tokens: int) -> Usage:
    return Usage(attempt_id=attempt.attempt_id, total_tokens=total_tokens)


def pricing_for(attempt: Attempt, *, basis: str) -> Pricing:
    return Pricing(attempt_id=attempt.attempt_id, basis=basis)


def diagnostic_for(attempt: Attempt, *, error_class: str | None) -> Diagnostic:
    return Diagnostic(attempt_id=attempt.attempt_id, error_class=error_class)


@dataclass(frozen=True)
class LegacyCandidate:
    attempt_id: str
    request_fingerprint: str


def reconcile_legacy_by_exact_fingerprint(
    fingerprint: str,
    candidates: Iterable[LegacyCandidate],
) -> str | None:
    """Import-only fallback: unique exact fingerprint or unresolved.

    New ORQETIA exchanges must use explicit attempt_id and never call this helper.
    """
    exact = [item.attempt_id for item in candidates if item.request_fingerprint == fingerprint]
    if len(exact) != 1:
        return None
    return exact[0]
