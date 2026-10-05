from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid7

from orqetia.execution import (
    ExecutionMode,
    ExecutionTargetSnapshot,
    ExecutionTask,
    OwnershipScope,
    RequestedTargetSnapshot,
    TaskCycleDecision,
    TaskPayloadReferences,
    TaskStatus,
    VALID_TRANSITIONS,
    task_cycle_decisions,
    task_transitions,
    tasks,
    validate_transition,
)

ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json"


class TaskStateMachineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(UTC)
        self.scope = OwnershipScope(uuid7(), uuid7())
        self.target = ExecutionTargetSnapshot("OPENAI", "gpt-test", "standard")

    def test_statuses_match_canonical_openapi_exactly(self) -> None:
        document = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        public_statuses = set(
            document["components"]["schemas"]["TaskView"]["properties"]["status"]["enum"]
        )
        self.assertEqual({status.value for status in TaskStatus}, public_statuses)
        self.assertNotIn("PENDING", public_statuses)
        self.assertNotIn("BLOCKED", public_statuses)

    def test_transition_table_has_no_outgoing_terminal_edges(self) -> None:
        terminal = {status for status in TaskStatus if status.terminal}
        self.assertTrue(terminal)
        for status in terminal:
            self.assertEqual(VALID_TRANSITIONS[status], frozenset())

    def test_invalid_transition_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid task transition"):
            validate_transition(TaskStatus.CREATED, TaskStatus.COMPLETE)

    def test_auto_task_never_freezes_effective_target(self) -> None:
        task = self._task()
        self.assertIsNone(task.requested_target)
        self.assertIsNone(task.effective_target)

        with self.assertRaisesRegex(ValueError, "AUTO task"):
            self._task(effective_target=self.target)

    def test_explicit_target_requires_resolved_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires requested and effective"):
            self._task(
                requested_execution_mode=ExecutionMode.EXPLICIT_TARGET,
                requested_target=RequestedTargetSnapshot("OPENAI"),
            )

        task = self._task(
            requested_execution_mode=ExecutionMode.EXPLICIT_TARGET,
            requested_target=RequestedTargetSnapshot("OPENAI"),
            effective_target=self.target,
        )
        self.assertEqual(task.effective_target, self.target)

    def test_requirement_partition_is_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "partition"):
            self._task(
                requirements=("VALID_JSON", "CITED"),
                accepted_requirements=("VALID_JSON",),
                missing_requirements=(),
            )

    def test_successful_terminal_state_requires_result_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "result_reference"):
            self._task(
                status=TaskStatus.COMPLETE,
                accepted_requirements=("VALID_JSON",),
                missing_requirements=(),
                terminal_at=self.now,
            )

    def test_cycle_decision_rejects_duplicate_candidate_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate targets"):
            TaskCycleDecision(
                cycle_index=1,
                candidate_order=(self.target, self.target),
                accepted_snapshot=(),
                missing_snapshot=("VALID_JSON",),
                recorded_at=self.now,
            )

    def test_physical_task_tables_store_references_not_raw_payloads(self) -> None:
        self.assertEqual(tasks.schema, "execution")
        self.assertEqual(task_transitions.schema, "execution")
        self.assertEqual(task_cycle_decisions.schema, "execution")
        self.assertIn("input_reference", tasks.c)
        self.assertIn("input_fingerprint", tasks.c)
        self.assertIn("result_reference", tasks.c)
        self.assertNotIn("input", tasks.c)
        self.assertNotIn("context", tasks.c)
        self.assertNotIn("result", tasks.c)

    def _task(self, **overrides: object) -> ExecutionTask:
        values: dict[str, object] = {
            "task_id": uuid7(),
            "session_id": uuid7(),
            "ownership": self.scope,
            "operation": "TASK_EXECUTION",
            "status": TaskStatus.CREATED,
            "effective_policy_version_id": uuid7(),
            "requested_execution_mode": ExecutionMode.AUTO,
            "requirements": ("VALID_JSON",),
            "accepted_requirements": (),
            "missing_requirements": ("VALID_JSON",),
            "payloads": TaskPayloadReferences(
                input_reference="payload://input/1",
                input_fingerprint="a" * 64,
            ),
            "created_at": self.now,
            "updated_at": self.now,
        }
        values.update(overrides)
        return ExecutionTask(**values)


if __name__ == "__main__":
    unittest.main()
