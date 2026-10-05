from __future__ import annotations

import unittest

from tests.characterization.reference_contract import (
    Execution,
    FinalState,
    HealthOracle,
    Invocation,
    Outcome,
    Policy,
    SingleAttemptAdapterOracle,
    aggregate_attempt_costs,
    billable_output_tokens,
    canonical_total_tokens,
    effective_cycle_delay,
    is_terminal,
    rank_auto_candidates,
    run_need,
    unique_cycle_candidates,
    usage_is_priceable,
)


class CanonicalCharacterizationTests(unittest.TestCase):
    def test_char_001_adapter_is_single_attempt(self) -> None:
        adapter = SingleAttemptAdapterOracle()
        adapter.analyze(max_attempts=99)
        self.assertEqual(adapter.external_calls, 1)

    def test_char_002_provider_is_called_once_per_cycle_even_when_duplicated(self) -> None:
        calls: list[tuple[str, int]] = []

        def invoke(provider: object, cycle: int, _call: int) -> Invocation:
            calls.append((str(provider), cycle))
            return Invocation(Outcome.NO_PROGRESS)

        result = run_need(
            candidates=lambda: ("A", "A", "B", "B"),
            invoke=invoke,
            policy=Policy(max_cycles=1, cycle_delay_seconds=0),
        )
        self.assertEqual(calls, [("A", 1), ("B", 1)])
        self.assertEqual(result.provider_calls, 2)

    def test_char_003_pool_is_re_evaluated_between_cycles(self) -> None:
        pool = ["A"]
        seen: list[tuple[str, int]] = []

        def candidates() -> tuple[str, ...]:
            return tuple(pool)

        def invoke(provider: object, cycle: int, _call: int) -> Invocation:
            seen.append((str(provider), cycle))
            if cycle == 1:
                pool[:] = ["B"]
            return Invocation(Outcome.TRANSIENT_FAILURE)

        run_need(
            candidates=candidates,
            invoke=invoke,
            policy=Policy(max_cycles=2, cycle_delay_seconds=0),
        )
        self.assertEqual(seen, [("A", 1), ("B", 2)])

    def test_char_004_retry_after_is_bounded_and_timer_has_single_effective_delay(self) -> None:
        self.assertEqual(effective_cycle_delay(10, [5, 40], cap_seconds=30), 30)
        self.assertEqual(effective_cycle_delay(45, [1000], cap_seconds=30), 45)
        self.assertEqual(effective_cycle_delay(0, [-5, float("nan"), None]), 0)

    def test_char_005_terminal_error_taxonomy(self) -> None:
        for item in (
            "AUTH_ERROR",
            "CREDIT_ERROR",
            "QUOTA_ERROR",
            "MODEL_ERROR",
            "PERMISSION_ERROR",
        ):
            self.assertTrue(is_terminal(item))
        for item in ("NETWORK_ERROR", "TIMEOUT_ERROR", "HTTP_500", "RATE_LIMIT"):
            self.assertFalse(is_terminal(item))

    def test_char_006_terminal_quarantine_survives_across_needs(self) -> None:
        health = HealthOracle(("A", "B"))
        health.record_failure("A", "AUTH_ERROR")
        self.assertFalse(health.is_eligible("A"))
        self.assertTrue(health.is_eligible("B"))
        self.assertFalse(health.is_eligible("A"))

    def test_char_007_transient_failure_does_not_terminally_quarantine(self) -> None:
        health = HealthOracle(("A",))
        health.record_failure("A", "TIMEOUT_ERROR")
        self.assertTrue(health.is_eligible("A"))

    def test_char_008_explicit_provider_is_unitary_pool(self) -> None:
        self.assertEqual(unique_cycle_candidates(("OPENAI",)), ("OPENAI",))

    def test_char_009_010_unpriced_is_eligible_but_sorts_after_priced(self) -> None:
        ordered = rank_auto_candidates(
            (
                {"name": "UNPRICED_A", "estimated_cost": None, "currency": None, "rank": 1},
                {"name": "PRICED_EXPENSIVE", "estimated_cost": 0.3, "currency": "USD", "rank": 2},
                {"name": "PRICED_CHEAP", "estimated_cost": 0.1, "currency": "USD", "rank": 3},
                {"name": "UNPRICED_B", "estimated_cost": None, "currency": None, "rank": 0},
            )
        )
        self.assertEqual(
            ordered,
            ("PRICED_CHEAP", "PRICED_EXPENSIVE", "UNPRICED_A", "UNPRICED_B"),
        )

    def test_char_011_each_need_restarts_cycle_budget(self) -> None:
        def execute() -> Execution:
            return run_need(
                candidates=lambda: ("A",),
                invoke=lambda _provider, _cycle, _call: Invocation(Outcome.NO_PROGRESS),
                policy=Policy(max_cycles=2, cycle_delay_seconds=0),
            )

        first = execute()
        second = execute()
        self.assertEqual((first.cycles_executed, second.cycles_executed), (2, 2))

    def test_char_012_partial_progress_is_not_lost(self) -> None:
        outcomes = iter((Outcome.PARTIAL_PROGRESS, Outcome.TRANSIENT_FAILURE))
        result = run_need(
            candidates=lambda: ("A", "B"),
            invoke=lambda _provider, _cycle, _call: Invocation(next(outcomes)),
            policy=Policy(max_cycles=1, cycle_delay_seconds=0),
        )
        self.assertEqual(result.final_state, FinalState.PARTIAL)
        self.assertTrue(result.progress)

    def test_char_013_input_blocked_terminates_immediately(self) -> None:
        result = run_need(
            candidates=lambda: ("A", "B"),
            invoke=lambda _provider, _cycle, _call: Invocation(Outcome.INPUT_BLOCKED),
            policy=Policy(max_cycles=3, cycle_delay_seconds=0),
        )
        self.assertEqual(result.final_state, FinalState.INPUT_BLOCKED)
        self.assertEqual(result.provider_calls, 1)

    def test_char_015_provider_total_wins_and_reasoning_is_not_double_counted(self) -> None:
        self.assertEqual(
            canonical_total_tokens(
                {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "reasoning_tokens": 40,
                    "total_tokens": 151,
                }
            ),
            151,
        )
        self.assertEqual(
            canonical_total_tokens(
                {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 40}
            ),
            150,
        )

    def test_char_017_observed_cost_has_precedence_over_estimate(self) -> None:
        totals, unpriced = aggregate_attempt_costs(
            (
                {
                    "observed_cost": 2.5,
                    "observed_cost_currency": "EUR",
                    "estimated_cost": 1.0,
                    "cost_currency": "USD",
                    "input_tokens": 1,
                },
            )
        )
        self.assertEqual(totals, (("EUR", 2.5),))
        self.assertEqual(unpriced, 0)

    def test_char_018_currencies_are_never_implicitly_combined(self) -> None:
        totals, unpriced = aggregate_attempt_costs(
            (
                {"estimated_cost": 1.0, "cost_currency": "USD", "input_tokens": 1},
                {"estimated_cost": 2.0, "cost_currency": "EUR", "input_tokens": 1},
                {"input_tokens": 10},
            )
        )
        self.assertEqual(totals, (("EUR", 2.0), ("USD", 1.0)))
        self.assertEqual(unpriced, 1)

    def test_char_018_unpriced_is_not_zero(self) -> None:
        totals, unpriced = aggregate_attempt_costs(({"input_tokens": 10, "estimated_cost": None},))
        self.assertEqual(totals, ())
        self.assertEqual(unpriced, 1)

    def test_char_019_mixed_native_and_token_usage_stays_unpriced_without_contract(self) -> None:
        self.assertFalse(
            usage_is_priceable(
                input_tokens=10,
                output_tokens=5,
                native_components=True,
                cached_input_tokens=0,
                input_rate=1.0,
                cached_rate=1.0,
            )
        )

    def test_char_020_missing_cache_split_is_fail_safe(self) -> None:
        self.assertFalse(
            usage_is_priceable(
                input_tokens=10,
                output_tokens=5,
                native_components=False,
                cached_input_tokens=None,
                input_rate=1.0,
                cached_rate=0.5,
            )
        )
        self.assertTrue(
            usage_is_priceable(
                input_tokens=10,
                output_tokens=5,
                native_components=False,
                cached_input_tokens=None,
                input_rate=1.0,
                cached_rate=1.0,
            )
        )

    def test_char_021_reasoning_billing_is_catalog_controlled(self) -> None:
        self.assertEqual(
            billable_output_tokens(
                50,
                40,
                reasoning_billing_mode="INCLUDED_IN_OUTPUT",
            ),
            50,
        )
        self.assertEqual(
            billable_output_tokens(
                50,
                40,
                reasoning_billing_mode="ADD_REASONING_TO_OUTPUT",
            ),
            90,
        )


if __name__ == "__main__":
    unittest.main()
