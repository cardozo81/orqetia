from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid7

from orqetia.execution import (
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
)
from orqetia.providers import (
    OutputKind,
    ProviderAttemptResult,
    ProviderOutcome,
    ProviderTarget,
)


class ProviderAttemptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(UTC)
        self.target = ProviderTarget("ORQETIA_TEST_PROVIDER", "sim-small", "standard")

    def test_ready_attempt_contains_no_dispatch_outcome(self) -> None:
        attempt = self._attempt()
        self.assertEqual(attempt.status, ProviderAttemptStatus.READY)
        self.assertIsNone(attempt.dispatch_started_at)
        self.assertIsNone(attempt.result)

    def test_dispatching_requires_dispatch_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "dispatch_started_at"):
            self._attempt(status=ProviderAttemptStatus.DISPATCHING)

    def test_completed_requires_matching_result_identity(self) -> None:
        result = ProviderAttemptResult(
            attempt_id=uuid7(),
            outcome=ProviderOutcome.SUCCESS,
            output_kind=OutputKind.TEXT,
            accepted_requirements=("VALID_JSON",),
            missing_requirements=(),
            simulated_latency_ms=1,
        )
        with self.assertRaisesRegex(ValueError, "matching provider result"):
            self._attempt(
                status=ProviderAttemptStatus.COMPLETED,
                dispatch_started_at=self.now,
                terminal_at=self.now,
                result=result,
            )

    def test_ambiguous_requires_error_class(self) -> None:
        with self.assertRaisesRegex(ValueError, "ambiguity_error_class"):
            self._attempt(
                status=ProviderAttemptStatus.AMBIGUOUS,
                dispatch_started_at=self.now,
                terminal_at=self.now,
            )

    def test_task_scoped_attempt_requires_session_and_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires session and ownership"):
            self._attempt(task_id=uuid7(), ownership=None, session_id=None)

    def test_taskless_shape_remains_representable(self) -> None:
        attempt = self._attempt(ownership=None, task_id=None, session_id=None)
        self.assertIsNone(attempt.ownership)
        self.assertIsNone(attempt.task_id)

    def _attempt(self, **overrides: object) -> ProviderAttempt:
        values: dict[str, object] = {
            "attempt_id": uuid7(),
            "work_id": uuid7(),
            "ownership": OwnershipScope(uuid7(), uuid7()),
            "session_id": uuid7(),
            "task_id": uuid7(),
            "operation": "TASK_EXECUTION",
            "target": self.target,
            "cycle": 1,
            "attempt_index": 1,
            "request_reference": "payload://request/1",
            "request_fingerprint": "a" * 64,
            "missing_requirements": ("VALID_JSON",),
            "status": ProviderAttemptStatus.READY,
            "created_at": self.now,
            "updated_at": self.now,
        }
        values.update(overrides)
        return ProviderAttempt(**values)


if __name__ == "__main__":
    unittest.main()
