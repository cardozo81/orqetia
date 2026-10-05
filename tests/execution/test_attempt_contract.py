from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid7

from orqetia.execution import (
    DispatchAction,
    ProviderAttempt,
    ProviderAttemptStatus,
    execution_sessions,
    provider_attempts,
    tasks,
)
from orqetia.execution.sessions import ExecutionTargetSnapshot, OwnershipScope


class ProviderAttemptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(UTC)
        self.scope = OwnershipScope(uuid7(), uuid7())
        self.target = ExecutionTargetSnapshot("ORQETIA_TEST_PROVIDER", "sim-small", "standard")

    def test_prepared_attempt_has_no_dispatch_owner(self) -> None:
        attempt = self._attempt()
        self.assertIs(attempt.status, ProviderAttemptStatus.PREPARED)
        self.assertIsNone(attempt.dispatch_work_id)
        self.assertIsNone(attempt.dispatch_started_at)
        self.assertFalse(attempt.status.terminal)

    def test_dispatching_requires_work_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires work"):
            self._attempt(
                status=ProviderAttemptStatus.DISPATCHING,
                dispatch_started_at=self.now,
            )

    def test_completed_requires_outcome_and_terminal_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "terminal attempt"):
            self._attempt(
                status=ProviderAttemptStatus.COMPLETED,
                provider_outcome="SUCCESS",
            )
        with self.assertRaisesRegex(ValueError, "provider_outcome"):
            self._attempt(
                status=ProviderAttemptStatus.COMPLETED,
                terminal_at=self.now,
            )

    def test_task_scoped_attempt_requires_session(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires session_id"):
            self._attempt(task_id=uuid7(), session_id=None)

    def test_attempt_table_is_execution_owned_and_cross_scoped(self) -> None:
        self.assertEqual(provider_attempts.schema, "execution")
        self.assertEqual(tasks.schema, "execution")
        self.assertEqual(execution_sessions.schema, "execution")
        self.assertIn("dispatch_work_id", provider_attempts.c)
        self.assertIn("provider_outcome", provider_attempts.c)
        self.assertNotIn("provider_cost", provider_attempts.c)
        self.assertNotIn("credential", provider_attempts.c)

    def test_dispatch_actions_distinguish_duplicate_and_reclaim(self) -> None:
        self.assertNotEqual(
            DispatchAction.DUPLICATE_WORK,
            DispatchAction.MARKED_AMBIGUOUS,
        )

    def _attempt(self, **overrides: object) -> ProviderAttempt:
        values: dict[str, object] = {
            "attempt_id": uuid7(),
            "task_id": uuid7(),
            "session_id": uuid7(),
            "ownership": self.scope,
            "operation": "TASK_EXECUTION",
            "target": self.target,
            "cycle": 1,
            "attempt_index": 1,
            "request_reference": "payload://request/attempt",
            "request_fingerprint": "a" * 64,
            "status": ProviderAttemptStatus.PREPARED,
            "missing_requirements": ("VALID_JSON",),
            "created_at": self.now,
            "updated_at": self.now,
        }
        values.update(overrides)
        return ProviderAttempt(**values)


if __name__ == "__main__":
    unittest.main()
