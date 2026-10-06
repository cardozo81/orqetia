from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from orqetia.identity import (
    BackofficeAuthorizationService,
    BackofficeBindingStatus,
    BackofficeRole,
    HumanAuthenticationContext,
    InMemoryBackofficeAuthzAuditSink,
    InMemoryBackofficeBindingRepository,
)

NOW = datetime(2026, 10, 5, 23, 50, tzinfo=UTC)
ISSUER = "https://idp.example.com"


def _service():
    repository = InMemoryBackofficeBindingRepository()
    audit = InMemoryBackofficeAuthzAuditSink()
    service = BackofficeAuthorizationService(
        repository=repository,
        audit=audit,
    )
    return service, repository, audit


@pytest.mark.asyncio
async def test_unbound_identity_is_default_deny() -> None:
    service, _repository, _audit = _service()
    context = HumanAuthenticationContext(
        issuer=ISSUER,
        subject="subject-1",
        authenticated_at=NOW,
        mfa_satisfied=True,
        amr=("pwd", "webauthn"),
    )
    with pytest.raises(PermissionError, match="not bound"):
        await service.authenticate(context=context, occurred_at=NOW)


@pytest.mark.asyncio
async def test_roles_resolve_permissions_and_sensitive_action_requires_recent_mfa() -> None:
    service, _repository, audit = _service()
    binding = await service.create_binding(
        issuer=ISSUER,
        subject="subject-2",
        roles=(BackofficeRole.PROVIDER_OPERATOR,),
        occurred_at=NOW,
    )
    context = HumanAuthenticationContext(
        issuer=ISSUER,
        subject="subject-2",
        authenticated_at=NOW,
        mfa_satisfied=True,
        amr=("pwd", "webauthn"),
        acr="urn:mfa",
    )
    principal = await service.authenticate(
        context=context,
        occurred_at=NOW + timedelta(minutes=1),
    )
    assert principal.binding_id == binding.binding_id
    assert "providers:admin" in principal.permissions
    assert "reports:read" in principal.permissions
    assert "tenancy:admin" not in principal.permissions

    service.require_permission(
        principal=principal,
        permission="providers:admin",
        occurred_at=NOW + timedelta(minutes=2),
    )
    with pytest.raises(PermissionError, match="permission denied"):
        service.require_permission(
            principal=principal,
            permission="tenancy:admin",
            occurred_at=NOW + timedelta(minutes=2),
        )
    assert audit.events[0].action == "BINDING_CREATE"


@pytest.mark.asyncio
async def test_disabled_binding_blocks_access_even_with_valid_idp_context() -> None:
    service, _repository, _audit = _service()
    binding = await service.create_binding(
        issuer=ISSUER,
        subject="subject-3",
        roles=(BackofficeRole.ADMIN,),
        occurred_at=NOW,
    )
    await service.set_status(
        binding_id=binding.binding_id,
        status=BackofficeBindingStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    context = HumanAuthenticationContext(
        issuer=ISSUER,
        subject="subject-3",
        authenticated_at=NOW + timedelta(seconds=1),
        mfa_satisfied=True,
    )
    with pytest.raises(PermissionError, match="disabled"):
        await service.authenticate(
            context=context,
            occurred_at=NOW + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_stale_or_missing_step_up_fails_closed() -> None:
    service, _repository, _audit = _service()
    await service.create_binding(
        issuer=ISSUER,
        subject="subject-4",
        roles=(BackofficeRole.ADMIN,),
        occurred_at=NOW,
    )
    no_mfa = await service.authenticate(
        context=HumanAuthenticationContext(
            issuer=ISSUER,
            subject="subject-4",
            authenticated_at=NOW,
            mfa_satisfied=False,
        ),
        occurred_at=NOW,
    )
    with pytest.raises(PermissionError, match="step-up"):
        service.require_permission(
            principal=no_mfa,
            permission="users:admin",
            occurred_at=NOW + timedelta(minutes=1),
        )

    fresh = await service.authenticate(
        context=HumanAuthenticationContext(
            issuer=ISSUER,
            subject="subject-4",
            authenticated_at=NOW,
            mfa_satisfied=True,
            amr=("webauthn",),
        ),
        occurred_at=NOW,
    )
    with pytest.raises(PermissionError, match="step-up"):
        service.require_permission(
            principal=fresh,
            permission="users:admin",
            occurred_at=NOW + timedelta(minutes=6),
        )


@pytest.mark.asyncio
async def test_role_changes_are_versioned_and_external_identity_is_immutable() -> None:
    service, repository, _audit = _service()
    binding = await service.create_binding(
        issuer=ISSUER,
        subject="subject-5",
        roles=(BackofficeRole.SUPPORT_READONLY,),
        occurred_at=NOW,
    )
    updated = await service.set_roles(
        binding_id=binding.binding_id,
        roles=(BackofficeRole.FINANCE_ANALYST,),
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert updated.version == 2
    assert updated.roles == (BackofficeRole.FINANCE_ANALYST,)

    with pytest.raises(ValueError, match="identity binding is immutable"):
        await repository.replace(
            replace(
                updated,
                subject="attacker-controlled",
                version=3,
                updated_at=NOW + timedelta(seconds=2),
            ),
            expected_version=2,
        )


@pytest.mark.asyncio
async def test_safe_role_matrix_never_grants_by_unknown_client_input() -> None:
    service, _repository, _audit = _service()
    with pytest.raises(ValueError):
        await service.create_binding(
            issuer=ISSUER,
            subject="subject-6",
            roles=("ROOT",),  # type: ignore[arg-type]
            occurred_at=NOW,
        )
