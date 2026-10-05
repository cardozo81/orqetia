"""Test-only authorization oracle for #12 explicit target revalidation."""

from __future__ import annotations

from dataclasses import dataclass

from tests.contracts.tenancy_target_reference import ClientEnvelope, resolve_selection


@dataclass(frozen=True)
class Principal:
    principal_type: str
    tenant_id: str
    client_id: str
    scopes: frozenset[str]


def authorize_task_create(
    *,
    principal: Principal,
    envelope: ClientEnvelope,
    request: dict | None,
):
    if principal.principal_type != "SERVICE_CLIENT":
        raise PermissionError("service-client authorization contract required")
    if "tasks:write" not in principal.scopes:
        raise PermissionError("tasks:write required")

    execution = (request or {}).get("execution")
    explicit = bool(execution and execution.get("mode") == "EXPLICIT_TARGET")
    if explicit and "tasks:target" not in principal.scopes:
        raise PermissionError("tasks:target required")

    return resolve_selection(
        request,
        authenticated_tenant_id=principal.tenant_id,
        authenticated_client_id=principal.client_id,
        envelope=envelope,
    )


def client_task_view(
    *,
    task_id: str,
    requested_execution_mode: str,
    requested_target: dict | None,
    effective_target: dict | None,
    attempt_id: str | None,
) -> dict[str, object]:
    """Explicit allowlist; finance/provider-secret fields cannot enter this DTO."""
    return {
        "task_id": task_id,
        "requested_execution_mode": requested_execution_mode,
        "requested_target": requested_target,
        "effective_target": effective_target,
        "attempt_id": attempt_id,
    }


def authorize_backoffice_action(principal: Principal, privilege: str) -> None:
    del privilege
    if principal.principal_type == "SERVICE_CLIENT":
        raise PermissionError("service client cannot use Backoffice privileges")
