"""Immutable administrative provider catalog versions materialized as ProviderRegistry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID, uuid7

from orqetia.providers import ProviderRegistry, ProviderSpec


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, order=True)
class ProviderEndpointMetadata:
    provider_id: str
    base_url: str
    region: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or len(self.provider_id) > 100:
            raise ValueError("provider_id must contain 1..100 characters")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("provider endpoint must be an https URL without userinfo")
        if parsed.query or parsed.fragment:
            raise ValueError("provider endpoint cannot contain query or fragment")
        if len(self.base_url) > 1000:
            raise ValueError("provider endpoint exceeds 1000 characters")
        if self.region is not None and (
            not self.region.strip() or len(self.region) > 100
        ):
            raise ValueError("region must contain 1..100 characters")


@dataclass(frozen=True)
class ProviderCatalogVersion:
    catalog_version_id: UUID
    version_number: int
    providers: tuple[ProviderSpec, ...]
    endpoints: tuple[ProviderEndpointMetadata, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.version_number < 1:
            raise ValueError("catalog version_number must be positive")
        _aware(self.created_at, "created_at")
        registry = ProviderRegistry(self.providers)
        normalized_providers = registry.providers
        object.__setattr__(self, "providers", normalized_providers)

        endpoint_ids = [item.provider_id for item in self.endpoints]
        if len(set(endpoint_ids)) != len(endpoint_ids):
            raise ValueError("provider endpoints cannot contain duplicate provider_id values")
        known = {provider.provider_id for provider in normalized_providers}
        unknown = set(endpoint_ids) - known
        if unknown:
            raise ValueError("provider endpoint references unknown provider")
        object.__setattr__(
            self,
            "endpoints",
            tuple(sorted(self.endpoints, key=lambda item: item.provider_id)),
        )

    def registry(self) -> ProviderRegistry:
        return ProviderRegistry(self.providers)

    def endpoint_for(self, provider_id: str) -> ProviderEndpointMetadata | None:
        return next(
            (item for item in self.endpoints if item.provider_id == provider_id),
            None,
        )


@dataclass(frozen=True)
class ProviderCatalogAssignment:
    catalog_version_id: UUID
    assignment_version: int
    assigned_at: datetime

    def __post_init__(self) -> None:
        if self.assignment_version < 1:
            raise ValueError("catalog assignment_version must be positive")
        _aware(self.assigned_at, "assigned_at")


@dataclass(frozen=True)
class EffectiveProviderCatalog:
    version: ProviderCatalogVersion
    assignment: ProviderCatalogAssignment

    @property
    def registry(self) -> ProviderRegistry:
        return self.version.registry()


@dataclass(frozen=True)
class ProviderCatalogAuditEvent:
    action: str
    catalog_version_id: UUID
    version_number: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.action not in {"CATALOG_PUBLISHED", "CATALOG_ACTIVATED"}:
            raise ValueError("unknown provider catalog audit action")
        if self.version_number < 1:
            raise ValueError("audit version_number must be positive")
        _aware(self.occurred_at, "occurred_at")


class ProviderCatalogAuditSink(Protocol):
    async def record(self, event: ProviderCatalogAuditEvent) -> None: ...


class ProviderCatalogRepository(Protocol):
    async def list_versions(self) -> tuple[ProviderCatalogVersion, ...]: ...

    async def get_effective(self) -> EffectiveProviderCatalog | None: ...

    async def publish_and_activate(
        self,
        *,
        version: ProviderCatalogVersion,
        assignment: ProviderCatalogAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveProviderCatalog: ...


class InMemoryProviderCatalogRepository:
    def __init__(self) -> None:
        self._versions: dict[UUID, ProviderCatalogVersion] = {}
        self._assignment: ProviderCatalogAssignment | None = None

    async def list_versions(self) -> tuple[ProviderCatalogVersion, ...]:
        return tuple(
            sorted(self._versions.values(), key=lambda item: item.version_number)
        )

    async def get_effective(self) -> EffectiveProviderCatalog | None:
        if self._assignment is None:
            return None
        version = self._versions.get(self._assignment.catalog_version_id)
        if version is None:
            raise RuntimeError("provider catalog assignment references missing version")
        return EffectiveProviderCatalog(version=version, assignment=self._assignment)

    async def publish_and_activate(
        self,
        *,
        version: ProviderCatalogVersion,
        assignment: ProviderCatalogAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveProviderCatalog:
        if version.catalog_version_id != assignment.catalog_version_id:
            raise ValueError("assignment must reference published catalog version")
        if version.catalog_version_id in self._versions:
            raise ValueError("provider catalog version already exists")
        if any(
            item.version_number == version.version_number
            for item in self._versions.values()
        ):
            raise ValueError("provider catalog version number already exists")

        current = self._assignment
        if current is None:
            if expected_assignment_version is not None:
                raise ValueError("provider catalog assignment version conflict")
            if assignment.assignment_version != 1:
                raise ValueError("initial catalog assignment_version must be 1")
        else:
            if current.assignment_version != expected_assignment_version:
                raise ValueError("provider catalog assignment version conflict")
            if assignment.assignment_version != current.assignment_version + 1:
                raise ValueError("catalog assignment_version must increment by one")

        self._versions[version.catalog_version_id] = version
        self._assignment = assignment
        return EffectiveProviderCatalog(version=version, assignment=assignment)


class InMemoryProviderCatalogAuditSink:
    def __init__(self) -> None:
        self.events: list[ProviderCatalogAuditEvent] = []

    async def record(self, event: ProviderCatalogAuditEvent) -> None:
        self.events.append(event)


class ProviderCatalogAdminService:
    def __init__(
        self,
        *,
        repository: ProviderCatalogRepository,
        audit: ProviderCatalogAuditSink,
    ) -> None:
        self._repository = repository
        self._audit = audit

    async def publish_and_activate(
        self,
        *,
        providers: tuple[ProviderSpec, ...],
        endpoints: tuple[ProviderEndpointMetadata, ...],
        occurred_at: datetime,
    ) -> EffectiveProviderCatalog:
        _aware(occurred_at, "occurred_at")
        current = await self._repository.get_effective()
        versions = await self._repository.list_versions()
        next_number = 1 + max(
            (item.version_number for item in versions),
            default=0,
        )
        version = ProviderCatalogVersion(
            catalog_version_id=uuid7(),
            version_number=next_number,
            providers=providers,
            endpoints=endpoints,
            created_at=occurred_at,
        )
        expected = None if current is None else current.assignment.assignment_version
        assignment = ProviderCatalogAssignment(
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
            ProviderCatalogAuditEvent(
                action="CATALOG_PUBLISHED",
                catalog_version_id=version.catalog_version_id,
                version_number=version.version_number,
                occurred_at=occurred_at,
            )
        )
        await self._audit.record(
            ProviderCatalogAuditEvent(
                action="CATALOG_ACTIVATED",
                catalog_version_id=version.catalog_version_id,
                version_number=version.version_number,
                occurred_at=occurred_at,
            )
        )
        return effective

    async def resolve_effective(self) -> EffectiveProviderCatalog:
        effective = await self._repository.get_effective()
        if effective is None:
            raise LookupError("no effective provider catalog")
        return effective
