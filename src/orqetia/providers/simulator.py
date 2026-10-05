"""Deterministic offline provider used to prove ORQETIA orchestration without cost."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .protocol import (
    OutputKind,
    ProviderAdapter,
    ProviderAttemptRequest,
    ProviderAttemptResult,
    ProviderCostMetadata,
    ProviderOutcome,
    ProviderTarget,
    ProviderUsage,
)

ORQETIA_TEST_PROVIDER = "ORQETIA_TEST_PROVIDER"


class SimulatorScenario(str):
    SUCCESS = "success"
    STRUCTURED_SUCCESS = "structured_success"
    REQUIREMENT_NOT_SATISFIED = "requirement_not_satisfied"
    PARTIAL = "partial"
    TRANSIENT_ERROR = "transient_error"
    TERMINAL_ERROR = "terminal_error"
    RATE_LIMIT = "rate_limit"
    RETRY_AFTER = "retry_after"
    TIMEOUT = "timeout"
    CREDIT_EXHAUSTED = "credit_exhausted"
    QUOTA = "quota"
    AUTH_FAILURE = "auth_failure"
    MALFORMED_OUTPUT = "malformed_output"
    UNAVAILABLE = "unavailable"


_SCENARIOS = {
    SimulatorScenario.SUCCESS,
    SimulatorScenario.STRUCTURED_SUCCESS,
    SimulatorScenario.REQUIREMENT_NOT_SATISFIED,
    SimulatorScenario.PARTIAL,
    SimulatorScenario.TRANSIENT_ERROR,
    SimulatorScenario.TERMINAL_ERROR,
    SimulatorScenario.RATE_LIMIT,
    SimulatorScenario.RETRY_AFTER,
    SimulatorScenario.TIMEOUT,
    SimulatorScenario.CREDIT_EXHAUSTED,
    SimulatorScenario.QUOTA,
    SimulatorScenario.AUTH_FAILURE,
    SimulatorScenario.MALFORMED_OUTPUT,
    SimulatorScenario.UNAVAILABLE,
}


@dataclass(frozen=True)
class SimulatorCandidate:
    target: ProviderTarget
    comparison_group: str
    comparison_group_rank: int
    estimated_cost: Decimal | None
    policy_rank: int
    currency: str | None = None
    native_unit: str | None = None

    def __post_init__(self) -> None:
        if not self.comparison_group.strip():
            raise ValueError("comparison_group is required")
        if self.comparison_group_rank < 0 or self.policy_rank < 0:
            raise ValueError("candidate ranks cannot be negative")
        if self.estimated_cost is None and self.comparison_group != "UNPRICED":
            raise ValueError("unpriced candidate must use UNPRICED comparison_group")
        if self.estimated_cost is not None and self.estimated_cost < 0:
            raise ValueError("estimated_cost cannot be negative")
        if self.estimated_cost is not None and self.comparison_group == "UNPRICED":
            raise ValueError("UNPRICED candidate cannot contain estimated_cost")


@dataclass(frozen=True)
class SimulatorStep:
    scenario: str
    expected_cycle: int
    expected_attempt_index: int
    expected_target: ProviderTarget | None = None
    accepted_requirements: tuple[str, ...] = ()
    response_reference: str | None = None
    simulated_latency_ms: int = 0
    retry_after_seconds: int | None = None
    error_class: str | None = None
    usage: ProviderUsage = ProviderUsage()
    cost: ProviderCostMetadata | None = None

    def __post_init__(self) -> None:
        if self.scenario not in _SCENARIOS:
            raise ValueError(f"unknown simulator scenario: {self.scenario}")
        if self.expected_cycle < 1 or self.expected_attempt_index < 1:
            raise ValueError("expected cycle/attempt index must be positive")
        if self.simulated_latency_ms < 0:
            raise ValueError("simulated_latency_ms cannot be negative")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds cannot be negative")
        if self.scenario == SimulatorScenario.RETRY_AFTER:
            if self.retry_after_seconds is None:
                raise ValueError("retry_after scenario requires retry_after_seconds")
        if len(set(self.accepted_requirements)) != len(self.accepted_requirements):
            raise ValueError("accepted_requirements must not contain duplicates")


@dataclass(frozen=True)
class SimulatorFixture:
    fixture_id: str
    seed: str
    candidates: tuple[SimulatorCandidate, ...]
    steps: tuple[SimulatorStep, ...]

    def __post_init__(self) -> None:
        if not self.fixture_id.strip() or len(self.fixture_id) > 200:
            raise ValueError("fixture_id must contain 1..200 characters")
        if not self.seed.strip() or len(self.seed) > 200:
            raise ValueError("seed must contain 1..200 characters")
        if not self.steps:
            raise ValueError("fixture must contain at least one step")


@dataclass(frozen=True)
class SimulatorInvocation:
    fixture_id: str
    seed: str
    invocation_index: int
    attempt_id: object
    operation: str
    task_id: object | None
    session_id: object | None
    cycle: int
    attempt_index: int
    target: ProviderTarget
    scenario: str
    outcome: ProviderOutcome
    simulated_latency_ms: int
    retry_after_seconds: int | None
    usage: ProviderUsage


class SimulatorFixtureExhausted(RuntimeError):
    pass


class SimulatorFixtureMismatch(RuntimeError):
    pass


class DeterministicTestProvider(ProviderAdapter):
    """Execute fixture steps exactly once each, without network or wall-clock sleeps."""

    def __init__(self, fixture: SimulatorFixture) -> None:
        self._fixture = fixture
        self._cursor = 0
        self._invocations: list[SimulatorInvocation] = []

    @property
    def fixture(self) -> SimulatorFixture:
        return self._fixture

    @property
    def invocations(self) -> tuple[SimulatorInvocation, ...]:
        return tuple(self._invocations)

    async def invoke(self, request: ProviderAttemptRequest) -> ProviderAttemptResult:
        if self._cursor >= len(self._fixture.steps):
            raise SimulatorFixtureExhausted(
                f"fixture {self._fixture.fixture_id} has no remaining steps"
            )

        step = self._fixture.steps[self._cursor]
        self._verify_step(step, request)
        result = self._result_for(step, request)

        self._cursor += 1
        self._invocations.append(
            SimulatorInvocation(
                fixture_id=self._fixture.fixture_id,
                seed=self._fixture.seed,
                invocation_index=self._cursor,
                attempt_id=request.attempt_id,
                operation=request.operation,
                task_id=request.task_id,
                session_id=request.session_id,
                cycle=request.cycle,
                attempt_index=request.attempt_index,
                target=request.target,
                scenario=step.scenario,
                outcome=result.outcome,
                simulated_latency_ms=result.simulated_latency_ms,
                retry_after_seconds=result.retry_after_seconds,
                usage=result.usage,
            )
        )
        return result

    def _verify_step(
        self,
        step: SimulatorStep,
        request: ProviderAttemptRequest,
    ) -> None:
        if request.cycle != step.expected_cycle:
            raise SimulatorFixtureMismatch(
                f"expected cycle {step.expected_cycle}, received {request.cycle}"
            )
        if request.attempt_index != step.expected_attempt_index:
            raise SimulatorFixtureMismatch(
                "expected attempt index "
                f"{step.expected_attempt_index}, received {request.attempt_index}"
            )
        if step.expected_target is not None and request.target != step.expected_target:
            raise SimulatorFixtureMismatch(
                f"expected target {step.expected_target}, received {request.target}"
            )

    @staticmethod
    def _result_for(
        step: SimulatorStep,
        request: ProviderAttemptRequest,
    ) -> ProviderAttemptResult:
        missing = request.missing_requirements
        accepted = tuple(item for item in step.accepted_requirements if item in missing)

        scenario = step.scenario
        if scenario in {SimulatorScenario.SUCCESS, SimulatorScenario.STRUCTURED_SUCCESS}:
            accepted = missing
            remaining: tuple[str, ...] = ()
            outcome = ProviderOutcome.SUCCESS
        elif scenario == SimulatorScenario.PARTIAL:
            remaining = tuple(item for item in missing if item not in set(accepted))
            if not accepted or not remaining:
                raise SimulatorFixtureMismatch(
                    "partial scenario must accept a strict non-empty subset"
                )
            outcome = ProviderOutcome.PARTIAL
        else:
            accepted = ()
            remaining = missing
            outcome = _outcome_for_scenario(scenario)

        output_kind = _output_kind_for_scenario(scenario)
        response_reference = step.response_reference
        if output_kind in {OutputKind.TEXT, OutputKind.STRUCTURED, OutputKind.MALFORMED}:
            response_reference = response_reference or (
                f"simulator://{request.attempt_id}/{scenario}"
            )

        retry_after = step.retry_after_seconds
        if scenario == SimulatorScenario.RATE_LIMIT and retry_after is None:
            retry_after = None

        return ProviderAttemptResult(
            attempt_id=request.attempt_id,
            outcome=outcome,
            output_kind=output_kind,
            accepted_requirements=accepted,
            missing_requirements=remaining,
            response_reference=response_reference,
            error_class=step.error_class or _default_error_class(scenario),
            retry_after_seconds=retry_after,
            simulated_latency_ms=step.simulated_latency_ms,
            usage=step.usage,
            cost=step.cost,
        )


def _outcome_for_scenario(scenario: str) -> ProviderOutcome:
    mapping = {
        SimulatorScenario.REQUIREMENT_NOT_SATISFIED: (
            ProviderOutcome.REQUIREMENT_NOT_SATISFIED
        ),
        SimulatorScenario.TRANSIENT_ERROR: ProviderOutcome.TRANSIENT_ERROR,
        SimulatorScenario.TERMINAL_ERROR: ProviderOutcome.TERMINAL_ERROR,
        SimulatorScenario.RATE_LIMIT: ProviderOutcome.RATE_LIMITED,
        SimulatorScenario.RETRY_AFTER: ProviderOutcome.RATE_LIMITED,
        SimulatorScenario.TIMEOUT: ProviderOutcome.TIMEOUT,
        SimulatorScenario.CREDIT_EXHAUSTED: ProviderOutcome.CREDIT_EXHAUSTED,
        SimulatorScenario.QUOTA: ProviderOutcome.QUOTA_EXHAUSTED,
        SimulatorScenario.AUTH_FAILURE: ProviderOutcome.AUTH_FAILURE,
        SimulatorScenario.MALFORMED_OUTPUT: ProviderOutcome.MALFORMED_OUTPUT,
        SimulatorScenario.UNAVAILABLE: ProviderOutcome.UNAVAILABLE,
    }
    try:
        return mapping[scenario]
    except KeyError as exc:
        raise SimulatorFixtureMismatch(f"scenario has no non-success outcome: {scenario}") from exc


def _output_kind_for_scenario(scenario: str) -> OutputKind:
    if scenario == SimulatorScenario.STRUCTURED_SUCCESS:
        return OutputKind.STRUCTURED
    if scenario == SimulatorScenario.SUCCESS:
        return OutputKind.TEXT
    if scenario == SimulatorScenario.PARTIAL:
        return OutputKind.TEXT
    if scenario == SimulatorScenario.MALFORMED_OUTPUT:
        return OutputKind.MALFORMED
    return OutputKind.NONE


def _default_error_class(scenario: str) -> str | None:
    mapping = {
        SimulatorScenario.REQUIREMENT_NOT_SATISFIED: "REQUIREMENT_NOT_SATISFIED",
        SimulatorScenario.TRANSIENT_ERROR: "TRANSIENT_PROVIDER_ERROR",
        SimulatorScenario.TERMINAL_ERROR: "TERMINAL_PROVIDER_ERROR",
        SimulatorScenario.RATE_LIMIT: "RATE_LIMIT",
        SimulatorScenario.RETRY_AFTER: "RATE_LIMIT",
        SimulatorScenario.TIMEOUT: "TIMEOUT",
        SimulatorScenario.CREDIT_EXHAUSTED: "CREDIT_EXHAUSTED",
        SimulatorScenario.QUOTA: "QUOTA_EXHAUSTED",
        SimulatorScenario.AUTH_FAILURE: "AUTH_FAILURE",
        SimulatorScenario.MALFORMED_OUTPUT: "MALFORMED_OUTPUT",
        SimulatorScenario.UNAVAILABLE: "UNAVAILABLE",
    }
    return mapping.get(scenario)
