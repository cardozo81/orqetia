"""Canonical orchestration policy for AUTO and EXPLICIT_TARGET execution.

This module owns deterministic policy decisions only. Provider I/O remains behind the
durable provider-attempt worker path. Every decision is derived from durable task,
session, cycle, attempt and policy facts so callers can reconstruct it after restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from orqetia.providers import ProviderAttemptResult, ProviderOutcome

from .attempts import ProviderAttempt, ProviderAttemptStatus
from .sessions import (
    ExecutionSession,
    ExecutionTargetSnapshot,
    QuarantineState,
    SessionStatus,
    TargetHealth,
)
from .tasks import ExecutionMode, ExecutionTask, TaskCycleDecision, TaskStatus


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True)
class OrchestrationPolicy:
    """Administrative execution limits resolved for one effective policy version."""

    max_cycles: int
    max_attempts: int
    cycle_delay_seconds: int = 0
    retry_after_cap_seconds: int = 300
    deadline_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.cycle_delay_seconds < 0:
            raise ValueError("cycle_delay_seconds cannot be negative")
        if self.retry_after_cap_seconds < 0 or self.retry_after_cap_seconds > 300:
            raise ValueError("retry_after_cap_seconds must be between 0 and 300")
        if self.deadline_at is not None:
            _require_aware(self.deadline_at, "deadline_at")


@dataclass(frozen=True)
class OrchestrationCandidate:
    """One already-authorized candidate plus internal comparison metadata."""

    target: ExecutionTargetSnapshot
    comparison_group: str
    comparison_group_rank: int
    estimated_cost: Decimal | None
    policy_rank: int
    eligible: bool = True
    currency: str | None = None
    native_unit: str | None = None
    pricing_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.comparison_group.strip():
            raise ValueError("comparison_group is required")
        if self.comparison_group_rank < 0 or self.policy_rank < 0:
            raise ValueError("candidate ranks cannot be negative")
        if self.estimated_cost is None and self.comparison_group != "UNPRICED":
            raise ValueError("missing estimated_cost requires UNPRICED comparison_group")
        if self.estimated_cost is not None and self.estimated_cost < 0:
            raise ValueError("estimated_cost cannot be negative")
        if self.estimated_cost is not None and self.comparison_group == "UNPRICED":
            raise ValueError("UNPRICED candidate cannot contain estimated_cost")
        if self.currency is not None and len(self.currency) > 12:
            raise ValueError("currency exceeds 12 characters")
        if self.native_unit is not None and len(self.native_unit) > 100:
            raise ValueError("native_unit exceeds 100 characters")
        if self.pricing_reference is not None and len(self.pricing_reference) > 200:
            raise ValueError("pricing_reference exceeds 200 characters")


def rank_auto_candidates(
    candidates: tuple[OrchestrationCandidate, ...],
) -> tuple[OrchestrationCandidate, ...]:
    """Apply canonical AUTO ranking to already-authorized eligible candidates."""

    return tuple(sorted(candidates, key=_auto_candidate_rank_key))


def _auto_candidate_rank_key(
    candidate: OrchestrationCandidate,
) -> tuple[int, int, Decimal, int, str, str, str]:
    priced = candidate.estimated_cost is not None
    amount = candidate.estimated_cost
    return (
        candidate.comparison_group_rank,
        0 if priced else 1,
        amount if amount is not None else Decimal("Infinity"),
        candidate.policy_rank,
        candidate.target.provider_id,
        candidate.target.model_id,
        candidate.target.reasoning_profile,
    )


@dataclass(frozen=True)
class RequirementProgress:
    accepted: tuple[str, ...]
    missing: tuple[str, ...]


class AttemptObservationStatus(StrEnum):
    COMPLETED = "COMPLETED"
    AMBIGUOUS = "AMBIGUOUS"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class AttemptObservation:
    """Durable provider-attempt fact consumed by the policy engine."""

    attempt_id: UUID
    target: ExecutionTargetSnapshot
    status: AttemptObservationStatus
    outcome: ProviderOutcome | None = None
    accepted_requirements: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    response_reference: str | None = None
    error_class: str | None = None
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.status is AttemptObservationStatus.COMPLETED and self.outcome is None:
            raise ValueError("COMPLETED observation requires outcome")
        if self.status is not AttemptObservationStatus.COMPLETED and self.outcome is not None:
            raise ValueError("non-COMPLETED observation cannot contain outcome")
        if set(self.accepted_requirements) & set(self.missing_requirements):
            raise ValueError("accepted and missing requirements must be disjoint")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds cannot be negative")
        if self.response_reference is not None and len(self.response_reference) > 500:
            raise ValueError("response_reference exceeds 500 characters")
        if self.error_class is not None and len(self.error_class) > 200:
            raise ValueError("error_class exceeds 200 characters")

    @classmethod
    def from_result(
        cls,
        *,
        target: ExecutionTargetSnapshot,
        result: ProviderAttemptResult,
    ) -> AttemptObservation:
        return cls(
            attempt_id=result.attempt_id,
            target=target,
            status=AttemptObservationStatus.COMPLETED,
            outcome=result.outcome,
            accepted_requirements=result.accepted_requirements,
            missing_requirements=result.missing_requirements,
            response_reference=result.response_reference,
            error_class=result.error_class,
            retry_after_seconds=result.retry_after_seconds,
        )

    @classmethod
    def from_attempt(cls, attempt: ProviderAttempt) -> AttemptObservation:
        if attempt.status is ProviderAttemptStatus.COMPLETED:
            if attempt.provider_outcome is None:
                raise ValueError("completed attempt is missing provider_outcome")
            return cls(
                attempt_id=attempt.attempt_id,
                target=attempt.target,
                status=AttemptObservationStatus.COMPLETED,
                outcome=ProviderOutcome(attempt.provider_outcome),
                accepted_requirements=attempt.accepted_requirements,
                missing_requirements=attempt.missing_requirements,
                response_reference=attempt.response_reference,
                error_class=attempt.error_class,
                retry_after_seconds=attempt.retry_after_seconds,
            )
        if attempt.status is ProviderAttemptStatus.AMBIGUOUS:
            return cls(
                attempt_id=attempt.attempt_id,
                target=attempt.target,
                status=AttemptObservationStatus.AMBIGUOUS,
                error_class=attempt.error_class,
            )
        if attempt.status is ProviderAttemptStatus.CANCELLED:
            return cls(
                attempt_id=attempt.attempt_id,
                target=attempt.target,
                status=AttemptObservationStatus.CANCELLED,
                error_class=attempt.error_class,
            )
        raise ValueError("attempt is not terminal and cannot be observed yet")


@dataclass(frozen=True)
class OrchestrationCyclePlan:
    cycle_index: int
    execution_mode: ExecutionMode
    candidates: tuple[OrchestrationCandidate, ...]
    accepted_snapshot: tuple[str, ...]
    missing_snapshot: tuple[str, ...]
    delay_seconds: int
    result_reference_snapshot: str | None = None

    @property
    def candidate_order(self) -> tuple[ExecutionTargetSnapshot, ...]:
        return tuple(item.target for item in self.candidates)

    def as_task_cycle_decision(self, *, recorded_at: datetime) -> TaskCycleDecision:
        return TaskCycleDecision(
            cycle_index=self.cycle_index,
            candidate_order=self.candidate_order,
            accepted_snapshot=self.accepted_snapshot,
            missing_snapshot=self.missing_snapshot,
            recorded_at=recorded_at,
            delay_seconds=self.delay_seconds,
            result_reference_snapshot=self.result_reference_snapshot,
        )


class OrchestrationDisposition(StrEnum):
    DISPATCH = "DISPATCH"
    NEXT_CYCLE = "NEXT_CYCLE"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class OrchestrationDecision:
    disposition: OrchestrationDisposition
    progress: RequirementProgress
    cycle_index: int
    attempt_index: int | None = None
    next_target: ExecutionTargetSnapshot | None = None
    delay_seconds: int = 0
    result_reference: str | None = None
    reason_code: str | None = None
    terminal_quarantine_targets: tuple[ExecutionTargetSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        if self.disposition is OrchestrationDisposition.DISPATCH:
            if self.next_target is None or self.attempt_index is None:
                raise ValueError("DISPATCH requires target and attempt_index")
        elif self.next_target is not None or self.attempt_index is not None:
            raise ValueError("non-DISPATCH decision cannot contain dispatch fields")


_TERMINAL_PROVIDER_OUTCOMES = frozenset(
    {
        ProviderOutcome.TERMINAL_ERROR,
        ProviderOutcome.CREDIT_EXHAUSTED,
        ProviderOutcome.QUOTA_EXHAUSTED,
        ProviderOutcome.AUTH_FAILURE,
    }
)


class CanonicalOrchestrationPolicyEngine:
    """Stateless canonical policy decisions over durable execution facts."""

    def plan_next_cycle(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        policy: OrchestrationPolicy,
        candidates: tuple[OrchestrationCandidate, ...],
        now: datetime,
        prior_retry_after_seconds: tuple[int | None, ...] = (),
    ) -> OrchestrationCyclePlan | OrchestrationDecision:
        _require_aware(now, "now")
        self._validate_context(task=task, session=session)

        preflight = self._preflight(task=task, session=session, policy=policy, now=now)
        if preflight is not None:
            return preflight

        cycle_index = task.current_cycle + 1
        ranked = self._eligible_candidates(
            task=task,
            session=session,
            candidates=candidates,
            now=now,
        )
        if not ranked:
            return self._terminal_from_progress(
                progress=RequirementProgress(
                    accepted=task.accepted_requirements,
                    missing=task.missing_requirements,
                ),
                cycle_index=task.current_cycle,
                reason_code="NO_ELIGIBLE_TARGET",
            )

        delay = 0
        if task.current_cycle > 0:
            delay = self._effective_cycle_delay(
                policy=policy,
                retry_after_seconds=prior_retry_after_seconds,
            )

        return OrchestrationCyclePlan(
            cycle_index=cycle_index,
            execution_mode=task.requested_execution_mode,
            candidates=ranked,
            accepted_snapshot=task.accepted_requirements,
            missing_snapshot=task.missing_requirements,
            delay_seconds=delay,
            result_reference_snapshot=task.result_reference,
        )

    def evaluate_cycle(
        self,
        *,
        plan: OrchestrationCyclePlan,
        policy: OrchestrationPolicy,
        observations: tuple[AttemptObservation, ...],
        attempts_used_before_cycle: int,
    ) -> OrchestrationDecision:
        if attempts_used_before_cycle < 0:
            raise ValueError("attempts_used_before_cycle cannot be negative")
        if plan.cycle_index < 1:
            raise ValueError("cycle_index must be positive")
        if len(observations) > len(plan.candidates):
            raise ValueError("cycle has more observations than planned candidates")

        progress = RequirementProgress(
            accepted=plan.accepted_snapshot,
            missing=plan.missing_snapshot,
        )
        retry_after_values: list[int | None] = []
        quarantines: list[ExecutionTargetSnapshot] = []
        last_progress_reference: str | None = plan.result_reference_snapshot

        for index, observation in enumerate(observations):
            expected_target = plan.candidates[index].target
            if observation.target != expected_target:
                raise ValueError("attempt observation does not match frozen candidate order")

            if observation.status is AttemptObservationStatus.AMBIGUOUS:
                return OrchestrationDecision(
                    disposition=OrchestrationDisposition.UNAVAILABLE,
                    progress=progress,
                    cycle_index=plan.cycle_index,
                    reason_code="AMBIGUOUS_PROVIDER_EFFECT",
                )
            if observation.status is AttemptObservationStatus.CANCELLED:
                return OrchestrationDecision(
                    disposition=OrchestrationDisposition.CANCELLED,
                    progress=progress,
                    cycle_index=plan.cycle_index,
                    reason_code="TASK_CANCELLED",
                )

            assert observation.outcome is not None
            progress = self._apply_completed_observation(
                progress=progress,
                observation=observation,
            )

            if observation.response_reference is not None and observation.accepted_requirements:
                last_progress_reference = observation.response_reference

            if observation.outcome in _TERMINAL_PROVIDER_OUTCOMES:
                quarantines.append(observation.target)

            if observation.outcome is ProviderOutcome.SUCCESS:
                if index != len(observations) - 1:
                    raise ValueError("observations cannot continue after SUCCESS")
                return OrchestrationDecision(
                    disposition=OrchestrationDisposition.COMPLETE,
                    progress=progress,
                    cycle_index=plan.cycle_index,
                    result_reference=observation.response_reference,
                    reason_code="REQUIREMENTS_SATISFIED",
                    terminal_quarantine_targets=tuple(quarantines),
                )

            retry_after_values.append(observation.retry_after_seconds)

            if (
                plan.execution_mode is ExecutionMode.EXPLICIT_TARGET
                and observation.outcome in _TERMINAL_PROVIDER_OUTCOMES
            ):
                return self._terminal_from_progress(
                    progress=progress,
                    cycle_index=plan.cycle_index,
                    result_reference=last_progress_reference,
                    reason_code="EXPLICIT_TARGET_TERMINAL_FAILURE",
                    terminal_quarantine_targets=tuple(quarantines),
                )

        attempts_used = attempts_used_before_cycle + len(observations)
        if attempts_used >= policy.max_attempts:
            return self._terminal_from_progress(
                progress=progress,
                cycle_index=plan.cycle_index,
                result_reference=last_progress_reference,
                reason_code="MAX_ATTEMPTS_EXHAUSTED",
                terminal_quarantine_targets=tuple(quarantines),
            )

        if len(observations) < len(plan.candidates):
            candidate = plan.candidates[len(observations)]
            return OrchestrationDecision(
                disposition=OrchestrationDisposition.DISPATCH,
                progress=progress,
                cycle_index=plan.cycle_index,
                attempt_index=attempts_used + 1,
                next_target=candidate.target,
                delay_seconds=plan.delay_seconds if not observations else 0,
                reason_code=(
                    "CYCLE_START"
                    if not observations
                    else "ESCALATE_TO_NEXT_CANDIDATE"
                ),
                terminal_quarantine_targets=tuple(quarantines),
            )

        if plan.cycle_index >= policy.max_cycles:
            return self._terminal_from_progress(
                progress=progress,
                cycle_index=plan.cycle_index,
                result_reference=last_progress_reference,
                reason_code="MAX_CYCLES_EXHAUSTED",
                terminal_quarantine_targets=tuple(quarantines),
            )

        return OrchestrationDecision(
            disposition=OrchestrationDisposition.NEXT_CYCLE,
            progress=progress,
            cycle_index=plan.cycle_index,
            delay_seconds=self._effective_cycle_delay(
                policy=policy,
                retry_after_seconds=tuple(retry_after_values),
            ),
            result_reference=last_progress_reference,
            reason_code="CYCLE_EXHAUSTED",
            terminal_quarantine_targets=tuple(quarantines),
        )

    @staticmethod
    def _validate_context(*, task: ExecutionTask, session: ExecutionSession) -> None:
        if task.session_id != session.session_id:
            raise ValueError("task session_id does not match session")
        if task.ownership != session.ownership:
            raise PermissionError("task/session ownership mismatch")
        if task.effective_policy_version_id != session.policy.effective_policy_version_id:
            raise ValueError("task policy version does not match session snapshot")

    def _preflight(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        policy: OrchestrationPolicy,
        now: datetime,
    ) -> OrchestrationDecision | None:
        progress = RequirementProgress(
            accepted=task.accepted_requirements,
            missing=task.missing_requirements,
        )
        if task.status in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}:
            return OrchestrationDecision(
                disposition=OrchestrationDisposition.CANCELLED,
                progress=progress,
                cycle_index=task.current_cycle,
                reason_code="TASK_CANCELLED",
            )
        if task.status.terminal:
            raise ValueError("terminal task cannot be orchestrated")
        if task.status is not TaskStatus.RUNNING:
            raise ValueError("orchestration requires RUNNING task")
        if session.status is not SessionStatus.ACTIVE:
            return OrchestrationDecision(
                disposition=OrchestrationDisposition.UNAVAILABLE,
                progress=progress,
                cycle_index=task.current_cycle,
                reason_code="SESSION_NOT_ACTIVE",
            )
        if policy.deadline_at is not None and now >= policy.deadline_at:
            return self._terminal_from_progress(
                progress=progress,
                cycle_index=task.current_cycle,
                reason_code="ORCHESTRATION_DEADLINE_EXHAUSTED",
            )
        if task.current_cycle >= policy.max_cycles:
            return self._terminal_from_progress(
                progress=progress,
                cycle_index=task.current_cycle,
                reason_code="MAX_CYCLES_EXHAUSTED",
            )
        return None

    def _eligible_candidates(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        candidates: tuple[OrchestrationCandidate, ...],
        now: datetime,
    ) -> tuple[OrchestrationCandidate, ...]:
        targets = [candidate.target for candidate in candidates]
        if len(set(targets)) != len(targets):
            raise ValueError("candidate targets must be unique")

        authorized = set(session.policy.authorized_targets)
        runtime = {item.target: item for item in session.target_runtime}
        eligible: list[OrchestrationCandidate] = []

        for candidate in candidates:
            if not candidate.eligible or candidate.target not in authorized:
                continue

            state = runtime.get(candidate.target)
            if state is not None:
                if state.health is TargetHealth.UNAVAILABLE:
                    continue
                if state.quarantine is QuarantineState.TERMINAL:
                    continue
                if (
                    state.quarantine is QuarantineState.TEMPORARY
                    and state.quarantine_until is not None
                    and state.quarantine_until > now
                ):
                    continue

            if task.requested_execution_mode is ExecutionMode.EXPLICIT_TARGET:
                assert task.effective_target is not None
                if candidate.target != task.effective_target:
                    continue
            eligible.append(candidate)

        if task.requested_execution_mode is ExecutionMode.EXPLICIT_TARGET:
            return tuple(eligible[:1])

        return rank_auto_candidates(tuple(eligible))

    @staticmethod
    def _apply_completed_observation(
        *,
        progress: RequirementProgress,
        observation: AttemptObservation,
    ) -> RequirementProgress:
        current_missing = set(progress.missing)
        accepted_now = set(observation.accepted_requirements)
        missing_after = set(observation.missing_requirements)

        if observation.outcome is ProviderOutcome.SUCCESS:
            if accepted_now != current_missing or missing_after:
                raise ValueError("SUCCESS must satisfy every remaining requirement")
        elif observation.outcome is ProviderOutcome.PARTIAL:
            if not accepted_now or not accepted_now < current_missing:
                raise ValueError("PARTIAL must accept a strict non-empty subset")
            if missing_after != current_missing - accepted_now:
                raise ValueError("PARTIAL missing requirements do not match remaining work")
        else:
            if accepted_now:
                raise ValueError("non-progress outcome cannot accept requirements")
            if missing_after != current_missing:
                raise ValueError("non-progress outcome must preserve missing requirements")

        accepted_items = list(progress.accepted)
        for item in observation.accepted_requirements:
            if item not in accepted_items:
                accepted_items.append(item)
        missing = tuple(item for item in progress.missing if item in missing_after)
        return RequirementProgress(accepted=tuple(accepted_items), missing=missing)

    @staticmethod
    def _effective_cycle_delay(
        *,
        policy: OrchestrationPolicy,
        retry_after_seconds: tuple[int | None, ...],
    ) -> int:
        bounded_retry = 0
        for raw in retry_after_seconds:
            if raw is None:
                continue
            bounded_retry = max(
                bounded_retry,
                min(raw, policy.retry_after_cap_seconds),
            )
        return max(policy.cycle_delay_seconds, bounded_retry)

    @staticmethod
    def _terminal_from_progress(
        *,
        progress: RequirementProgress,
        cycle_index: int,
        reason_code: str,
        result_reference: str | None = None,
        terminal_quarantine_targets: tuple[ExecutionTargetSnapshot, ...] = (),
    ) -> OrchestrationDecision:
        disposition = (
            OrchestrationDisposition.PARTIAL
            if progress.accepted and progress.missing
            else OrchestrationDisposition.UNAVAILABLE
        )
        return OrchestrationDecision(
            disposition=disposition,
            progress=progress,
            cycle_index=cycle_index,
            result_reference=result_reference,
            reason_code=reason_code,
            terminal_quarantine_targets=terminal_quarantine_targets,
        )
