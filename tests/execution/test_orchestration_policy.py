from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

from orqetia.execution import (
    AttemptObservation,
    AttemptObservationStatus,
    CanonicalOrchestrationPolicyEngine,
    ExecutionMode,
    ExecutionSession,
    ExecutionTargetSnapshot,
    ExecutionTask,
    OrchestrationCandidate,
    OrchestrationCyclePlan,
    OrchestrationDecision,
    OrchestrationDisposition,
    OrchestrationPolicy,
    OwnershipScope,
    QuarantineState,
    RequestedTargetSnapshot,
    SessionPolicySnapshot,
    SessionStatus,
    TargetHealth,
    TargetRuntimeState,
    TaskPayloadReferences,
    TaskStatus,
)
from orqetia.providers import (
    DeterministicTestProvider,
    ProviderAttemptRequest,
    ProviderOutcome,
    ProviderTarget,
    SimulatorFixture,
    SimulatorScenario,
    SimulatorStep,
)


class CanonicalOrchestrationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(UTC)
        self.scope = OwnershipScope(uuid7(), uuid7())
        self.session_id = uuid7()
        self.policy_id = uuid7()
        self.cheap = ExecutionTargetSnapshot("CHEAP", "small", "standard")
        self.expensive = ExecutionTargetSnapshot("EXPENSIVE", "large", "deep")
        self.unpriced = ExecutionTargetSnapshot("UNPRICED", "special", "standard")
        self.engine = CanonicalOrchestrationPolicyEngine()
        self.policy = OrchestrationPolicy(
            max_cycles=3,
            max_attempts=8,
            cycle_delay_seconds=5,
            retry_after_cap_seconds=30,
        )

    def test_auto_orders_lower_comparable_cost_first_and_keeps_unpriced_eligible(self) -> None:
        task = self._task()
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(
                self._candidate(self.unpriced, amount=None, group="UNPRICED", group_rank=1),
                self._candidate(self.expensive, amount="0.30", group_rank=0),
                self._candidate(self.cheap, amount="0.10", group_rank=0),
            ),
        )

        self.assertEqual(
            plan.candidate_order,
            (self.cheap, self.expensive, self.unpriced),
        )

    def test_auto_does_not_invent_fx_between_comparison_groups(self) -> None:
        task = self._task()
        session = self._session()
        eur_low_numeric = self._candidate(
            self.cheap,
            amount="0.01",
            group="MONEY:EUR",
            group_rank=1,
            currency="EUR",
        )
        usd_high_numeric = self._candidate(
            self.expensive,
            amount="9.00",
            group="MONEY:USD",
            group_rank=0,
            currency="USD",
        )

        plan = self._plan(
            task=task,
            session=session,
            candidates=(eur_low_numeric, usd_high_numeric),
        )

        self.assertEqual(plan.candidate_order, (self.expensive, self.cheap))

    def test_auto_failure_escalates_and_cheap_success_would_stop_expensive(self) -> None:
        asyncio.run(self._test_auto_failure_escalates())

    async def _test_auto_failure_escalates(self) -> None:
        task = self._task(requirements=("VALID_JSON",))
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(
                self._candidate(self.expensive, amount="0.20"),
                self._candidate(self.cheap, amount="0.01"),
            ),
        )
        provider = DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="auto-escalation",
                seed="fixed",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=SimulatorScenario.REQUIREMENT_NOT_SATISFIED,
                        expected_cycle=1,
                        expected_attempt_index=1,
                        expected_target=self._provider_target(self.cheap),
                    ),
                    SimulatorStep(
                        scenario=SimulatorScenario.SUCCESS,
                        expected_cycle=1,
                        expected_attempt_index=2,
                        expected_target=self._provider_target(self.expensive),
                    ),
                ),
            )
        )

        first = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(),
            attempts_used_before_cycle=0,
        )
        self.assertEqual(first.disposition, OrchestrationDisposition.DISPATCH)
        self.assertEqual(first.next_target, self.cheap)

        first_result = await provider.invoke(self._request(first))
        after_first = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(
                AttemptObservation.from_result(target=self.cheap, result=first_result),
            ),
            attempts_used_before_cycle=0,
        )
        self.assertEqual(after_first.disposition, OrchestrationDisposition.DISPATCH)
        self.assertEqual(after_first.next_target, self.expensive)
        self.assertEqual(after_first.progress.missing, ("VALID_JSON",))

        second_result = await provider.invoke(self._request(after_first))
        complete = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(
                AttemptObservation.from_result(target=self.cheap, result=first_result),
                AttemptObservation.from_result(
                    target=self.expensive,
                    result=second_result,
                ),
            ),
            attempts_used_before_cycle=0,
        )
        self.assertEqual(complete.disposition, OrchestrationDisposition.COMPLETE)
        self.assertEqual(complete.progress.accepted, ("VALID_JSON",))
        self.assertEqual(complete.progress.missing, ())
        self.assertEqual(len(provider.invocations), 2)

    def test_auto_cheap_success_prevents_expensive_dispatch(self) -> None:
        asyncio.run(self._test_cheap_success_stops())

    async def _test_cheap_success_stops(self) -> None:
        task = self._task(requirements=("VALID_JSON",))
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(
                self._candidate(self.expensive, amount="0.20"),
                self._candidate(self.cheap, amount="0.01"),
            ),
        )
        provider = DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="cheap-complete",
                seed="fixed",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=SimulatorScenario.SUCCESS,
                        expected_cycle=1,
                        expected_attempt_index=1,
                        expected_target=self._provider_target(self.cheap),
                    ),
                ),
            )
        )

        dispatch = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(),
            attempts_used_before_cycle=0,
        )
        result = await provider.invoke(self._request(dispatch))
        complete = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(AttemptObservation.from_result(target=self.cheap, result=result),),
            attempts_used_before_cycle=0,
        )

        self.assertEqual(complete.disposition, OrchestrationDisposition.COMPLETE)
        self.assertEqual(len(provider.invocations), 1)

    def test_partial_progress_preserves_accepted_and_only_requests_missing(self) -> None:
        asyncio.run(self._test_partial_progress())

    async def _test_partial_progress(self) -> None:
        task = self._task(requirements=("VALID_JSON", "CITED"))
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(
                self._candidate(self.cheap, amount="0.01"),
                self._candidate(self.expensive, amount="0.20"),
            ),
        )
        provider = DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="partial-escalation",
                seed="fixed",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=SimulatorScenario.PARTIAL,
                        expected_cycle=1,
                        expected_attempt_index=1,
                        expected_target=self._provider_target(self.cheap),
                        accepted_requirements=("VALID_JSON",),
                    ),
                    SimulatorStep(
                        scenario=SimulatorScenario.SUCCESS,
                        expected_cycle=1,
                        expected_attempt_index=2,
                        expected_target=self._provider_target(self.expensive),
                    ),
                ),
            )
        )

        first = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(),
            attempts_used_before_cycle=0,
        )
        first_result = await provider.invoke(self._request(first))
        after_first = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(
                AttemptObservation.from_result(target=self.cheap, result=first_result),
            ),
            attempts_used_before_cycle=0,
        )

        self.assertEqual(after_first.progress.accepted, ("VALID_JSON",))
        self.assertEqual(after_first.progress.missing, ("CITED",))
        self.assertEqual(after_first.next_target, self.expensive)

        second_result = await provider.invoke(self._request(after_first))
        complete = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(
                AttemptObservation.from_result(target=self.cheap, result=first_result),
                AttemptObservation.from_result(
                    target=self.expensive,
                    result=second_result,
                ),
            ),
            attempts_used_before_cycle=0,
        )
        self.assertEqual(complete.disposition, OrchestrationDisposition.COMPLETE)
        self.assertEqual(complete.progress.accepted, ("VALID_JSON", "CITED"))

    def test_new_cycle_re_evaluates_auto_cost_order(self) -> None:
        session = self._session()
        first_task = self._task()
        first = self._plan(
            task=first_task,
            session=session,
            candidates=(
                self._candidate(self.cheap, amount="0.01"),
                self._candidate(self.expensive, amount="0.20"),
            ),
        )
        self.assertEqual(first.candidate_order[0], self.cheap)

        second_task = replace(first_task, current_cycle=1, version=2)
        second = self._plan(
            task=second_task,
            session=session,
            candidates=(
                self._candidate(self.cheap, amount="0.50"),
                self._candidate(self.expensive, amount="0.02"),
            ),
        )
        self.assertEqual(second.candidate_order[0], self.expensive)

    def test_explicit_target_ignores_cheaper_candidates_and_retries_same_target(self) -> None:
        asyncio.run(self._test_explicit_retry())

    async def _test_explicit_retry(self) -> None:
        task = self._task(
            mode=ExecutionMode.EXPLICIT_TARGET,
            explicit_target=self.expensive,
            requirements=("VALID_JSON",),
        )
        session = self._session()
        candidates = (
            self._candidate(self.cheap, amount="0.0001"),
            self._candidate(self.expensive, amount="9.00"),
        )
        first_plan = self._plan(task=task, session=session, candidates=candidates)
        self.assertEqual(first_plan.candidate_order, (self.expensive,))

        provider = DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="explicit-retry",
                seed="fixed",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=SimulatorScenario.RETRY_AFTER,
                        expected_cycle=1,
                        expected_attempt_index=1,
                        expected_target=self._provider_target(self.expensive),
                        retry_after_seconds=17,
                    ),
                    SimulatorStep(
                        scenario=SimulatorScenario.SUCCESS,
                        expected_cycle=2,
                        expected_attempt_index=2,
                        expected_target=self._provider_target(self.expensive),
                    ),
                ),
            )
        )

        first_dispatch = self.engine.evaluate_cycle(
            plan=first_plan,
            policy=self.policy,
            observations=(),
            attempts_used_before_cycle=0,
        )
        first_result = await provider.invoke(self._request(first_dispatch))
        next_cycle = self.engine.evaluate_cycle(
            plan=first_plan,
            policy=self.policy,
            observations=(
                AttemptObservation.from_result(
                    target=self.expensive,
                    result=first_result,
                ),
            ),
            attempts_used_before_cycle=0,
        )
        self.assertEqual(next_cycle.disposition, OrchestrationDisposition.NEXT_CYCLE)
        self.assertEqual(next_cycle.delay_seconds, 17)

        second_task = replace(task, current_cycle=1, version=2)
        second_plan_raw = self.engine.plan_next_cycle(
            task=second_task,
            session=session,
            policy=self.policy,
            candidates=(
                self._candidate(self.cheap, amount="100.00"),
                self._candidate(self.expensive, amount="0.00001"),
            ),
            now=self.now,
            prior_retry_after_seconds=(first_result.retry_after_seconds,),
        )
        self.assertIsInstance(second_plan_raw, OrchestrationCyclePlan)
        assert isinstance(second_plan_raw, OrchestrationCyclePlan)
        self.assertEqual(second_plan_raw.candidate_order, (self.expensive,))
        self.assertEqual(second_plan_raw.delay_seconds, 17)

        second_dispatch = self.engine.evaluate_cycle(
            plan=second_plan_raw,
            policy=self.policy,
            observations=(),
            attempts_used_before_cycle=1,
        )
        second_result = await provider.invoke(self._request(second_dispatch))
        complete = self.engine.evaluate_cycle(
            plan=second_plan_raw,
            policy=self.policy,
            observations=(
                AttemptObservation.from_result(
                    target=self.expensive,
                    result=second_result,
                ),
            ),
            attempts_used_before_cycle=1,
        )
        self.assertEqual(complete.disposition, OrchestrationDisposition.COMPLETE)
        self.assertEqual(
            tuple(invocation.target for invocation in provider.invocations),
            (
                self._provider_target(self.expensive),
                self._provider_target(self.expensive),
            ),
        )

    def test_explicit_terminal_failure_never_falls_back_cross_target(self) -> None:
        asyncio.run(self._test_explicit_terminal())

    async def _test_explicit_terminal(self) -> None:
        task = self._task(
            mode=ExecutionMode.EXPLICIT_TARGET,
            explicit_target=self.cheap,
            requirements=("VALID_JSON",),
        )
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(
                self._candidate(self.expensive, amount="0.001"),
                self._candidate(self.cheap, amount="5.00"),
            ),
        )
        provider = DeterministicTestProvider(
            SimulatorFixture(
                fixture_id="explicit-terminal",
                seed="fixed",
                candidates=(),
                steps=(
                    SimulatorStep(
                        scenario=SimulatorScenario.AUTH_FAILURE,
                        expected_cycle=1,
                        expected_attempt_index=1,
                        expected_target=self._provider_target(self.cheap),
                    ),
                ),
            )
        )

        dispatch = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(),
            attempts_used_before_cycle=0,
        )
        result = await provider.invoke(self._request(dispatch))
        terminal = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(AttemptObservation.from_result(target=self.cheap, result=result),),
            attempts_used_before_cycle=0,
        )

        self.assertEqual(terminal.disposition, OrchestrationDisposition.UNAVAILABLE)
        self.assertEqual(terminal.reason_code, "EXPLICIT_TARGET_TERMINAL_FAILURE")
        self.assertEqual(terminal.terminal_quarantine_targets, (self.cheap,))
        self.assertEqual(len(provider.invocations), 1)

    def test_terminal_auto_failure_quarantines_target_and_can_continue(self) -> None:
        task = self._task(requirements=("VALID_JSON",))
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(
                self._candidate(self.cheap, amount="0.01"),
                self._candidate(self.expensive, amount="0.20"),
            ),
        )
        observation = AttemptObservation(
            attempt_id=uuid7(),
            target=self.cheap,
            status=AttemptObservationStatus.COMPLETED,
            outcome=ProviderOutcome.AUTH_FAILURE,
            accepted_requirements=(),
            missing_requirements=("VALID_JSON",),
            error_class="AUTH_FAILURE",
        )

        decision = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(observation,),
            attempts_used_before_cycle=0,
        )

        self.assertEqual(decision.disposition, OrchestrationDisposition.DISPATCH)
        self.assertEqual(decision.next_target, self.expensive)
        self.assertEqual(decision.terminal_quarantine_targets, (self.cheap,))

    def test_internal_client_quota_does_not_quarantine_provider(self) -> None:
        task = self._task(requirements=("VALID_JSON",))
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(
                self._candidate(self.cheap, amount="0.01"),
                self._candidate(self.expensive, amount="0.20"),
            ),
        )
        observation = AttemptObservation(
            attempt_id=uuid7(),
            target=self.cheap,
            status=AttemptObservationStatus.COMPLETED,
            outcome=ProviderOutcome.QUOTA_EXHAUSTED,
            accepted_requirements=(),
            missing_requirements=("VALID_JSON",),
            error_class="ORQETIA_CLIENT_QUOTA_HARD_LIMIT_EXCEEDED",
        )

        decision = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(observation,),
            attempts_used_before_cycle=0,
        )

        self.assertEqual(decision.disposition, OrchestrationDisposition.DISPATCH)
        self.assertEqual(decision.next_target, self.expensive)
        self.assertEqual(decision.terminal_quarantine_targets, ())

    def test_session_health_and_quarantine_filter_candidates(self) -> None:
        task = self._task()
        session = self._session(
            runtime=(
                TargetRuntimeState(
                    target=self.cheap,
                    health=TargetHealth.UNAVAILABLE,
                    quarantine=QuarantineState.NONE,
                    updated_at=self.now,
                ),
                TargetRuntimeState(
                    target=self.expensive,
                    health=TargetHealth.HEALTHY,
                    quarantine=QuarantineState.TERMINAL,
                    updated_at=self.now,
                    quarantine_reason_code="AUTH_FAILURE",
                ),
            )
        )

        result = self.engine.plan_next_cycle(
            task=task,
            session=session,
            policy=self.policy,
            candidates=(
                self._candidate(self.cheap, amount="0.01"),
                self._candidate(self.expensive, amount="0.02"),
                self._candidate(self.unpriced, amount=None, group="UNPRICED", group_rank=2),
            ),
            now=self.now,
        )

        self.assertIsInstance(result, OrchestrationCyclePlan)
        assert isinstance(result, OrchestrationCyclePlan)
        self.assertEqual(result.candidate_order, (self.unpriced,))

    def test_retry_after_is_bounded_and_combined_with_cycle_delay(self) -> None:
        policy = OrchestrationPolicy(
            max_cycles=2,
            max_attempts=2,
            cycle_delay_seconds=10,
            retry_after_cap_seconds=30,
        )
        task = self._task(requirements=("VALID_JSON",))
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(self._candidate(self.cheap, amount="0.01"),),
            policy=policy,
        )
        observation = AttemptObservation(
            attempt_id=uuid7(),
            target=self.cheap,
            status=AttemptObservationStatus.COMPLETED,
            outcome=ProviderOutcome.RATE_LIMITED,
            accepted_requirements=(),
            missing_requirements=("VALID_JSON",),
            retry_after_seconds=999,
        )

        decision = self.engine.evaluate_cycle(
            plan=plan,
            policy=policy,
            observations=(observation,),
            attempts_used_before_cycle=0,
        )

        self.assertEqual(decision.disposition, OrchestrationDisposition.NEXT_CYCLE)
        self.assertEqual(decision.delay_seconds, 30)

    def test_attempt_budget_terminalizes_partial_progress(self) -> None:
        policy = OrchestrationPolicy(max_cycles=3, max_attempts=1)
        task = self._task(requirements=("VALID_JSON", "CITED"))
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(self._candidate(self.cheap, amount="0.01"),),
            policy=policy,
        )
        observation = AttemptObservation(
            attempt_id=uuid7(),
            target=self.cheap,
            status=AttemptObservationStatus.COMPLETED,
            outcome=ProviderOutcome.PARTIAL,
            accepted_requirements=("VALID_JSON",),
            missing_requirements=("CITED",),
            response_reference="simulator://partial",
        )

        decision = self.engine.evaluate_cycle(
            plan=plan,
            policy=policy,
            observations=(observation,),
            attempts_used_before_cycle=0,
        )

        self.assertEqual(decision.disposition, OrchestrationDisposition.PARTIAL)
        self.assertEqual(decision.reason_code, "MAX_ATTEMPTS_EXHAUSTED")
        self.assertEqual(decision.progress.accepted, ("VALID_JSON",))
        self.assertEqual(decision.progress.missing, ("CITED",))

    def test_ambiguous_attempt_never_redispatches(self) -> None:
        task = self._task()
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(
                self._candidate(self.cheap, amount="0.01"),
                self._candidate(self.expensive, amount="0.02"),
            ),
        )
        ambiguous = AttemptObservation(
            attempt_id=uuid7(),
            target=self.cheap,
            status=AttemptObservationStatus.AMBIGUOUS,
            error_class="DISPATCH_RESULT_UNKNOWN_AFTER_RECLAIM",
        )

        decision = self.engine.evaluate_cycle(
            plan=plan,
            policy=self.policy,
            observations=(ambiguous,),
            attempts_used_before_cycle=0,
        )

        self.assertEqual(decision.disposition, OrchestrationDisposition.UNAVAILABLE)
        self.assertEqual(decision.reason_code, "AMBIGUOUS_PROVIDER_EFFECT")

    def test_cancelling_task_is_never_planned_for_dispatch(self) -> None:
        task = replace(self._task(), status=TaskStatus.CANCELLING)
        session = self._session()

        result = self.engine.plan_next_cycle(
            task=task,
            session=session,
            policy=self.policy,
            candidates=(self._candidate(self.cheap, amount="0.01"),),
            now=self.now,
        )

        self.assertIsInstance(result, OrchestrationDecision)
        assert isinstance(result, OrchestrationDecision)
        self.assertEqual(result.disposition, OrchestrationDisposition.CANCELLED)

    def test_deadline_prevents_new_provider_attempt(self) -> None:
        task = self._task()
        session = self._session()
        policy = OrchestrationPolicy(
            max_cycles=3,
            max_attempts=3,
            deadline_at=self.now - timedelta(seconds=1),
        )

        result = self.engine.plan_next_cycle(
            task=task,
            session=session,
            policy=policy,
            candidates=(self._candidate(self.cheap, amount="0.01"),),
            now=self.now,
        )

        self.assertIsInstance(result, OrchestrationDecision)
        assert isinstance(result, OrchestrationDecision)
        self.assertEqual(result.disposition, OrchestrationDisposition.UNAVAILABLE)
        self.assertEqual(result.reason_code, "ORCHESTRATION_DEADLINE_EXHAUSTED")

    def test_cycle_plan_materializes_existing_durable_ledger_contract(self) -> None:
        task = self._task()
        session = self._session()
        plan = self._plan(
            task=task,
            session=session,
            candidates=(self._candidate(self.cheap, amount="0.01"),),
        )

        decision = plan.as_task_cycle_decision(recorded_at=self.now)

        self.assertEqual(decision.cycle_index, 1)
        self.assertEqual(decision.candidate_order, (self.cheap,))
        self.assertEqual(decision.accepted_snapshot, ())
        self.assertEqual(decision.missing_snapshot, task.requirements)

    def _plan(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        candidates: tuple[OrchestrationCandidate, ...],
        policy: OrchestrationPolicy | None = None,
    ) -> OrchestrationCyclePlan:
        result = self.engine.plan_next_cycle(
            task=task,
            session=session,
            policy=policy or self.policy,
            candidates=candidates,
            now=self.now,
        )
        self.assertIsInstance(result, OrchestrationCyclePlan)
        assert isinstance(result, OrchestrationCyclePlan)
        return result

    def _session(
        self,
        *,
        runtime: tuple[TargetRuntimeState, ...] = (),
    ) -> ExecutionSession:
        return ExecutionSession(
            session_id=self.session_id,
            ownership=self.scope,
            status=SessionStatus.ACTIVE,
            policy=SessionPolicySnapshot(
                effective_policy_version_id=self.policy_id,
                authorized_targets=(self.cheap, self.expensive, self.unpriced),
            ),
            target_runtime=runtime,
            created_at=self.now,
            updated_at=self.now,
        )

    def _task(
        self,
        *,
        mode: ExecutionMode = ExecutionMode.AUTO,
        explicit_target: ExecutionTargetSnapshot | None = None,
        requirements: tuple[str, ...] = ("VALID_JSON", "CITED"),
    ) -> ExecutionTask:
        requested = (
            RequestedTargetSnapshot(explicit_target.provider_id)
            if explicit_target is not None
            else None
        )
        return ExecutionTask(
            task_id=uuid7(),
            session_id=self.session_id,
            ownership=self.scope,
            operation="TASK_EXECUTION",
            status=TaskStatus.RUNNING,
            effective_policy_version_id=self.policy_id,
            requested_execution_mode=mode,
            requested_target=requested,
            effective_target=explicit_target,
            requirements=requirements,
            accepted_requirements=(),
            missing_requirements=requirements,
            payloads=TaskPayloadReferences(
                input_reference="payload://input/orchestration",
                input_fingerprint="a" * 64,
            ),
            created_at=self.now,
            updated_at=self.now,
            started_at=self.now,
            version=3,
        )

    @staticmethod
    def _candidate(
        target: ExecutionTargetSnapshot,
        *,
        amount: str | None,
        group: str = "MONEY:USD",
        group_rank: int = 0,
        currency: str | None = "USD",
        policy_rank: int = 0,
    ) -> OrchestrationCandidate:
        if amount is None:
            currency = None
        return OrchestrationCandidate(
            target=target,
            comparison_group=group,
            comparison_group_rank=group_rank,
            estimated_cost=None if amount is None else Decimal(amount),
            currency=currency,
            policy_rank=policy_rank,
        )

    @staticmethod
    def _provider_target(target: ExecutionTargetSnapshot) -> ProviderTarget:
        return ProviderTarget(
            provider_id=target.provider_id,
            model_id=target.model_id,
            reasoning_profile=target.reasoning_profile,
        )

    @staticmethod
    def _request(decision: OrchestrationDecision) -> ProviderAttemptRequest:
        assert decision.next_target is not None
        assert decision.attempt_index is not None
        return ProviderAttemptRequest(
            attempt_id=uuid7(),
            operation="TASK_EXECUTION",
            target=CanonicalOrchestrationPolicyTests._provider_target(decision.next_target),
            cycle=decision.cycle_index,
            attempt_index=decision.attempt_index,
            request_reference="payload://request/orchestration",
            request_fingerprint="b" * 64,
            missing_requirements=decision.progress.missing,
            task_id=uuid7(),
            session_id=uuid7(),
        )

if __name__ == "__main__":
    unittest.main()
