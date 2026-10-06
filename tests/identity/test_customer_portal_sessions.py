from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest

from orqetia.identity.backoffice_authz import HumanAuthenticationContext
from orqetia.identity.customer_authz import (
    CustomerAuthorizationService,
    CustomerMembershipStatus,
    CustomerRole,
    InMemoryCustomerAuthzAuditSink,
    InMemoryCustomerIdentityRepository,
)
from orqetia.identity.customer_web_sessions import (
    CustomerPortalWebSessionService,
    InMemoryCustomerPortalWebSessionStore,
)
from orqetia.identity.web_sessions import WebSessionRejected

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


class _OwnerResolver:
    async def resolve_active_owner(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> object:
        del tenant_id, client_id
        return object()


@pytest.mark.asyncio
async def test_portal_session_binds_only_server_resolved_membership() -> None:
    repository = InMemoryCustomerIdentityRepository()
    authorization = CustomerAuthorizationService(
        repository=repository,
        owner_resolver=_OwnerResolver(),
        audit=InMemoryCustomerAuthzAuditSink(),
    )
    identity = await authorization.create_identity(
        issuer="https://issuer.example",
        subject="customer-user",
        occurred_at=NOW,
    )
    first = await authorization.add_membership(
        identity_id=identity.identity_id,
        tenant_id=uuid7(),
        client_id=uuid7(),
        roles=(CustomerRole.VIEWER,),
        occurred_at=NOW,
    )
    second = await authorization.add_membership(
        identity_id=identity.identity_id,
        tenant_id=uuid7(),
        client_id=uuid7(),
        roles=(CustomerRole.DEVELOPER,),
        occurred_at=NOW,
    )
    sessions = CustomerPortalWebSessionService(
        store=InMemoryCustomerPortalWebSessionStore(),
        authorization=authorization,
    )
    context = HumanAuthenticationContext(
        issuer=identity.issuer,
        subject=identity.subject,
        authenticated_at=NOW,
        mfa_satisfied=False,
        amr=("pwd",),
        acr=None,
    )
    established = await sessions.establish_identity(
        context=context,
        occurred_at=NOW,
    )

    pending = await sessions.authenticate_identity(
        session_token=established.session_token,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert not pending.record.membership_selected
    with pytest.raises(WebSessionRejected):
        await sessions.authenticate(
            session_token=established.session_token,
            occurred_at=NOW + timedelta(seconds=2),
        )

    selected = await sessions.bind_membership(
        session_token=established.session_token,
        membership_id=second.membership_id,
        occurred_at=NOW + timedelta(seconds=3),
    )
    assert selected.principal.membership_id == second.membership_id
    assert selected.principal.tenant_id == second.tenant_id
    assert selected.principal.client_id == second.client_id
    assert selected.record.tenant_id == second.tenant_id
    assert selected.record.client_id == second.client_id

    with pytest.raises(WebSessionRejected):
        await sessions.bind_membership(
            session_token=established.session_token,
            membership_id=first.membership_id,
            occurred_at=NOW + timedelta(seconds=4),
        )

    await authorization.set_membership_status(
        membership_id=second.membership_id,
        status=CustomerMembershipStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=5),
    )
    with pytest.raises(WebSessionRejected):
        await sessions.authenticate(
            session_token=established.session_token,
            occurred_at=NOW + timedelta(seconds=6),
        )


@pytest.mark.asyncio
async def test_portal_session_csrf_and_revocation_are_bound_to_server_state() -> None:
    repository = InMemoryCustomerIdentityRepository()
    authorization = CustomerAuthorizationService(
        repository=repository,
        owner_resolver=_OwnerResolver(),
        audit=InMemoryCustomerAuthzAuditSink(),
    )
    identity = await authorization.create_identity(
        issuer="https://issuer.example",
        subject="csrf-user",
        occurred_at=NOW,
    )
    membership = await authorization.add_membership(
        identity_id=identity.identity_id,
        tenant_id=uuid7(),
        client_id=uuid7(),
        roles=(CustomerRole.OWNER,),
        occurred_at=NOW,
    )
    sessions = CustomerPortalWebSessionService(
        store=InMemoryCustomerPortalWebSessionStore(),
        authorization=authorization,
    )
    established = await sessions.establish_identity(
        context=HumanAuthenticationContext(
            issuer=identity.issuer,
            subject=identity.subject,
            authenticated_at=NOW,
            mfa_satisfied=True,
            amr=("pwd", "mfa"),
            acr="urn:mfa",
        ),
        occurred_at=NOW,
    )
    selected = await sessions.bind_membership(
        session_token=established.session_token,
        membership_id=membership.membership_id,
        occurred_at=NOW + timedelta(seconds=1),
    )

    sessions.require_csrf(
        session=selected.record,
        csrf_token=established.csrf_token,
    )
    with pytest.raises(WebSessionRejected):
        sessions.require_csrf(
            session=selected.record,
            csrf_token="wrong",
        )

    await sessions.revoke(
        session_token=established.session_token,
        occurred_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(WebSessionRejected):
        await sessions.authenticate(
            session_token=established.session_token,
            occurred_at=NOW + timedelta(seconds=3),
        )
