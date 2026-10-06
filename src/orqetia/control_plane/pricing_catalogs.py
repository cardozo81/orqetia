"""Immutable administrative provider-pricing catalog versions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid7

from orqetia.usage_accounting import PricingCatalog, PricingResolver, PricingRule


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True)
class ProviderPricingCatalogVersion:
    catalog_version_id: UUID
    version_number: int
    rules: tuple[PricingRule, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.version_number < 1:
            raise ValueError("pricing catalog version_number must be positive")
        _aware(self.created_at, "created_at")
        catalog = PricingCatalog(self.rules)
        normalized = tuple(
            sorted(
                catalog.rules,
                key=lambda rule: (
                    rule.provider_id,
                    rule.model_id,
                    rule.reasoning_profile or "",
                    rule.rule_id,
                    rule.version,
                ),
            )
        )
        object.__setattr__(self, "rules", normalized)

    def catalog(self) -> PricingCatalog:
        return PricingCatalog(self.rules)

    def resolver(self) -> PricingResolver:
        return PricingResolver(self.catalog())


@dataclass(frozen=True)
class ProviderPricingAssignment:
    catalog_version_id: UUID
    assignment_version: int
    assigned_at: datetime

    def __post_init__(self) -> None:
        if self.assignment_version < 1:
            raise ValueError("pricing assignment_version must be positive")
        _aware(self.assigned_at, "assigned_at")


@dataclass(frozen=True)
class EffectiveProviderPricingCatalog:
    version: ProviderPricingCatalogVersion
    assignment: ProviderPricingAssignment

    @property
    def catalog(self) -> PricingCatalog:
        return self.version.catalog()

    @property
    def resolver(self) -> PricingResolver:
        return self.version.resolver()


@dataclass(frozen=True)
class ProviderPricingAuditEvent:
    action: str
    catalog_version_id: UUID
    version_number: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.action not in {"PRICING_PUBLISHED", "PRICING_ACTIVATED"}:
            raise ValueError("unknown provider pricing audit action")
        if self.version_number < 1:
            raise ValueError("audit version_number must be positive")
        _aware(self.occurred_at, "occurred_at")


class ProviderPricingAuditSink(Protocol):
    async def record(self, event: ProviderPricingAuditEvent) -> None: ...


class ProviderPricingCatalogRepository(Protocol):
    async def list_versions(self) -> tuple[ProviderPricingCatalogVersion, ...]: ...

    async def get_effective(self) -> EffectiveProviderPricingCatalog | None: ...

    async def publish_and_activate(
        self,
        *,
        version: ProviderPricingCatalogVersion,
        assignment: ProviderPricingAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveProviderPricingCatalog: ...


class InMemoryProviderPricingCatalogRepository:
    def __init__(self) -> None:
        self._versions: dict[UUID, ProviderPricingCatalogVersion] = {}
        self._assignment: ProviderPricingAssignment | None = None

    async def list_versions(self) -> tuple[ProviderPricingCatalogVersion, ...]:
        return tuple(
            sorted(self._versions.values(), key=lambda item: item.version_number)
        )

    async def get_effective(self) -> EffectiveProviderPricingCatalog | None:
        if self._assignment is None:
            return None
        version = self._versions.get(self._assignment.catalog_version_id)
        if version is None:
            raise RuntimeError("pricing assignment references missing catalog version")
        return EffectiveProviderPricingCatalog(version=version, assignment=self._assignment)

    async def publish_and_activate(
        self,
        *,
        version: ProviderPricingCatalogVersion,
        assignment: ProviderPricingAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveProviderPricingCatalog:
        if version.catalog_version_id != assignment.catalog_version_id:
            raise ValueError("assignment must reference pricing catalog version")
        if version.catalog_version_id in self._versions:
            raise ValueError("pricing catalog version already exists")
        if any(
            item.version_number == version.version_number
            for item in self._versions.values()
        ):
            raise ValueError("pricing catalog version number already exists")

        current = self._assignment
        if current is None:
            if expected_assignment_version is not None:
                raise ValueError("pricing assignment version conflict")
            if assignment.assignment_version != 1:
                raise ValueError("initial pricing assignment_version must be 1")
        else:
            if current.assignment_version != expected_assignment_version:
                raise ValueError("pricing assignment version conflict")
            if assignment.assignment_version != current.assignment_version + 1:
                raise ValueError("pricing assignment_version must increment by one")

        self._versions[version.catalog_version_id] = version
        self._assignment = assignment
        return EffectiveProviderPricingCatalog(
            version=version,
            assignment=assignment,
        )


class InMemoryProviderPricingAuditSink:
    def __init__(self) -> None:
        self.events: list[ProviderPricingAuditEvent] = []

    async def record(self, event: ProviderPricingAuditEvent) -> None:
        self.events.append(event)


class ProviderPricingAdminService:
    def __init__(
        self,
        *,
        repository: ProviderPricingCatalogRepository,
        audit: ProviderPricingAuditSink,
    ) -> None:
        self._repository = repository
        self._audit = audit

    async def publish_and_activate(
        self,
        *,
        rules: tuple[PricingRule, ...],
        occurred_at: datetime,
    ) -> EffectiveProviderPricingCatalog:
        _aware(occurred_at, "occurred_at")
        current = await self._repository.get_effective()
        versions = await self._repository.list_versions()
        next_number = 1 + max(
            (item.version_number for item in versions),
            default=0,
        )
        version = ProviderPricingCatalogVersion(
            catalog_version_id=uuid7(),
            version_number=next_number,
            rules=rules,
            created_at=occurred_at,
        )
        expected = None if current is None else current.assignment.assignment_version
        assignment = ProviderPricingAssignment(
            catalog_version_id=version.catalog_version_id,
            assignment_version=1 if expected is None else expected + 1,
            assigned_at=occurred_at,
        )
        effective = await self._repository.publish_and_activate(
            version=version,
            assignment=assignment,
            expected_assignment_version=expected,
        )
        await self._audit.record(
            ProviderPricingAuditEvent(
                action="PRICING_PUBLISHED",
                catalog_version_id=version.catalog_version_id,
                version_number=version.version_number,
                occurred_at=occurred_at,
            )
        )
        await self._audit.record(
            ProviderPricingAuditEvent(
                action="PRICING_ACTIVATED",
                catalog_version_id=version.catalog_version_id,
                version_number=version.version_number,
                occurred_at=occurred_at,
            )
        )
        return effective

    async def resolve_effective(self) -> EffectiveProviderPricingCatalog:
        effective = await self._repository.get_effective()
        if effective is None:
            raise LookupError("no effective provider pricing catalog")
        return effective
