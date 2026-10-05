"""Secure read-model contracts for client and Backoffice query surfaces."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Mapping
from uuid import UUID, uuid7


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class ReadAudience(StrEnum):
    CLIENT = "CLIENT"
    BACKOFFICE = "BACKOFFICE"


class ReadClassification(StrEnum):
    CLIENT_PRIVATE = "CLIENT_PRIVATE"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class ReadSurface(StrEnum):
    OVERVIEW = "OVERVIEW"
    TASK_LIST = "TASK_LIST"
    USAGE_SUMMARY = "USAGE_SUMMARY"
    QUOTA_UTILIZATION = "QUOTA_UTILIZATION"
    ESTIMATE_BENCHMARK = "ESTIMATE_BENCHMARK"
    PROVIDER_COST_SUMMARY = "PROVIDER_COST_SUMMARY"
    CREDENTIAL_STATUS = "CREDENTIAL_STATUS"


@dataclass(frozen=True)
class SurfacePolicy:
    stale_while_revalidate: bool
    cache_max_age_seconds: int
    stale_window_seconds: int
    maximum_page_size: int
    maximum_export_rows: int

    def __post_init__(self) -> None:
        if self.cache_max_age_seconds < 0:
            raise ValueError("cache_max_age_seconds cannot be negative")
        if self.stale_window_seconds < 0:
            raise ValueError("stale_window_seconds cannot be negative")
        if not self.stale_while_revalidate and self.stale_window_seconds != 0:
            raise ValueError("non-SWR surface cannot define stale window")
        if self.maximum_page_size < 1 or self.maximum_export_rows < 1:
            raise ValueError("page/export limits must be positive")


SURFACE_POLICIES: Mapping[ReadSurface, SurfacePolicy] = {
    ReadSurface.OVERVIEW: SurfacePolicy(True, 30, 60, 100, 10_000),
    ReadSurface.TASK_LIST: SurfacePolicy(False, 10, 0, 100, 20_000),
    ReadSurface.USAGE_SUMMARY: SurfacePolicy(True, 60, 120, 200, 50_000),
    ReadSurface.QUOTA_UTILIZATION: SurfacePolicy(False, 5, 0, 100, 10_000),
    ReadSurface.ESTIMATE_BENCHMARK: SurfacePolicy(True, 300, 600, 100, 10_000),
    ReadSurface.PROVIDER_COST_SUMMARY: SurfacePolicy(True, 60, 120, 200, 50_000),
    ReadSurface.CREDENTIAL_STATUS: SurfacePolicy(False, 0, 0, 100, 10_000),
}

_CLIENT_FORBIDDEN_KEY_PARTS = (
    "provider_cost",
    "client_charge",
    "currency",
    "unit_price",
    "pricing",
    "provider_account",
    "provider_credential",
    "credit_balance",
    "commercial_term",
)


@dataclass(frozen=True)
class ProjectionIdentity:
    surface: ReadSurface
    audience: ReadAudience
    query_fingerprint: str
    tenant_id: UUID | None = None
    client_id: UUID | None = None
    role_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.query_fingerprint.strip() or len(self.query_fingerprint) > 200:
            raise ValueError("query_fingerprint must contain 1..200 characters")
        normalized_roles = tuple(sorted(set(self.role_scope)))
        if any(not role.strip() or len(role) > 100 for role in normalized_roles):
            raise ValueError("role_scope values must contain 1..100 characters")
        object.__setattr__(self, "role_scope", normalized_roles)

        if self.audience is ReadAudience.CLIENT:
            if self.tenant_id is None or self.client_id is None:
                raise ValueError("CLIENT projection requires tenant_id and client_id")
            if self.surface is ReadSurface.PROVIDER_COST_SUMMARY:
                raise ValueError("provider financial surface is Backoffice-only")
        elif self.client_id is not None and self.tenant_id is None:
            raise ValueError("client_id cannot be scoped without tenant_id")

    @property
    def cache_partition_key(self) -> str:
        material = "|".join(
            (
                self.surface.value,
                self.audience.value,
                str(self.tenant_id or "-"),
                str(self.client_id or "-"),
                ",".join(self.role_scope),
                self.query_fingerprint,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProjectionFreshness:
    as_of: datetime
    source_watermark: str
    refreshed_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.as_of, "as_of")
        _require_aware(self.refreshed_at, "refreshed_at")
        if not self.source_watermark.strip() or len(self.source_watermark) > 300:
            raise ValueError("source_watermark must contain 1..300 characters")
        if self.as_of > self.refreshed_at:
            raise ValueError("as_of cannot be later than refreshed_at")

    def stale_at(self, surface: ReadSurface) -> datetime:
        policy = SURFACE_POLICIES[surface]
        return self.refreshed_at + timedelta(seconds=policy.cache_max_age_seconds)

    def expires_at(self, surface: ReadSurface) -> datetime:
        policy = SURFACE_POLICIES[surface]
        return self.stale_at(surface) + timedelta(seconds=policy.stale_window_seconds)


@dataclass(frozen=True)
class ReadModelDocument:
    projection_id: UUID
    identity: ProjectionIdentity
    version: int
    classification: ReadClassification
    freshness: ProjectionFreshness
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("projection version must be positive")
        if self.identity.audience is ReadAudience.CLIENT:
            if self.classification not in {
                ReadClassification.CLIENT_PRIVATE,
                ReadClassification.INTERNAL,
            }:
                raise ValueError("client projection classification is not client-visible")
            _reject_client_financial_fields(self.payload)
        try:
            json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("projection payload must be JSON-serializable") from error

    @classmethod
    def create(
        cls,
        *,
        identity: ProjectionIdentity,
        version: int,
        classification: ReadClassification,
        freshness: ProjectionFreshness,
        payload: Mapping[str, object],
    ) -> ReadModelDocument:
        return cls(
            projection_id=uuid7(),
            identity=identity,
            version=version,
            classification=classification,
            freshness=freshness,
            payload=dict(payload),
        )

    def cache_control(self) -> str:
        policy = SURFACE_POLICIES[self.identity.surface]
        parts = ["private", f"max-age={policy.cache_max_age_seconds}"]
        if policy.stale_while_revalidate:
            parts.append(f"stale-while-revalidate={policy.stale_window_seconds}")
        return ", ".join(parts)


def _reject_client_financial_fields(value: object, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _CLIENT_FORBIDDEN_KEY_PARTS):
                raise ValueError(f"client projection contains forbidden financial field: {path}.{key}")
            _reject_client_financial_fields(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_client_financial_fields(nested, f"{path}[{index}]")
