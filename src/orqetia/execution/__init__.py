"""Execution bounded-context contracts and PostgreSQL adapters."""

from .attempt_postgres import (
    PROVIDER_DISPATCH_OPERATION,
    PROVIDER_DISPATCH_VERSION,
    PostgresProviderAttemptStore,
)
from .attempt_tables import attempt_metadata, provider_attempts
from .attempts import ProviderAttempt, ProviderAttemptStatus, ProviderAttemptStore
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
    VALID_TRANSITIONS,
    ExecutionMode,
    ExecutionTask,
    ExecutionTaskStore,
    RequestedTargetSnapshot,
    TaskCycleDecision,
    TaskPayloadReferences,
    TaskReasonEnvelope,
    TaskStatus,
    validate_requirement_partition,
    validate_transition,
)

__all__ = [
    "PROVIDER_DISPATCH_OPERATION",
    "PROVIDER_DISPATCH_VERSION",
    "ExecutionMode",
    "ExecutionSession",
    "ExecutionSessionStore",
    "ExecutionTargetSnapshot",
    "ExecutionTask",
    "ExecutionTaskStore",
    "OwnershipScope",
    "PostgresExecutionSessionStore",
    "PostgresExecutionTaskStore",
    "PostgresProviderAttemptStore",
    "ProviderAttempt",
    "ProviderAttemptStatus",
    "ProviderAttemptStore",
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
    "attempt_metadata",
    "execution_metadata",
    "execution_sessions",
    "provider_attempts",
    "session_target_runtime",
    "task_cycle_decisions",
    "task_metadata",
    "task_transitions",
    "tasks",
    "validate_requirement_partition",
    "validate_transition",
]
