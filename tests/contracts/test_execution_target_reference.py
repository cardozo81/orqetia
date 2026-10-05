from __future__ import annotations

import unittest

from tests.characterization.reference_contract import FinalState, Invocation, Outcome, Policy
from tests.contracts.execution_target_reference import (
    Candidate,
    Mode,
    Target,
    explicit_retry_attempts,
    mode_or_default,
    rank_auto,
    resolve_explicit,
    run_auto,
    run_requirement_cycle,
)


class ExecutionTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cheap = Candidate(
            Target("OPENAI", "cheap", "MEDIUM"),
            "MONEY:USD", 10, 0.10, "USD", 10,
        )
        self.expensive = Candidate(
            Target("ANTHROPIC", "expensive", "DEFAULT"),
            "MONEY:USD", 10, 0.30, "USD", 20,
        )

    def test_omitted_execution_defaults_to_auto(self) -> None:
        self.assertEqual(mode_or_default(None), Mode.AUTO)

    def test_auto_orders_lower_comparable_cost_first(self) -> None:
        ranked = rank_auto((self.expensive, self.cheap))
        self.assertEqual(tuple(item.target for item in ranked), (self.cheap.target, self.expensive.target))

    def test_cheaper_complete_prevents_expensive_call(self) -> None:
        state, calls = run_auto(
            candidates=lambda: (self.expensive, self.cheap),
            outcome_for=lambda candidate, cycle: Invocation(Outcome.COMPLETE if candidate is self.cheap else Outcome.NO_PROGRESS),
            policy=Policy(max_cycles=1, cycle_delay_seconds=0),
        )
        self.assertEqual(state, FinalState.COMPLETE)
        self.assertEqual(calls, [self.cheap.target])

    def test_no_progress_escalates_to_next_candidate(self) -> None:
        state, calls = run_auto(
            candidates=lambda: (self.expensive, self.cheap),
            outcome_for=lambda candidate, cycle: Invocation(
                Outcome.NO_PROGRESS if candidate is self.cheap else Outcome.COMPLETE
            ),
            policy=Policy(max_cycles=1, cycle_delay_seconds=0),
        )
        self.assertEqual(state, FinalState.COMPLETE)
        self.assertEqual(calls, [self.cheap.target, self.expensive.target])

    def test_partial_preserves_accepted_and_next_candidate_receives_only_missing(self) -> None:
        state, calls = run_requirement_cycle(
            candidates=(self.expensive, self.cheap),
            initial_missing={"summary", "schema"},
            response_for=lambda candidate, missing: (
                ({"summary"}, False) if candidate is self.cheap else (set(missing), True)
            ),
        )
        self.assertEqual(state.accepted, {"summary", "schema"})
        self.assertEqual(state.missing, set())
        self.assertEqual(calls[0], (self.cheap.target, frozenset({"summary", "schema"})))
        self.assertEqual(calls[1], (self.expensive.target, frozenset({"schema"})))

    def test_new_cycle_re_evaluates_cost_ordering(self) -> None:
        cheap_now = self.cheap
        other = self.expensive
        cycle_seen: list[int] = []

        def candidates() -> tuple[Candidate, ...]:
            if cycle_seen and cycle_seen[-1] >= 1:
                return (
                    Candidate(cheap_now.target, "MONEY:USD", 10, 0.50, "USD", 10),
                    Candidate(other.target, "MONEY:USD", 10, 0.05, "USD", 20),
                )
            return (cheap_now, other)

        def outcome(candidate: Candidate, cycle: int) -> Invocation:
            cycle_seen.append(cycle)
            if cycle == 1:
                return Invocation(Outcome.TRANSIENT_FAILURE)
            return Invocation(Outcome.COMPLETE)

        state, calls = run_auto(
            candidates=candidates,
            outcome_for=outcome,
            policy=Policy(max_cycles=2, cycle_delay_seconds=0),
        )
        self.assertEqual(state, FinalState.COMPLETE)
        self.assertEqual(calls[0], cheap_now.target)
        self.assertEqual(calls[-1], other.target)

    def test_unpriced_remains_eligible_and_not_zero(self) -> None:
        unpriced = Candidate(Target("MISTRAL", "u", "DEFAULT"), "UNPRICED", 30, None, None, 1)
        ranked = rank_auto((unpriced, self.expensive, self.cheap))
        self.assertEqual(tuple(item.target for item in ranked), (self.cheap.target, self.expensive.target, unpriced.target))

    def test_different_currency_uses_group_order_not_implicit_fx(self) -> None:
        usd = Candidate(Target("OPENAI", "usd", "DEFAULT"), "MONEY:USD", 10, 999.0, "USD", 1)
        eur = Candidate(Target("MISTRAL", "eur", "DEFAULT"), "MONEY:EUR", 20, 0.0001, "EUR", 1)
        ranked = rank_auto((eur, usd))
        self.assertEqual(tuple(item.target for item in ranked), (usd.target, eur.target))

    def test_explicit_target_resolves_defaults_and_freezes(self) -> None:
        target = Target("OPENAI", "gpt-approved", "MEDIUM")
        resolved = resolve_explicit(
            {"mode": "EXPLICIT_TARGET", "target": {"provider": "OPENAI"}},
            allowed_targets=(target,),
            default_model={"OPENAI": "gpt-approved"},
            default_profile={("OPENAI", "gpt-approved"): "MEDIUM"},
        )
        self.assertEqual(resolved, target)

    def test_invalid_explicit_target_rejected_instead_of_fallback(self) -> None:
        target = Target("OPENAI", "gpt-approved", "MEDIUM")
        with self.assertRaises(PermissionError):
            resolve_explicit(
                {"mode": "EXPLICIT_TARGET", "target": {"provider": "ANTHROPIC"}},
                allowed_targets=(target,),
                default_model={"OPENAI": "gpt-approved"},
                default_profile={("OPENAI", "gpt-approved"): "MEDIUM"},
            )

    def test_explicit_retries_keep_target_but_create_distinct_attempt_ids(self) -> None:
        target = Target("OPENAI", "gpt-approved", "MEDIUM")
        attempts = explicit_retry_attempts(
            target=target,
            attempt_ids=("attempt-1", "attempt-2", "attempt-3"),
            request_fingerprint="sha256:same",
        )
        self.assertEqual(tuple(item[1] for item in attempts), (target, target, target))
        self.assertEqual(tuple(item[0] for item in attempts), ("attempt-1", "attempt-2", "attempt-3"))

    def test_cost_change_does_not_change_explicit_target(self) -> None:
        target = Target("OPENAI", "gpt-approved", "MEDIUM")
        attempts = explicit_retry_attempts(
            target=target,
            attempt_ids=("a1", "a2"),
            request_fingerprint="sha256:same",
        )
        hypothetical_cheaper_other = Target("ANTHROPIC", "cheap", "DEFAULT")
        self.assertTrue(all(item[1] == target for item in attempts))
        self.assertNotIn(hypothetical_cheaper_other, tuple(item[1] for item in attempts))

    def test_terminal_explicit_failure_has_no_cross_target_fallback(self) -> None:
        target = Target("OPENAI", "gpt-approved", "MEDIUM")
        explicit_candidate = Candidate(target, "MONEY:USD", 10, 1.0, "USD", 1)
        state, calls = run_auto(
            candidates=lambda: (explicit_candidate,),
            outcome_for=lambda candidate, cycle: Invocation(Outcome.PROVIDER_TERMINAL),
            policy=Policy(max_cycles=1, cycle_delay_seconds=0),
        )
        self.assertEqual(state, FinalState.UNAVAILABLE)
        self.assertEqual(calls, [target])


if __name__ == "__main__":
    unittest.main()
