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

__all__ = [
    "ExecutionSession",
    "ExecutionSessionStore",
    "ExecutionTargetSnapshot",
    "OwnershipScope",
    "PostgresExecutionSessionStore",
    "QuarantineState",
    "SessionPolicySnapshot",
    "SessionStatus",
    "TargetHealth",
    "TargetRuntimeState",
    "execution_metadata",
    "execution_sessions",
    "session_target_runtime",
]
