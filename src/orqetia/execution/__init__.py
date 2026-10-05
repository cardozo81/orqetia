"""Execution bounded-context contracts and PostgreSQL adapters."""

from .attempt_postgres import PostgresProviderAttemptStore
from .attempt_tables import attempt_metadata, provider_attempts
from .attempts import (
    DispatchAction,
    DispatchClaim,
    ProviderAttempt,
    ProviderAttemptStatus,
    ProviderAttemptStore,
)
from .orchestration import (
    AttemptObservation,
    AttemptObservationStatus,
    CanonicalOrchestrationPolicyEngine,
    OrchestrationCandidate,
    OrchestrationCyclePlan,
    OrchestrationDecision,
    OrchestrationDisposition,
    OrchestrationPolicy,
    RequirementProgress,
)
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
    "AttemptObservation",
    "AttemptObservationStatus",
    "CanonicalOrchestrationPolicyEngine",
    "DispatchAction",
    "DispatchClaim",
    "ExecutionMode",
    "ExecutionSession",
    "ExecutionSessionStore",
    "ExecutionTargetSnapshot",
    "ExecutionTask",
    "ExecutionTaskStore",
    "OrchestrationCandidate",
    "OrchestrationCyclePlan",
    "OrchestrationDecision",
    "OrchestrationDisposition",
    "OrchestrationPolicy",
    "OwnershipScope",
    "PostgresExecutionSessionStore",
    "PostgresExecutionTaskStore",
    "PostgresProviderAttemptStore",
    "ProviderAttempt",
    "ProviderAttemptStatus",
    "ProviderAttemptStore",
    "QuarantineState",
    "RequestedTargetSnapshot",
    "RequirementProgress",
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
