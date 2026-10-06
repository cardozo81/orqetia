"""Audit bounded-context contracts."""

from .customer_activity import (
    CustomerActivityEvent,
    CustomerActivityStore,
    InMemoryCustomerActivityStore,
    activity_event,
)
from .customer_activity_postgres import PostgresCustomerActivityStore
from .customer_activity_tables import customer_activity_events

__all__ = [
    "CustomerActivityEvent",
    "CustomerActivityStore",
    "InMemoryCustomerActivityStore",
    "PostgresCustomerActivityStore",
    "activity_event",
    "customer_activity_events",
]

from .immutable import (
    AuditClass,
    AuditRetentionPolicy,
    AuditRetentionRule,
    DEVELOPMENT_AUDIT_RETENTION_POLICY_V1,
    ImmutableAuditEvent,
    ImmutableAuditStore,
    build_immutable_audit_event,
    compute_event_hash,
    verify_event_integrity,
)
from .immutable_postgres import PostgresImmutableAuditStore
from .immutable_tables import audit_events

__all__ += [
    "AuditClass",
    "AuditRetentionPolicy",
    "AuditRetentionRule",
    "DEVELOPMENT_AUDIT_RETENTION_POLICY_V1",
    "ImmutableAuditEvent",
    "ImmutableAuditStore",
    "PostgresImmutableAuditStore",
    "audit_events",
    "build_immutable_audit_event",
    "compute_event_hash",
    "verify_event_integrity",
]
