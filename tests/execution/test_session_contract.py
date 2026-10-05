from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid7

from orqetia.execution import (
    ExecutionSession,
    ExecutionTargetSnapshot,
    OwnershipScope,
    QuarantineState,
    SessionPolicySnapshot,
    SessionStatus,
    TargetHealth,
    TargetRuntimeState,
    execution_sessions,
    session_target_runtime,
)


class ExecutionSessionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(UTC)
        self.target = ExecutionTargetSnapshot("OPENAI", "gpt-test", "standard")
        self.policy = SessionPolicySnapshot(
            effective_policy_version_id=uuid7(),
            authorized_targets=(self.target,),
        )

    def test_policy_snapshot_rejects_duplicate_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates"):
            SessionPolicySnapshot(
                effective_policy_version_id=uuid7(),
                authorized_targets=(self.target, self.target),
            )

    def test_terminal_quarantine_never_has_expiry(self) -> None:
        state = TargetRuntimeState(
            target=self.target,
            health=TargetHealth.UNAVAILABLE,
            quarantine=QuarantineState.TERMINAL,
            quarantine_reason_code="AUTH_FAILURE",
            updated_at=self.now,
        )
        self.assertIsNone(state.quarantine_until)

        with self.assertRaisesRegex(ValueError, "cannot auto-expire"):
            TargetRuntimeState(
                target=self.target,
                health=TargetHealth.UNAVAILABLE,
                quarantine=QuarantineState.TERMINAL,
                quarantine_until=self.now + timedelta(minutes=5),
                updated_at=self.now,
            )

    def test_temporary_quarantine_requires_expiry(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires quarantine_until"):
            TargetRuntimeState(
                target=self.target,
                health=TargetHealth.DEGRADED,
                quarantine=QuarantineState.TEMPORARY,
                updated_at=self.now,
            )

    def test_terminal_session_status_requires_terminal_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "terminal session status"):
            ExecutionSession(
                session_id=uuid7(),
                ownership=OwnershipScope(uuid7(), uuid7()),
                status=SessionStatus.CLOSED,
                policy=self.policy,
                created_at=self.now,
                updated_at=self.now,
            )

    def test_session_has_no_cross_task_cycle_budget(self) -> None:
        session = ExecutionSession(
            session_id=uuid7(),
            ownership=OwnershipScope(uuid7(), uuid7()),
            status=SessionStatus.ACTIVE,
            policy=self.policy,
            created_at=self.now,
            updated_at=self.now,
        )
        self.assertFalse(hasattr(session, "cycle"))
        self.assertFalse(hasattr(session, "cycles"))
        self.assertFalse(hasattr(session, "cycle_budget"))

    def test_runtime_state_must_reference_authorized_target(self) -> None:
        other = ExecutionTargetSnapshot("OTHER", "model", "standard")
        with self.assertRaisesRegex(ValueError, "authorized target"):
            ExecutionSession(
                session_id=uuid7(),
                ownership=OwnershipScope(uuid7(), uuid7()),
                status=SessionStatus.ACTIVE,
                policy=self.policy,
                created_at=self.now,
                updated_at=self.now,
                target_runtime=(
                    TargetRuntimeState(
                        target=other,
                        health=TargetHealth.HEALTHY,
                        quarantine=QuarantineState.NONE,
                        updated_at=self.now,
                    ),
                ),
            )

    def test_physical_tables_are_execution_owned_and_scope_children(self) -> None:
        self.assertEqual(execution_sessions.schema, "execution")
        self.assertEqual(session_target_runtime.schema, "execution")
        self.assertIn("tenant_id", execution_sessions.c)
        self.assertIn("client_id", execution_sessions.c)
        self.assertIn("effective_policy_version_id", execution_sessions.c)
        self.assertIn("authorized_targets", execution_sessions.c)
        self.assertIn("usage_reference_ids", execution_sessions.c)
        self.assertIn("internal_cost_reference_ids", execution_sessions.c)
        self.assertNotIn("cycle", execution_sessions.c)
        self.assertNotIn("cycle_budget", execution_sessions.c)


if __name__ == "__main__":
    unittest.main()
