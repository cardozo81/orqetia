"""PostgreSQL repository for immutable administrative provider catalogs."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.providers import (
    AdapterResolution,
    ProviderCapability,
    ProviderModelSpec,
    ProviderSpec,
    ReasoningProfileSpec,
)

from .provider_catalog_tables import (
    provider_catalog_assignment,
    provider_catalog_versions,
)
from .provider_catalogs import (
    EffectiveProviderCatalog,
    ProviderCatalogAssignment,
    ProviderCatalogRepository,
    ProviderCatalogVersion,
    ProviderEndpointMetadata,
)

SessionFactory = async_sessionmaker[AsyncSession]


def _catalog_json(providers: tuple[ProviderSpec, ...]) -> list[dict[str, object]]:
    return [
        {
            "provider_id": provider.provider_id,
            "display_name": provider.display_name,
            "default_model_id": provider.default_model_id,
            "approved": provider.approved,
            "auto_eligible": provider.auto_eligible,
            "explicit_eligible": provider.explicit_eligible,
            "models": [
                {
                    "model_id": model.model_id,
                    "adapter": {
                        "adapter_key": model.adapter.adapter_key,
                        "protocol_version": model.adapter.protocol_version,
                    },
                    "offered_capabilities": sorted(
                        capability.value
                        for capability in model.offered_capabilities
                    ),
                    "approved_capabilities": sorted(
                        capability.value
                        for capability in model.approved_capabilities
                    ),
                    "reasoning_profiles": [
                        {
                            "profile_id": profile.profile_id,
                            "approved": profile.approved,
                            "auto_eligible": profile.auto_eligible,
                            "explicit_eligible": profile.explicit_eligible,
                        }
                        for profile in model.reasoning_profiles
                    ],
                    "default_reasoning_profile": model.default_reasoning_profile,
                    "approved": model.approved,
                    "auto_eligible": model.auto_eligible,
                    "explicit_eligible": model.explicit_eligible,
                }
                for model in provider.models
            ],
        }
        for provider in providers
    ]


def _catalog_from_json(value: object) -> tuple[ProviderSpec, ...]:
    if not isinstance(value, list):
        raise ValueError("persisted provider catalog must be an array")
    providers: list[ProviderSpec] = []
    for raw_provider in value:
        if not isinstance(raw_provider, dict):
            raise ValueError("persisted provider catalog item must be an object")
        models_raw = raw_provider.get("models")
        if not isinstance(models_raw, list):
            raise ValueError("persisted provider models must be an array")
        models: list[ProviderModelSpec] = []
        for raw_model in models_raw:
            if not isinstance(raw_model, dict):
                raise ValueError("persisted provider model must be an object")
            adapter_raw = raw_model.get("adapter")
            profiles_raw = raw_model.get("reasoning_profiles")
            if not isinstance(adapter_raw, dict) or not isinstance(profiles_raw, list):
                raise ValueError("persisted provider model metadata is invalid")
            profiles = tuple(
                ReasoningProfileSpec(
                    profile_id=str(raw_profile["profile_id"]),
                    approved=bool(raw_profile["approved"]),
                    auto_eligible=bool(raw_profile["auto_eligible"]),
                    explicit_eligible=bool(raw_profile["explicit_eligible"]),
                )
                for raw_profile in profiles_raw
                if isinstance(raw_profile, dict)
            )
            if len(profiles) != len(profiles_raw):
                raise ValueError("persisted reasoning profile must be an object")
            offered_raw = raw_model.get("offered_capabilities")
            approved_raw = raw_model.get("approved_capabilities")
            if not isinstance(offered_raw, list) or not isinstance(approved_raw, list):
                raise ValueError("persisted provider capabilities must be arrays")
            models.append(
                ProviderModelSpec(
                    model_id=str(raw_model["model_id"]),
                    adapter=AdapterResolution(
                        adapter_key=str(adapter_raw["adapter_key"]),
                        protocol_version=str(adapter_raw["protocol_version"]),
                    ),
                    offered_capabilities=frozenset(
                        ProviderCapability(str(item)) for item in offered_raw
                    ),
                    approved_capabilities=frozenset(
                        ProviderCapability(str(item)) for item in approved_raw
                    ),
                    reasoning_profiles=profiles,
                    default_reasoning_profile=cast(
                        str | None,
                        raw_model.get("default_reasoning_profile"),
                    ),
                    approved=bool(raw_model["approved"]),
                    auto_eligible=bool(raw_model["auto_eligible"]),
                    explicit_eligible=bool(raw_model["explicit_eligible"]),
                )
            )
        providers.append(
            ProviderSpec(
                provider_id=str(raw_provider["provider_id"]),
                display_name=str(raw_provider["display_name"]),
                models=tuple(models),
                default_model_id=cast(str | None, raw_provider.get("default_model_id")),
                approved=bool(raw_provider["approved"]),
                auto_eligible=bool(raw_provider["auto_eligible"]),
                explicit_eligible=bool(raw_provider["explicit_eligible"]),
            )
        )
    return tuple(providers)


def _endpoints_json(
    endpoints: tuple[ProviderEndpointMetadata, ...],
) -> list[dict[str, object]]:
    return [
        {
            "provider_id": item.provider_id,
            "base_url": item.base_url,
            "region": item.region,
        }
        for item in endpoints
    ]


def _endpoints_from_json(value: object) -> tuple[ProviderEndpointMetadata, ...]:
    if not isinstance(value, list):
        raise ValueError("persisted provider endpoints must be an array")
    output: list[ProviderEndpointMetadata] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("persisted provider endpoint must be an object")
        output.append(
            ProviderEndpointMetadata(
                provider_id=str(raw["provider_id"]),
                base_url=str(raw["base_url"]),
                region=cast(str | None, raw.get("region")),
            )
        )
    return tuple(output)


def _version_from_row(row: RowMapping) -> ProviderCatalogVersion:
    return ProviderCatalogVersion(
        catalog_version_id=cast(UUID, row["catalog_version_id"]),
        version_number=cast(int, row["version_number"]),
        providers=_catalog_from_json(row["catalog"]),
        endpoints=_endpoints_from_json(row["endpoints"]),
        created_at=row["created_at"],
    )


class PostgresProviderCatalogRepository(ProviderCatalogRepository):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def list_versions(self) -> tuple[ProviderCatalogVersion, ...]:
        statement = sa.select(provider_catalog_versions).order_by(
            provider_catalog_versions.c.version_number
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_version_from_row(row) for row in rows)

    async def get_effective(self) -> EffectiveProviderCatalog | None:
        statement = (
            sa.select(
                provider_catalog_assignment,
                provider_catalog_versions,
            )
            .join(
                provider_catalog_versions,
                provider_catalog_versions.c.catalog_version_id
                == provider_catalog_assignment.c.catalog_version_id,
            )
            .where(provider_catalog_assignment.c.assignment_key == "GLOBAL")
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        version = _version_from_row(row)
        return EffectiveProviderCatalog(
            version=version,
            assignment=ProviderCatalogAssignment(
                catalog_version_id=cast(UUID, row["catalog_version_id"]),
                assignment_version=cast(int, row["assignment_version"]),
                assigned_at=row["assigned_at"],
            ),
        )

    async def publish_and_activate(
        self,
        *,
        version: ProviderCatalogVersion,
        assignment: ProviderCatalogAssignment,
        expected_assignment_version: int | None,
    ) -> EffectiveProviderCatalog:
        async with self._sessions.begin() as database:
            current = (
                await database.execute(
                    sa.select(provider_catalog_assignment)
                    .where(provider_catalog_assignment.c.assignment_key == "GLOBAL")
                    .with_for_update()
                )
            ).mappings().one_or_none()

            if current is None:
                if expected_assignment_version is not None:
                    raise ValueError("provider catalog assignment version conflict")
                if assignment.assignment_version != 1:
                    raise ValueError("initial catalog assignment_version must be 1")
            else:
                current_version = cast(int, current["assignment_version"])
                if current_version != expected_assignment_version:
                    raise ValueError("provider catalog assignment version conflict")
                if assignment.assignment_version != current_version + 1:
                    raise ValueError("catalog assignment_version must increment by one")

            await database.execute(
                sa.insert(provider_catalog_versions).values(
                    catalog_version_id=version.catalog_version_id,
                    version_number=version.version_number,
                    catalog=_catalog_json(version.providers),
                    endpoints=_endpoints_json(version.endpoints),
                    created_at=version.created_at,
                )
            )

            assignment_values = {
                "assignment_key": "GLOBAL",
                "catalog_version_id": assignment.catalog_version_id,
                "assignment_version": assignment.assignment_version,
                "assigned_at": assignment.assigned_at,
            }
            if current is None:
                await database.execute(
                    sa.insert(provider_catalog_assignment).values(**assignment_values)
                )
            else:
                await database.execute(
                    sa.update(provider_catalog_assignment)
                    .where(provider_catalog_assignment.c.assignment_key == "GLOBAL")
                    .values(**assignment_values)
                )

        return EffectiveProviderCatalog(version=version, assignment=assignment)



def decode_provider_catalog(value: object) -> tuple[ProviderSpec, ...]:
    """Decode the canonical non-secret administrative provider catalog JSON shape."""
    return _catalog_from_json(value)


def decode_provider_endpoints(value: object) -> tuple[ProviderEndpointMetadata, ...]:
    """Decode the canonical non-secret administrative provider endpoint JSON shape."""
    return _endpoints_from_json(value)
