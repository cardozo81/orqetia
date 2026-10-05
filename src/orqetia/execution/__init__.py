"""Execution bounded-context contracts and PostgreSQL adapters."""

from .postgres import PostgresExecutionSessionStore
from .sessions import (
    ExecutionSession,
    ExecutionSessionStore,
    ExecutionTargetSnapshot,
    OwnershipScope,
    QuarantineState,
    SessionPolicySnapshot,
    SessionStatus,
    TargetHealth,
    TargetRuntimeState,
)
from .tables import execution_metadata, execution_sessions, session_target_runtime
from .task_postgres import PostgresExecutionTaskStore
from .task_tables import (
    task_cycle_decisions,
    task_metadata,
    task_transitions,
    tasks,
)
from .tasks import (
    ExecutionMode,
    ExecutionTask,
    ExecutionTaskStore,
    RequestedTargetSnapshot,
    TaskCycleDecision,
    TaskPayloadReferences,
    TaskReasonEnvelope,
    TaskStatus,
    VALID_TRANSITIONS,
    validate_requirement_partition,
    validate_transition,
)

__all__ = [
    "ExecutionMode",
    "ExecutionSession",
    "ExecutionSessionStore",
    "ExecutionTargetSnapshot",
    "ExecutionTask",
    "ExecutionTaskStore",
    "OwnershipScope",
    "PostgresExecutionSessionStore",
    "PostgresExecutionTaskStore",
    "QuarantineState",
    "RequestedTargetSnapshot",
    "SessionPolicySnapshot",
    "SessionStatus",
    "TargetHealth",
    "TargetRuntimeState",
    "TaskCycleDecision",
    "TaskPayloadReferences",
    "TaskReasonEnvelope",
    "TaskStatus",
    "VALID_TRANSITIONS",
    "execution_metadata",
    "execution_sessions",
    "session_target_runtime",
    "task_cycle_decisions",
    "task_metadata",
    "task_transitions",
    "tasks",
    "validate_requirement_partition",
    "validate_transition",
]
