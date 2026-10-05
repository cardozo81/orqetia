"""Test-only oracle for #21 tenant/client execution-target envelope."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    AUTO = "AUTO"
    EXPLICIT_TARGET = "EXPLICIT_TARGET"


@dataclass(frozen=True)
class Target:
    provider_id: str
    model_id: str
    reasoning_profile: str


@dataclass(frozen=True)
class ClientEnvelope:
    tenant_id: str
    client_id: str
    policy_version_id: str
    explicit_target_enabled: bool
    allowed_providers: frozenset[str]
    allowed_models: frozenset[tuple[str, str]]
    allowed_profiles: frozenset[tuple[str, str, str]]
    default_model_by_provider: dict[str, str]
    default_profile_by_model: dict[tuple[str, str], str]
    max_cycles: int
    timeout_seconds: int


@dataclass(frozen=True)
class TaskSelectionSnapshot:
    policy_version_id: str
    requested_execution_mode: Mode
    requested_target: Target | None
    effective_target: Target | None
    max_cycles: int
    timeout_seconds: int


def resolve_selection(
    request: dict | None,
    *,
    authenticated_tenant_id: str,
    authenticated_client_id: str,
    envelope: ClientEnvelope,
) -> TaskSelectionSnapshot:
    if authenticated_tenant_id != envelope.tenant_id or authenticated_client_id != envelope.client_id:
        raise PermissionError("ownership mismatch")

    request = dict(request or {})
    forbidden_admin_fields = {
        "max_cycles",
        "retry",
        "delay",
        "timeout",
        "timeout_seconds",
        "quota",
        "rate_limit",
        "allowed_providers",
    }
    if forbidden_admin_fields.intersection(request):
        raise PermissionError("administrative execution controls are not client-selectable")

    execution = request.get("execution")
    if execution is None:
        return TaskSelectionSnapshot(
            policy_version_id=envelope.policy_version_id,
            requested_execution_mode=Mode.AUTO,
            requested_target=None,
            effective_target=None,
            max_cycles=envelope.max_cycles,
            timeout_seconds=envelope.timeout_seconds,
        )

    if execution.get("mode") != Mode.EXPLICIT_TARGET.value:
        raise ValueError("unsupported execution mode")
    if not envelope.explicit_target_enabled:
        raise PermissionError("explicit target disabled")

    target = execution.get("target") or {}
    provider = str(target.get("provider") or "").strip().upper()
    if not provider or provider not in envelope.allowed_providers:
        raise PermissionError("provider not allowed")

    requested_model = target.get("model")
    model = str(requested_model or envelope.default_model_by_provider.get(provider) or "").strip()
    if not model or (provider, model) not in envelope.allowed_models:
        raise PermissionError("model not allowed")

    requested_profile = target.get("reasoning_profile")
    profile = str(
        requested_profile
        or envelope.default_profile_by_model.get((provider, model))
        or ""
    ).strip()
    if not profile or (provider, model, profile) not in envelope.allowed_profiles:
        raise PermissionError("profile not allowed")

    requested = Target(
        provider_id=provider,
        model_id=str(requested_model or ""),
        reasoning_profile=str(requested_profile or ""),
    )
    effective = Target(provider, model, profile)

    return TaskSelectionSnapshot(
        policy_version_id=envelope.policy_version_id,
        requested_execution_mode=Mode.EXPLICIT_TARGET,
        requested_target=requested,
        effective_target=effective,
        max_cycles=envelope.max_cycles,
        timeout_seconds=envelope.timeout_seconds,
    )
