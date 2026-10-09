"""Synchronous worker candidates must satisfy registry-approved capabilities.

Delta #168: capability admission is server-side, not caller-supplied.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from orqetia.execution import ExecutionTargetSnapshot
from orqetia.infrastructure.processes.orchestration_candidates import (
    ControlPlaneOrchestrationCandidateResolver,
)
from orqetia.providers import (
    AdapterResolution,
    ProviderCapability,
    ProviderModelSpec,
    ProviderRegistry,
    ProviderSpec,
    ReasoningProfileSpec,
    RegistryEligibilityMode,
)


def _provider(name: str, *, offers_sync: bool, approves_sync: bool) -> ProviderSpec:
    base = frozenset({ProviderCapability.REASONING})
    offered = base | ({ProviderCapability.SYNCHRONOUS} if offers_sync else set())
    approved = base | ({ProviderCapability.SYNCHRONOUS} if approves_sync else set())
    return ProviderSpec(
        provider_id=name,
        display_name=name,
        default_model_id="model",
        models=(
            ProviderModelSpec(
                model_id="model",
                adapter=AdapterResolution(f"{name}.adapter"),
                offered_capabilities=offered,
                approved_capabilities=approved,
                reasoning_profiles=(ReasoningProfileSpec("standard"),),
                default_reasoning_profile="standard",
            ),
        ),
    )


class _Catalogs:
    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    async def get_effective(self) -> object:
        return SimpleNamespace(registry=self.registry)


class _Pricing:
    async def get_effective(self) -> None:
        return None


@pytest.mark.asyncio
async def test_worker_skips_models_without_approved_synchronous_capability() -> None:
    registry = ProviderRegistry((
        _provider("approved", offers_sync=True, approves_sync=True),
        _provider("unapproved", offers_sync=True, approves_sync=False),
        _provider("unsupported", offers_sync=False, approves_sync=False),
    ))
    resolver = ControlPlaneOrchestrationCandidateResolver(
        catalogs=_Catalogs(registry),
        pricing=_Pricing(),
    )
    authorized = tuple(
        ExecutionTargetSnapshot(provider, "model", "standard")
        for provider in ("approved", "unapproved", "unsupported")
    )
    candidates = await resolver.resolve_authorized(
        authorized_targets=authorized,
        mode=RegistryEligibilityMode.AUTO,
        occurred_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    assert [item.target.provider_id for item in candidates] == ["approved"]

    explicit = await resolver.resolve_authorized(
        authorized_targets=authorized,
        mode=RegistryEligibilityMode.EXPLICIT_TARGET,
        occurred_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    assert [item.target.provider_id for item in explicit] == ["approved"]
