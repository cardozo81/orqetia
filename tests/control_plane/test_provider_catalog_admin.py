from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orqetia.control_plane import (
    InMemoryProviderCatalogAuditSink,
    InMemoryProviderCatalogRepository,
    ProviderCatalogAdminService,
    ProviderEndpointMetadata,
)
from orqetia.providers import (
    AdapterResolution,
    ProviderCapability,
    ProviderModelSpec,
    ProviderRegistryError,
    ProviderSpec,
    ProviderTarget,
    ReasoningProfileSpec,
    RegistryFailureCode,
)

NOW = datetime(2026, 10, 6, 6, tzinfo=UTC)


def _providers() -> tuple[ProviderSpec, ...]:
    approved_model = ProviderModelSpec(
        model_id="alpha-1",
        adapter=AdapterResolution("alpha.adapter"),
        offered_capabilities=frozenset(
            {
                ProviderCapability.STRUCTURED_OUTPUT,
                ProviderCapability.REASONING,
            }
        ),
        approved_capabilities=frozenset(
            {
                ProviderCapability.STRUCTURED_OUTPUT,
                ProviderCapability.REASONING,
            }
        ),
        reasoning_profiles=(
            ReasoningProfileSpec("standard"),
            ReasoningProfileSpec("deep", auto_eligible=False),
        ),
        default_reasoning_profile="standard",
    )
    disabled_model = ProviderModelSpec(
        model_id="alpha-preview",
        adapter=AdapterResolution("alpha.preview.adapter"),
        offered_capabilities=frozenset({ProviderCapability.REASONING}),
        approved_capabilities=frozenset(),
        reasoning_profiles=(ReasoningProfileSpec("standard"),),
        default_reasoning_profile="standard",
        approved=False,
    )
    return (
        ProviderSpec(
            provider_id="alpha",
            display_name="Alpha",
            models=(approved_model, disabled_model),
            default_model_id="alpha-1",
        ),
        ProviderSpec(
            provider_id="disabled-provider",
            display_name="Disabled Provider",
            models=(),
            approved=False,
        ),
    )


@pytest.mark.asyncio
async def test_publish_versions_catalog_and_materializes_registry() -> None:
    repository = InMemoryProviderCatalogRepository()
    audit = InMemoryProviderCatalogAuditSink()
    service = ProviderCatalogAdminService(repository=repository, audit=audit)

    first = await service.publish_and_activate(
        providers=_providers(),
        endpoints=(
            ProviderEndpointMetadata(
                provider_id="alpha",
                base_url="https://api.alpha.example/v1",
                region="us",
            ),
        ),
        occurred_at=NOW,
    )
    second = await service.publish_and_activate(
        providers=(
            ProviderSpec(
                provider_id="alpha",
                display_name="Alpha",
                models=(_providers()[0].models[0],),
                default_model_id="alpha-1",
            ),
        ),
        endpoints=(
            ProviderEndpointMetadata(
                provider_id="alpha",
                base_url="https://api.alpha.example/v2",
            ),
        ),
        occurred_at=NOW + timedelta(seconds=1),
    )

    versions = await repository.list_versions()
    assert [item.version_number for item in versions] == [1, 2]
    assert first.version.version_number == 1
    assert second.assignment.assignment_version == 2
    assert second.version.endpoint_for("alpha").base_url.endswith("/v2")

    registry = second.registry
    resolved = registry.adapter_for(
        ProviderTarget("alpha", "alpha-1", "standard")
    )
    assert resolved.adapter_key == "alpha.adapter"
    assert [event.action for event in audit.events] == [
        "CATALOG_PUBLISHED",
        "CATALOG_ACTIVATED",
        "CATALOG_PUBLISHED",
        "CATALOG_ACTIVATED",
    ]


@pytest.mark.asyncio
async def test_unapproved_items_fail_closed_after_materialization() -> None:
    service = ProviderCatalogAdminService(
        repository=InMemoryProviderCatalogRepository(),
        audit=InMemoryProviderCatalogAuditSink(),
    )
    effective = await service.publish_and_activate(
        providers=_providers(),
        endpoints=(),
        occurred_at=NOW,
    )

    with pytest.raises(ProviderRegistryError) as caught:
        effective.registry.adapter_for(
            ProviderTarget("alpha", "alpha-preview", "standard")
        )
    assert caught.value.code is RegistryFailureCode.MODEL_NOT_APPROVED

    with pytest.raises(ProviderRegistryError) as provider_error:
        effective.registry.adapter_for(
            ProviderTarget("disabled-provider", "missing", "standard")
        )
    assert provider_error.value.code is RegistryFailureCode.PROVIDER_NOT_APPROVED


@pytest.mark.asyncio
async def test_defaults_capabilities_and_eligibility_preserve_registry_semantics() -> None:
    service = ProviderCatalogAdminService(
        repository=InMemoryProviderCatalogRepository(),
        audit=InMemoryProviderCatalogAuditSink(),
    )
    effective = await service.publish_and_activate(
        providers=_providers(),
        endpoints=(),
        occurred_at=NOW,
    )
    registry = effective.registry
    authorized = (
        ProviderTarget("alpha", "alpha-1", "standard"),
        ProviderTarget("alpha", "alpha-1", "deep"),
    )
    public = registry.public_metadata(authorized_targets=authorized)
    assert {item.reasoning_profile for item in public} == {"standard", "deep"}
    assert all(
        ProviderCapability.STRUCTURED_OUTPUT in item.capabilities
        for item in public
    )


def test_endpoint_metadata_is_admin_only_and_rejects_unsafe_urls() -> None:
    with pytest.raises(ValueError, match="https URL"):
        ProviderEndpointMetadata(
            provider_id="alpha",
            base_url="http://127.0.0.1:8080/private",
        )
    with pytest.raises(ValueError, match="userinfo"):
        ProviderEndpointMetadata(
            provider_id="alpha",
            base_url="https://user:secret@api.alpha.example/v1",
        )
    with pytest.raises(ValueError, match="query or fragment"):
        ProviderEndpointMetadata(
            provider_id="alpha",
            base_url="https://api.alpha.example/v1?token=secret",
        )


def test_endpoint_must_reference_known_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        from orqetia.control_plane import ProviderCatalogVersion
        from uuid import uuid7

        ProviderCatalogVersion(
            catalog_version_id=uuid7(),
            version_number=1,
            providers=_providers(),
            endpoints=(
                ProviderEndpointMetadata(
                    provider_id="not-in-catalog",
                    base_url="https://api.example.com",
                ),
            ),
            created_at=NOW,
        )
