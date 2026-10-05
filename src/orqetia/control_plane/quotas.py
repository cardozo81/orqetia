"""Client quota policy contracts owned by the Control Plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class QuotaScope(StrEnum):
    TENANT = "TENANT"
    CLIENT = "CLIENT"


class QuotaMetric(StrEnum):
    REQUESTS = "REQUESTS"
    TASKS = "TASKS"
    CONCURRENT_TASKS = "CONCURRENT_TASKS"
    TOKENS = "TOKENS"
    NATIVE_UNITS = "NATIVE_UNITS"
    PROVIDER_REQUESTS = "PROVIDER_REQUESTS"


class QuotaEnforcementMode(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"


@dataclass(frozen=True)
class QuotaPolicySnapshot:
    """Immutable effective quota policy passed to Usage & Accounting.

    Provider permissions remain a separate authorization concern. A provider-specific
    quota can restrict an already-authorized provider but can never grant permission.
    """

    policy_id: UUID
    version: int
    scope: QuotaScope
    tenant_id: UUID
    metric: QuotaMetric
    limit: Decimal
    enforcement: QuotaEnforcementMode
    effective_from: datetime
    client_id: UUID | None = None
    period_seconds: int | None = None
    burst: Decimal = Decimal("0")
    reservation_ttl_seconds: int = 300
    native_unit: str | None = None
    provider_id: str | None = None
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("quota policy version must be positive")
        if self.limit < 0:
            raise ValueError("quota limit cannot be negative")
        if self.burst < 0:
            raise ValueError("quota burst cannot be negative")
        if self.reservation_ttl_seconds < 1:
            raise ValueError("reservation_ttl_seconds must be positive")
        _require_aware(self.effective_from, "effective_from")
        if self.effective_to is not None:
            _require_aware(self.effective_to, "effective_to")
            if self.effective_to <= self.effective_from:
                raise ValueError("effective_to must be after effective_from")

        if self.scope is QuotaScope.CLIENT and self.client_id is None:
            raise ValueError("CLIENT quota requires client_id")
        if self.scope is QuotaScope.TENANT and self.client_id is not None:
            raise ValueError("TENANT quota must not pin client_id")

        if self.metric is QuotaMetric.CONCURRENT_TASKS:
            if self.period_seconds is not None:
                raise ValueError("CONCURRENT_TASKS must not define a reset period")
            if self.burst != 0:
                raise ValueError("CONCURRENT_TASKS does not support burst")
        elif self.period_seconds is None or self.period_seconds < 1:
            raise ValueError("periodic quota requires positive period_seconds")

        if self.metric is QuotaMetric.NATIVE_UNITS:
            if self.native_unit is None or not self.native_unit.strip():
                raise ValueError("NATIVE_UNITS quota requires native_unit")
        elif self.native_unit is not None:
            raise ValueError("native_unit is valid only for NATIVE_UNITS quota")

        if self.metric is QuotaMetric.PROVIDER_REQUESTS:
            if self.provider_id is None or not self.provider_id.strip():
                raise ValueError("PROVIDER_REQUESTS quota requires provider_id")
        if self.provider_id is not None and len(self.provider_id) > 100:
            raise ValueError("provider_id exceeds 100 characters")
        if self.native_unit is not None and len(self.native_unit) > 100:
            raise ValueError("native_unit exceeds 100 characters")

    @property
    def capacity(self) -> Decimal:
        return self.limit + self.burst

    @property
    def reference(self) -> str:
        return f"{self.policy_id}@{self.version}"

    def active_at(self, occurred_at: datetime) -> bool:
        _require_aware(occurred_at, "occurred_at")
        return self.effective_from <= occurred_at and (
            self.effective_to is None or occurred_at < self.effective_to
        )

    def validate_subject(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
        provider_id: str | None,
        native_unit: str | None,
        occurred_at: datetime,
    ) -> None:
        if not self.active_at(occurred_at):
            raise ValueError("quota policy is not effective at occurred_at")
        if tenant_id != self.tenant_id:
            raise PermissionError("quota policy tenant mismatch")
        if self.scope is QuotaScope.CLIENT and client_id != self.client_id:
            raise PermissionError("quota policy client mismatch")
        if self.provider_id is not None and provider_id != self.provider_id:
            raise ValueError("provider-specific quota does not match provider")
        if self.native_unit is not None and native_unit != self.native_unit:
            raise ValueError("native-unit quota does not match native unit")
