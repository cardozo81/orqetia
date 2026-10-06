"""Immutable audit ledger, retention policy and integrity verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7


class AuditClass(StrEnum):
    SECURITY_ADMIN = "SECURITY_ADMIN"
    CLIENT_ACTIVITY = "CLIENT_ACTIVITY"


@dataclass(frozen=True)
class AuditRetentionRule:
    audit_class: AuditClass
    retention_days: int

    def __post_init__(self) -> None:
        if not 1 <= self.retention_days <= 3650:
            raise ValueError("audit retention_days must be between 1 and 3650")


@dataclass(frozen=True)
class AuditRetentionPolicy:
    policy_id: str
    version: int
    rules: tuple[AuditRetentionRule, ...]

    def __post_init__(self) -> None:
        policy_id = self.policy_id.strip()
        if not policy_id or len(policy_id) > 100:
            raise ValueError("audit retention policy_id must contain 1..100 characters")
        if self.version < 1:
            raise ValueError("audit retention policy version must be positive")
        classes = [rule.audit_class for rule in self.rules]
        if len(classes) != len(set(classes)):
            raise ValueError("audit retention policy contains duplicate classes")
        if set(classes) != set(AuditClass):
            raise ValueError("audit retention policy must define every audit class")
        object.__setattr__(self, "policy_id", policy_id)

    def retention_for(self, audit_class: AuditClass) -> timedelta:
        for rule in self.rules:
            if rule.audit_class is audit_class:
                return timedelta(days=rule.retention_days)
        raise LookupError("audit retention class is not configured")


DEVELOPMENT_AUDIT_RETENTION_POLICY_V1 = AuditRetentionPolicy(
    policy_id="ORQETIA_AUDIT_DEVELOPMENT",
    version=1,
    rules=(
        AuditRetentionRule(AuditClass.SECURITY_ADMIN, 730),
        AuditRetentionRule(AuditClass.CLIENT_ACTIVITY, 365),
    ),
)


@dataclass(frozen=True)
class ImmutableAuditEvent:
    event_id: UUID
    audit_class: AuditClass
    action: str
    result: str
    correlation_id: str
    occurred_at: datetime
    recorded_at: datetime
    retention_policy_id: str
    retention_policy_version: int
    retain_until: datetime
    event_hash: str
    actor_type: str | None = None
    actor_id: str | None = None
    tenant_id: UUID | None = None
    client_id: UUID | None = None
    resource_type: str | None = None
    resource_id: str | None = None

    def __post_init__(self) -> None:
        for field, value, maximum in (
            ("action", self.action, 100),
            ("result", self.result, 40),
            ("correlation_id", self.correlation_id, 200),
            ("retention_policy_id", self.retention_policy_id, 100),
        ):
            normalized = value.strip()
            if not normalized or len(normalized) > maximum:
                raise ValueError(f"{field} must contain 1..{maximum} characters")
        for field, value, maximum in (
            ("actor_type", self.actor_type, 60),
            ("actor_id", self.actor_id, 500),
            ("resource_type", self.resource_type, 100),
            ("resource_id", self.resource_id, 500),
        ):
            if value is not None and (
                not value.strip() or len(value) > maximum
            ):
                raise ValueError(f"{field} must contain 1..{maximum} characters")
        if self.retention_policy_version < 1:
            raise ValueError("retention policy version must be positive")
        for field, value in (
            ("occurred_at", self.occurred_at),
            ("recorded_at", self.recorded_at),
            ("retain_until", self.retain_until),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field} must be timezone-aware")
        if self.recorded_at < self.occurred_at:
            raise ValueError("recorded_at cannot precede occurred_at")
        if self.retain_until <= self.occurred_at:
            raise ValueError("retain_until must follow occurred_at")
        if len(self.event_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.event_hash
        ):
            raise ValueError("event_hash must be lowercase SHA-256 hex")


class ImmutableAuditStore(Protocol):
    async def record(self, event: ImmutableAuditEvent) -> None: ...

    async def list_owned(
        self,
        *,
        audit_class: AuditClass,
        tenant_id: UUID,
        client_id: UUID,
        limit: int,
    ) -> tuple[ImmutableAuditEvent, ...]: ...

    async def find_integrity_failures(
        self,
        *,
        limit: int,
    ) -> tuple[UUID, ...]: ...


def _canonical_payload(
    *,
    event_id: UUID,
    audit_class: AuditClass,
    action: str,
    result: str,
    correlation_id: str,
    occurred_at: datetime,
    recorded_at: datetime,
    retention_policy_id: str,
    retention_policy_version: int,
    retain_until: datetime,
    actor_type: str | None,
    actor_id: str | None,
    tenant_id: UUID | None,
    client_id: UUID | None,
    resource_type: str | None,
    resource_id: str | None,
) -> bytes:
    payload = {
        "event_id": str(event_id),
        "audit_class": audit_class.value,
        "action": action,
        "result": result,
        "correlation_id": correlation_id,
        "occurred_at": occurred_at.isoformat(),
        "recorded_at": recorded_at.isoformat(),
        "retention_policy_id": retention_policy_id,
        "retention_policy_version": retention_policy_version,
        "retain_until": retain_until.isoformat(),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "tenant_id": None if tenant_id is None else str(tenant_id),
        "client_id": None if client_id is None else str(client_id),
        "resource_type": resource_type,
        "resource_id": resource_id,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_event_hash(event: ImmutableAuditEvent) -> str:
    return hashlib.sha256(
        _canonical_payload(
            event_id=event.event_id,
            audit_class=event.audit_class,
            action=event.action,
            result=event.result,
            correlation_id=event.correlation_id,
            occurred_at=event.occurred_at,
            recorded_at=event.recorded_at,
            retention_policy_id=event.retention_policy_id,
            retention_policy_version=event.retention_policy_version,
            retain_until=event.retain_until,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            tenant_id=event.tenant_id,
            client_id=event.client_id,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
        )
    ).hexdigest()


def verify_event_integrity(event: ImmutableAuditEvent) -> bool:
    return compute_event_hash(event) == event.event_hash


def build_immutable_audit_event(
    *,
    audit_class: AuditClass,
    action: str,
    result: str,
    correlation_id: str,
    occurred_at: datetime,
    recorded_at: datetime,
    policy: AuditRetentionPolicy = DEVELOPMENT_AUDIT_RETENTION_POLICY_V1,
    actor_type: str | None = None,
    actor_id: str | None = None,
    tenant_id: UUID | None = None,
    client_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> ImmutableAuditEvent:
    event_id = uuid7()
    retain_until = occurred_at + policy.retention_for(audit_class)
    provisional = ImmutableAuditEvent(
        event_id=event_id,
        audit_class=audit_class,
        action=action,
        result=result,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        recorded_at=recorded_at,
        retention_policy_id=policy.policy_id,
        retention_policy_version=policy.version,
        retain_until=retain_until,
        event_hash="0" * 64,
        actor_type=actor_type,
        actor_id=actor_id,
        tenant_id=tenant_id,
        client_id=client_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    return ImmutableAuditEvent(
        **{
            **provisional.__dict__,
            "event_hash": compute_event_hash(provisional),
        }
    )
