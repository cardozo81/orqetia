from __future__ import annotations

import asyncio
import io
import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid7

from orqetia.execution import (
    DispatchAction,
    DispatchClaim,
    ExecutionTargetSnapshot,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
)
from orqetia.infrastructure.processes.provider_attempts import ProviderAttemptHandler
from orqetia.observability import (
    METRIC_CONTRACTS,
    REDACTED,
    SPAN_CONTRACTS,
    JsonEventEmitter,
    work_telemetry_fields,
)
from orqetia.providers import (
    OutputKind,
    ProviderAttemptResult,
    ProviderOutcome,
    ProviderTarget,
)
from orqetia.settings import RuntimeSettings
from orqetia.shared.messaging import DataClassification, QueueName, WorkLease


class FakeAttemptStore:
    def __init__(self, attempt: ProviderAttempt) -> None:
        self._attempt = attempt
        self.completed = False

    async def get_owned(
        self,
        *,
        scope: OwnershipScope,
        attempt_id,
    ) -> ProviderAttempt | None:
        if scope != self._attempt.ownership or attempt_id != self._attempt.attempt_id:
            return None
        return self._attempt

    async def claim_dispatch(
        self,
        *,
        scope: OwnershipScope,
        attempt_id,
        work_id,
        occurred_at: datetime,
    ) -> DispatchClaim:
        if scope != self._attempt.ownership or attempt_id != self._attempt.attempt_id:
            raise AssertionError("unexpected ownership/attempt")
        journal = replace(
            self._attempt,
            status=ProviderAttemptStatus.DISPATCHING,
            dispatch_work_id=work_id,
            dispatch_started_at=occurred_at,
            updated_at=occurred_at,
            version=2,
        )
        return DispatchClaim(DispatchAction.DISPATCH, journal)

    async def complete_dispatch(
        self,
        *,
        scope: OwnershipScope,
        attempt_id,
        work_id,
        result: ProviderAttemptResult,
        occurred_at: datetime,
    ) -> bool:
        del scope, attempt_id, work_id, result, occurred_at
        self.completed = True
        return True

    async def mark_ambiguous(
        self,
        *,
        scope: OwnershipScope,
        attempt_id,
        work_id,
        occurred_at: datetime,
        error_class: str,
    ) -> bool:
        del scope, attempt_id, work_id, occurred_at, error_class
        return True


class FakeProvider:
    def __init__(self) -> None:
        self.invocations = []

    async def invoke(self, request):
        self.invocations.append(request)
        return ProviderAttemptResult(
            attempt_id=request.attempt_id,
            outcome=ProviderOutcome.SUCCESS,
            output_kind=OutputKind.TEXT,
            accepted_requirements=("VALID_JSON",),
            missing_requirements=(),
            simulated_latency_ms=17,
        )


class TelemetryContractTests(unittest.TestCase):
    def test_json_event_redacts_secrets_payload_and_financial_values(self) -> None:
        stream = io.StringIO()
        emitter = JsonEventEmitter(stream)

        emitter.emit(
            "test.safe",
            {
                "api_key": "provider-secret",
                "prompt": "private prompt",
                "payload": {"raw_output": "private output"},
                "provider_cost": "12.34",
                "currency": "USD",
                "input_tokens": 21,
                "internal_cost_reference_id": uuid7(),
            },
        )

        record = json.loads(stream.getvalue())
        self.assertEqual(record["api_key"], REDACTED)
        self.assertEqual(record["prompt"], REDACTED)
        self.assertEqual(record["payload"], REDACTED)
        self.assertEqual(record["provider_cost"], REDACTED)
        self.assertEqual(record["currency"], REDACTED)
        self.assertEqual(record["input_tokens"], 21)
        self.assertNotEqual(record["internal_cost_reference_id"], REDACTED)

    def test_work_fields_exclude_inline_payload_and_keep_correlation(self) -> None:
        lease = self._lease()
        fields = work_telemetry_fields(lease)

        self.assertNotIn("payload", fields)
        self.assertEqual(fields["tenant_id"], lease.tenant_id)
        self.assertEqual(fields["client_id"], lease.client_id)
        self.assertEqual(fields["correlation_id"], lease.correlation_id)
        self.assertEqual(fields["trace_id"], lease.trace_id)
        self.assertEqual(fields["work_attempt_count"], 2)

    def test_metric_contracts_exclude_high_cardinality_identity_labels(self) -> None:
        forbidden = {
            "tenant_id",
            "client_id",
            "session_id",
            "task_id",
            "attempt_id",
            "work_id",
            "provider_id",
            "model_id",
            "trace_id",
            "correlation_id",
        }
        for metric in METRIC_CONTRACTS:
            with self.subTest(metric=metric.name):
                self.assertFalse(forbidden.intersection(metric.labels))

    def test_span_contracts_never_include_payload_or_secret_fields(self) -> None:
        forbidden = {"payload", "prompt", "raw_input", "raw_output", "api_key", "secret"}
        for span in SPAN_CONTRACTS:
            with self.subTest(span=span.name):
                self.assertFalse(forbidden.intersection(span.attributes))

    def test_payload_logging_setting_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload logging is disabled"):
            RuntimeSettings(
                database_dsn="postgresql+psycopg://user:pass@localhost/orqetia",
                telemetry_log_payloads=True,
            )

    def test_provider_dispatch_propagates_work_correlation_to_adapter_and_logs(self) -> None:
        asyncio.run(self._test_provider_dispatch_correlation())

    async def _test_provider_dispatch_correlation(self) -> None:
        now = datetime.now(UTC)
        ownership = OwnershipScope(uuid7(), uuid7())
        attempt = ProviderAttempt(
            attempt_id=uuid7(),
            ownership=ownership,
            operation="TASK_EXECUTION",
            target=ExecutionTargetSnapshot(
                "ORQETIA_TEST_PROVIDER",
                "sim-small",
                "standard",
            ),
            cycle=2,
            attempt_index=3,
            request_reference="payload://request/observability",
            request_fingerprint="a" * 64,
            status=ProviderAttemptStatus.PREPARED,
            created_at=now,
            updated_at=now,
            session_id=uuid7(),
            task_id=uuid7(),
            missing_requirements=("VALID_JSON",),
        )
        lease = self._lease(
            attempt_id=attempt.attempt_id,
            tenant_id=ownership.tenant_id,
            client_id=ownership.client_id,
        )
        store = FakeAttemptStore(attempt)
        provider = FakeProvider()
        stream = io.StringIO()
        emitter = JsonEventEmitter(stream)
        handler = ProviderAttemptHandler(
            store=store,
            resolve_adapter=lambda target: self._resolve(provider, target),
            telemetry=emitter,
        )

        outcome = await handler(lease)

        self.assertEqual(outcome.disposition.value, "COMPLETE")
        self.assertTrue(store.completed)
        self.assertEqual(len(provider.invocations), 1)
        request = provider.invocations[0]
        self.assertEqual(request.correlation_id, lease.correlation_id)
        self.assertEqual(request.trace_id, lease.trace_id)

        records = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(
            [record["event"] for record in records],
            ["provider.dispatch_started", "provider.dispatch_completed"],
        )
        completed = records[-1]
        self.assertEqual(completed["attempt_id"], str(attempt.attempt_id))
        self.assertEqual(completed["session_id"], str(attempt.session_id))
        self.assertEqual(completed["task_id"], str(attempt.task_id))
        self.assertEqual(completed["correlation_id"], str(lease.correlation_id))
        self.assertEqual(completed["trace_id"], lease.trace_id)
        self.assertEqual(completed["cycle_index"], 2)
        self.assertEqual(completed["attempt_index"], 3)
        self.assertEqual(completed["provider_outcome"], ProviderOutcome.SUCCESS.value)
        self.assertNotIn("payload", completed)
        self.assertNotIn("provider_cost", completed)

    @staticmethod
    def _resolve(provider: FakeProvider, target: ProviderTarget) -> FakeProvider:
        if target.provider_id != "ORQETIA_TEST_PROVIDER":
            raise LookupError("unexpected provider target")
        return provider

    @staticmethod
    def _lease(
        *,
        attempt_id=None,
        tenant_id=None,
        client_id=None,
    ) -> WorkLease:
        now = datetime.now(UTC)
        resolved_attempt_id = attempt_id or uuid7()
        return WorkLease(
            work_id=uuid7(),
            queue_name=QueueName.EXECUTION,
            operation_type="provider.attempt.dispatch",
            operation_version=1,
            payload={
                "attempt_id": str(resolved_attempt_id),
                "prompt": "must never reach telemetry",
            },
            data_classification=DataClassification.CLIENT_PRIVATE,
            tenant_id=tenant_id or uuid7(),
            client_id=client_id or uuid7(),
            resource_type="provider_attempt",
            resource_id=resolved_attempt_id,
            correlation_id=uuid7(),
            causation_id=uuid7(),
            trace_id="trace-observability-test",
            logical_operation_id=str(resolved_attempt_id),
            lease_owner="worker-observability",
            lease_until=now + timedelta(seconds=30),
            attempt_count=2,
        )


if __name__ == "__main__":
    unittest.main()
