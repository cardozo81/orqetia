"""Test-only reference oracle for canonical RASAi orchestration/accounting semantics.

This module is not production code and must never be imported by ORQETIA runtime.
It freezes behavior characterized in docs/architecture/rasai-canonical-characterization-matrix.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Callable, Iterable, Mapping, Sequence


TERMINAL_ERRORS = frozenset(
    {"AUTH_ERROR", "CREDIT_ERROR", "QUOTA_ERROR", "MODEL_ERROR", "PERMISSION_ERROR"}
)


class Outcome(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL_PROGRESS = "PARTIAL_PROGRESS"
    INPUT_BLOCKED = "INPUT_BLOCKED"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    PROVIDER_TERMINAL = "PROVIDER_TERMINAL"
    NO_PROGRESS = "NO_PROGRESS"


class FinalState(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INPUT_BLOCKED = "INPUT_BLOCKED"


@dataclass(frozen=True)
class Policy:
    max_cycles: int = 3
    cycle_delay_seconds: float = 60.0
    retry_after_cap_seconds: float = 300.0


@dataclass(frozen=True)
class Invocation:
    outcome: Outcome
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class Execution:
    final_state: FinalState
    cycles_executed: int
    provider_calls: int
    progress: bool
    last_outcome: Outcome | None = None


def is_terminal(error_class: object) -> bool:
    token = str(getattr(error_class, "value", error_class) or "").strip().upper()
    return token in TERMINAL_ERRORS


def unique_cycle_candidates(candidates: Iterable[object]) -> tuple[object, ...]:
    seen: set[str] = set()
    output: list[object] = []
    for candidate in candidates:
        key = str(getattr(candidate, "name", candidate)).strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return tuple(output)


def effective_cycle_delay(
    configured_delay_seconds: float,
    retry_after_seconds: Iterable[float | None] = (),
    *,
    cap_seconds: float = 300.0,
) -> float:
    configured = max(0.0, float(configured_delay_seconds))
    cap = max(0.0, min(float(cap_seconds), 300.0))
    retry_delay = 0.0
    for raw in retry_after_seconds:
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        retry_delay = max(retry_delay, min(value, cap))
    return max(configured, retry_delay)


def run_need(
    *,
    candidates: Callable[[], Sequence[object]],
    invoke: Callable[[object, int, int], Invocation],
    policy: Policy,
    sleeper: Callable[[float], None] = lambda _seconds: None,
) -> Execution:
    progress = False
    calls = 0
    last_outcome: Outcome | None = None
    cycles_executed = 0

    for cycle in range(1, policy.max_cycles + 1):
        pool = unique_cycle_candidates(candidates())
        if not pool:
            break
        cycles_executed = cycle
        retry_after_values: list[float | None] = []

        for candidate in pool:
            calls += 1
            result = invoke(candidate, cycle, calls)
            last_outcome = result.outcome
            if result.outcome is Outcome.COMPLETE:
                return Execution(FinalState.COMPLETE, cycles_executed, calls, True, result.outcome)
            if result.outcome is Outcome.INPUT_BLOCKED:
                return Execution(FinalState.INPUT_BLOCKED, cycles_executed, calls, progress, result.outcome)
            if result.outcome is Outcome.PARTIAL_PROGRESS:
                progress = True
            elif result.outcome is Outcome.TRANSIENT_FAILURE:
                retry_after_values.append(result.retry_after_seconds)

        if cycle >= policy.max_cycles:
            break
        if not unique_cycle_candidates(candidates()):
            break

        delay = effective_cycle_delay(
            policy.cycle_delay_seconds,
            retry_after_values,
            cap_seconds=policy.retry_after_cap_seconds,
        )
        if delay > 0:
            sleeper(delay)

    return Execution(
        FinalState.PARTIAL if progress else FinalState.UNAVAILABLE,
        cycles_executed,
        calls,
        progress,
        last_outcome,
    )


class SingleAttemptAdapterOracle:
    """Adapter-level oracle: compatibility retry arguments never create logical retries."""

    def __init__(self) -> None:
        self.external_calls = 0

    def analyze(self, *, max_attempts: int = 3) -> None:
        del max_attempts
        self.external_calls += 1


class HealthOracle:
    def __init__(self, names: Sequence[str]) -> None:
        self._eligible = {name: True for name in names}

    def record_failure(self, name: str, error_class: str) -> None:
        if is_terminal(error_class):
            self._eligible[name] = False

    def is_eligible(self, name: str) -> bool:
        return self._eligible.get(name, False)


def rank_auto_candidates(candidates: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Preserve canonical priced-USD-before-UNPRICED ordering."""
    indexed = list(enumerate(candidates))

    def key(item: tuple[int, Mapping[str, object]]) -> tuple[float, float, int, int]:
        base_index, candidate = item
        amount = candidate.get("estimated_cost")
        currency = candidate.get("currency")
        rank = int(candidate.get("rank", 9999))
        priced = amount is not None and currency == "USD"
        if priced:
            return (0.0, float(amount), rank, base_index)
        return (1.0, float("inf"), base_index, rank)

    indexed.sort(key=key)
    return tuple(str(item["name"]) for _, item in indexed)


def canonical_total_tokens(source: Mapping[str, object]) -> int | None:
    total = source.get("total_tokens")
    if total is not None:
        return max(int(total), 0)
    input_tokens = source.get("input_tokens")
    output_tokens = source.get("output_tokens")
    if input_tokens is None and output_tokens is None:
        return None
    return max(int(input_tokens or 0), 0) + max(int(output_tokens or 0), 0)


def attempt_monetary_cost(attempt: Mapping[str, object]) -> tuple[float | None, str | None, str]:
    observed = attempt.get("observed_cost")
    if observed not in (None, ""):
        currency = attempt.get("observed_cost_currency") or attempt.get("cost_currency") or attempt.get("currency")
        return float(observed), str(currency).upper() if currency else None, "PROVIDER_OBSERVED"

    estimated = attempt.get("estimated_cost")
    if estimated not in (None, ""):
        currency = attempt.get("cost_currency") or attempt.get("currency")
        return float(estimated), str(currency).upper() if currency else None, "USAGE_DERIVED_ESTIMATE"

    legacy = attempt.get("estimated_cost_usd")
    if legacy not in (None, ""):
        return float(legacy), "USD", "LEGACY_USAGE_DERIVED_ESTIMATE"

    return None, None, "UNPRICED"


_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
    "native_usage_quantity",
)


def aggregate_attempt_costs(
    attempts: Iterable[Mapping[str, object]],
) -> tuple[tuple[tuple[str, float], ...], int]:
    totals: dict[str, float] = {}
    unpriced = 0
    for attempt in attempts:
        amount, currency, _basis = attempt_monetary_cost(attempt)
        if amount is None or not currency:
            if any(attempt.get(key) is not None for key in _USAGE_KEYS) or bool(attempt.get("native_usage")):
                unpriced += 1
            continue
        totals[currency] = totals.get(currency, 0.0) + amount
    return tuple((code, round(value, 10)) for code, value in sorted(totals.items())), unpriced


def usage_is_priceable(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    native_components: bool,
    cached_input_tokens: int | None,
    input_rate: float,
    cached_rate: float,
) -> bool:
    """Freeze fail-safe pricing boundary, not actual provider prices."""
    if native_components and (input_tokens is not None or output_tokens is not None):
        return False
    if native_components:
        return True
    if input_tokens is None or output_tokens is None:
        return False
    if cached_input_tokens is None and not math.isclose(input_rate, cached_rate, rel_tol=0.0, abs_tol=1e-12):
        return False
    return True
