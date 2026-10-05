"""Test-only oracle for #80 execution selection semantics."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from tests.characterization.reference_contract import (
    FinalState,
    Invocation,
    Policy,
    run_need,
)
from tests.contracts.attempt_operation_reference import AttemptFactory, OperationContract


class Mode(str, Enum):
    AUTO = "AUTO"
    EXPLICIT_TARGET = "EXPLICIT_TARGET"


@dataclass(frozen=True)
class Target:
    provider: str
    model: str
    reasoning_profile: str


@dataclass(frozen=True)
class Candidate:
    target: Target
    comparison_group: str
    comparison_group_rank: int
    estimated_cost: float | None
    currency: str | None
    policy_rank: int
    eligible: bool = True


@dataclass
class RequirementState:
    accepted: set[str]
    missing: set[str]


def mode_or_default(execution: dict | None) -> Mode:
    if execution is None:
        return Mode.AUTO
    return Mode(str(execution.get("mode") or "AUTO"))


def rank_auto(candidates: Iterable[Candidate]) -> tuple[Candidate, ...]:
    eligible = [item for item in candidates if item.eligible]

    def key(item: Candidate) -> tuple[int, int, float, int, str, str, str]:
        priced = item.estimated_cost is not None
        return (
            item.comparison_group_rank,
            0 if priced else 1,
            float(item.estimated_cost) if priced else float("inf"),
            item.policy_rank,
            item.target.provider,
            item.target.model,
            item.target.reasoning_profile,
        )

    return tuple(sorted(eligible, key=key))


def resolve_explicit(
    execution: dict,
    *,
    allowed_targets: tuple[Target, ...],
    default_model: dict[str, str],
    default_profile: dict[tuple[str, str], str],
) -> Target:
    if execution.get("mode") != Mode.EXPLICIT_TARGET.value:
        raise ValueError("not explicit mode")
    raw = execution.get("target") or {}
    provider = str(raw.get("provider") or "").strip().upper()
    if not provider:
        raise ValueError("provider required")
    model = str(raw.get("model") or default_model.get(provider) or "").strip()
    profile = str(
        raw.get("reasoning_profile") or default_profile.get((provider, model)) or ""
    ).strip()
    target = Target(provider, model, profile)
    if target not in allowed_targets:
        raise PermissionError("target unavailable")
    return target


def run_auto(
    *,
    candidates: Callable[[], tuple[Candidate, ...]],
    outcome_for: Callable[[Candidate, int], Invocation],
    policy: Policy,
) -> tuple[FinalState, list[Target]]:
    calls: list[Target] = []

    def pool() -> tuple[Candidate, ...]:
        return rank_auto(candidates())

    def invoke(candidate: object, cycle: int, _call_index: int) -> Invocation:
        assert isinstance(candidate, Candidate)
        calls.append(candidate.target)
        return outcome_for(candidate, cycle)

    result = run_need(candidates=pool, invoke=invoke, policy=policy)
    return result.final_state, calls


def run_requirement_cycle(
    *,
    candidates: tuple[Candidate, ...],
    initial_missing: set[str],
    response_for: Callable[[Candidate, frozenset[str]], tuple[set[str], bool]],
) -> tuple[RequirementState, list[tuple[Target, frozenset[str]]]]:
    state = RequirementState(accepted=set(), missing=set(initial_missing))
    calls: list[tuple[Target, frozenset[str]]] = []

    for candidate in rank_auto(candidates):
        if not state.missing:
            break
        requested = frozenset(state.missing)
        calls.append((candidate.target, requested))
        accepted_now, complete = response_for(candidate, requested)
        accepted_now = set(accepted_now).intersection(state.missing)
        state.accepted.update(accepted_now)
        state.missing.difference_update(accepted_now)
        if complete and not state.missing:
            break

    return state, calls


def explicit_retry_attempts(
    *,
    target: Target,
    attempt_ids: tuple[str, ...],
    request_fingerprint: str,
) -> tuple[tuple[str, Target], ...]:
    factory = AttemptFactory(attempt_ids)
    contract = OperationContract("TASK_EXECUTION")
    output: list[tuple[str, Target]] = []
    previous: str | None = None
    for _ in attempt_ids:
        attempt = factory.create(
            contract=contract,
            provider_id=target.provider,
            model_id=target.model,
            request_fingerprint=request_fingerprint,
            task_id="task-1",
            session_id="session-1",
            retry_of_attempt_id=previous,
        )
        output.append((attempt.attempt_id, target))
        previous = attempt.attempt_id
    return tuple(output)
