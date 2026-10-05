from __future__ import annotations

import asyncio
import unittest
from decimal import Decimal
from uuid import uuid7

from orqetia.providers import (
    ORQETIA_TEST_PROVIDER,
    DeterministicTestProvider,
    NativeUsage,
    OutputKind,
    ProviderAttemptRequest,
    ProviderCostMetadata,
    ProviderOutcome,
    ProviderTarget,
    ProviderUsage,
    SimulatorCandidate,
    SimulatorFixture,
    SimulatorFixtureExhausted,
    SimulatorFixtureMismatch,
    SimulatorScenario,
    SimulatorStep,
)


class DeterministicTestProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = ProviderTarget(
            provider_id=ORQETIA_TEST_PROVIDER,
            model_id="sim-small",
            reasoning_profile="standard",
        )

    def test_success_preserves_attempt_identity_and_usage(self) -> None:
        attempt_id = uuid7()
        usage = ProviderUsage(
            input_tokens=12,
            output_tokens=8,
            native=(NativeUsage("compute_units", Decimal("3"), "CU"),),
        )
        cost = ProviderCostMetadata(
            amount=Decimal("0.0012"),
            comparison_group="MONEY:USD",
            currency="USD",
        )
        provider = self._provider(
            SimulatorStep(
                scenario=SimulatorScenario.SUCCESS,
                expected_cycle=1,
                expected_attempt_index=1,
                expected_target=self.target,
                simulated_latency_ms=37,
                usage=usage,
                cost=cost,
            )
        )

        result = asyncio.run(provider.invoke(self._request(attempt_id=attempt_id)))

        self.assertEqual(result.attempt_id, attempt_id)
        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.TEXT)
        self.assertEqual(result.accepted_requirements, ("VALID_JSON", "CITED"))
        self.assertEqual(result.missing_requirements, ())
        self.assertEqual(result.simulated_latency_ms, 37)
        self.assertEqual(result.usage.total_tokens, 20)
        self.assertEqual(result.cost, cost)
        self.assertEqual(provider.invocations[0].attempt_id, attempt_id)
        self.assertEqual(provider.invocations[0].target, self.target)

    def test_structured_success_is_explicit(self) -> None:
        provider = self._provider(
            SimulatorStep(
                scenario=SimulatorScenario.STRUCTURED_SUCCESS,
                expected_cycle=1,
                expected_attempt_index=1,
            )
        )

        result = asyncio.run(provider.invoke(self._request()))

        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.output_kind, OutputKind.STRUCTURED)
        self.assertTrue(result.response_reference.startswith("simulator://"))

    def test_partial_accepts_strict_subset(self) -> None:
        provider = self._provider(
            SimulatorStep(
                scenario=SimulatorScenario.PARTIAL,
                expected_cycle=1,
                expected_attempt_index=1,
                accepted_requirements=("VALID_JSON",),
            )
        )

        result = asyncio.run(provider.invoke(self._request()))

        self.assertEqual(result.outcome, ProviderOutcome.PARTIAL)
        self.assertEqual(result.accepted_requirements, ("VALID_JSON",))
        self.assertEqual(result.missing_requirements, ("CITED",))

    def test_retry_after_is_normalized_without_sleep(self) -> None:
        provider = self._provider(
            SimulatorStep(
                scenario=SimulatorScenario.RETRY_AFTER,
                expected_cycle=2,
                expected_attempt_index=3,
                retry_after_seconds=17,
                simulated_latency_ms=5000,
            )
        )

        result = asyncio.run(
            provider.invoke(
                self._request(
                    cycle=2,
                    attempt_index=3,
                )
            )
        )

        self.assertEqual(result.outcome, ProviderOutcome.RATE_LIMITED)
        self.assertEqual(result.retry_after_seconds, 17)
        self.assertEqual(result.simulated_latency_ms, 5000)
        self.assertEqual(len(provider.invocations), 1)

    def test_all_failure_scenarios_have_deterministic_outcomes(self) -> None:
        cases = {
            SimulatorScenario.REQUIREMENT_NOT_SATISFIED: (
                ProviderOutcome.REQUIREMENT_NOT_SATISFIED
            ),
            SimulatorScenario.TRANSIENT_ERROR: ProviderOutcome.TRANSIENT_ERROR,
            SimulatorScenario.TERMINAL_ERROR: ProviderOutcome.TERMINAL_ERROR,
            SimulatorScenario.RATE_LIMIT: ProviderOutcome.RATE_LIMITED,
            SimulatorScenario.TIMEOUT: ProviderOutcome.TIMEOUT,
            SimulatorScenario.CREDIT_EXHAUSTED: ProviderOutcome.CREDIT_EXHAUSTED,
            SimulatorScenario.QUOTA: ProviderOutcome.QUOTA_EXHAUSTED,
            SimulatorScenario.AUTH_FAILURE: ProviderOutcome.AUTH_FAILURE,
            SimulatorScenario.MALFORMED_OUTPUT: ProviderOutcome.MALFORMED_OUTPUT,
            SimulatorScenario.UNAVAILABLE: ProviderOutcome.UNAVAILABLE,
        }
        for scenario, expected in cases.items():
            with self.subTest(scenario=scenario):
                provider = self._provider(
                    SimulatorStep(
                        scenario=scenario,
                        expected_cycle=1,
                        expected_attempt_index=1,
                    )
                )
                result = asyncio.run(provider.invoke(self._request()))
                self.assertEqual(result.outcome, expected)
                self.assertEqual(result.accepted_requirements, ())
                self.assertEqual(
                    result.missing_requirements,
                    ("VALID_JSON", "CITED"),
                )
                if scenario is SimulatorScenario.MALFORMED_OUTPUT:
                    self.assertEqual(result.output_kind, OutputKind.MALFORMED)
                else:
                    self.assertEqual(result.output_kind, OutputKind.NONE)

    def test_fixture_sequence_checks_cycle_attempt_and_target(self) -> None:
        other = ProviderTarget("OTHER_TEST_PROVIDER", "model-b", "deep")
        provider = DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="sequence",
                seed="fixed-seed",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=SimulatorScenario.TRANSIENT_ERROR,
                        expected_cycle=1,
                        expected_attempt_index=1,
                        expected_target=self.target,
                    ),
                    SimulatorStep(
                        scenario=SimulatorScenario.SUCCESS,
                        expected_cycle=2,
                        expected_attempt_index=2,
                        expected_target=other,
                    ),
                ),
            )
        )

        asyncio.run(provider.invoke(self._request()))
        with self.assertRaisesRegex(SimulatorFixtureMismatch, "expected cycle 2"):
            asyncio.run(provider.invoke(self._request(cycle=1, attempt_index=2)))

        second = asyncio.run(
            provider.invoke(
                self._request(
                    cycle=2,
                    attempt_index=2,
                    target=other,
                )
            )
        )
        self.assertEqual(second.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(len(provider.invocations), 2)

        with self.assertRaises(SimulatorFixtureExhausted):
            asyncio.run(
                provider.invoke(
                    self._request(
                        cycle=3,
                        attempt_index=3,
                        target=other,
                    )
                )
            )

    def test_candidates_preserve_priced_and_unpriced_metadata(self) -> None:
        priced = SimulatorCandidate(
            target=self.target,
            comparison_group="MONEY:USD",
            comparison_group_rank=0,
            estimated_cost=Decimal("0.002"),
            currency="USD",
            policy_rank=10,
        )
        unpriced = SimulatorCandidate(
            target=ProviderTarget("UNPRICED_TEST", "model-u", "standard"),
            comparison_group="UNPRICED",
            comparison_group_rank=2,
            estimated_cost=None,
            policy_rank=20,
        )
        fixture = SimulatorFixture(
            fixture_id="cost-ladder",
            seed="cost-seed",
            candidates=(priced, unpriced),
            steps=(
                SimulatorStep(
                    scenario=SimulatorScenario.SUCCESS,
                    expected_cycle=1,
                    expected_attempt_index=1,
                ),
            ),
        )

        self.assertEqual(fixture.candidates[0].estimated_cost, Decimal("0.002"))
        self.assertIsNone(fixture.candidates[1].estimated_cost)
        self.assertEqual(fixture.candidates[1].comparison_group, "UNPRICED")

    def test_explicit_target_retries_execute_exact_target_received(self) -> None:
        provider = DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="explicit-retries",
                seed="no-randomness",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=SimulatorScenario.TRANSIENT_ERROR,
                        expected_cycle=1,
                        expected_attempt_index=1,
                        expected_target=self.target,
                    ),
                    SimulatorStep(
                        scenario=SimulatorScenario.SUCCESS,
                        expected_cycle=2,
                        expected_attempt_index=2,
                        expected_target=self.target,
                    ),
                ),
            )
        )

        first_id = uuid7()
        second_id = uuid7()
        asyncio.run(provider.invoke(self._request(attempt_id=first_id)))
        asyncio.run(
            provider.invoke(
                self._request(
                    attempt_id=second_id,
                    cycle=2,
                    attempt_index=2,
                )
            )
        )

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(
            tuple(item.target for item in provider.invocations),
            (self.target, self.target),
        )

    def test_fixture_requires_explicit_retry_after_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires retry_after_seconds"):
            SimulatorStep(
                scenario=SimulatorScenario.RETRY_AFTER,
                expected_cycle=1,
                expected_attempt_index=1,
            )

    def _provider(self, step: SimulatorStep) -> DeterministicTestProvider:
        return DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="unit-fixture",
                seed="unit-seed",
                candidates=(),
                steps=(step,),
            )
        )

    def _request(
        self,
        *,
        attempt_id=None,
        cycle: int = 1,
        attempt_index: int = 1,
        target: ProviderTarget | None = None,
    ) -> ProviderAttemptRequest:
        return ProviderAttemptRequest(
            attempt_id=attempt_id or uuid7(),
            operation="TASK_EXECUTION",
            target=target or self.target,
            cycle=cycle,
            attempt_index=attempt_index,
            request_reference="payload://request/test",
            request_fingerprint="a" * 64,
            missing_requirements=("VALID_JSON", "CITED"),
            task_id=uuid7(),
            session_id=uuid7(),
        )


if __name__ == "__main__":
    unittest.main()
