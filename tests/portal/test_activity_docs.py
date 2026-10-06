from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid7

from fastapi.testclient import TestClient

from orqetia.audit import InMemoryCustomerActivityStore
from orqetia.identity.backoffice_authz import HumanAuthenticationContext
from orqetia.identity.customer_authz import (
    CustomerAuthorizationService,
    CustomerRole,
    InMemoryCustomerAuthzAuditSink,
    InMemoryCustomerIdentityRepository,
)
from orqetia.identity.customer_web_sessions import (
    CustomerPortalWebSessionService,
    InMemoryCustomerPortalWebSessionStore,
)
from orqetia.infrastructure.customer_portal import (
    CustomerPortalOidcAuthorizationStart,
    create_customer_portal_app,
)


class _OwnerResolver:
    async def resolve_active_owner(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> object:
        del tenant_id, client_id
        return object()


class _Oidc:
    def __init__(self, context: HumanAuthenticationContext) -> None:
        self.context = context

    async def begin_login(self) -> CustomerPortalOidcAuthorizationStart:
        return CustomerPortalOidcAuthorizationStart(
            authorization_url="https://idp.example/authorize",
            transaction_token="transaction",
        )

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        del code, state, transaction_token
        return self.context


def test_portal_activity_is_owner_scoped_and_docs_expose_no_internal_finance() -> None:
    now = datetime.now(UTC)
    repository = InMemoryCustomerIdentityRepository()
    authorization = CustomerAuthorizationService(
        repository=repository,
        owner_resolver=_OwnerResolver(),
        audit=InMemoryCustomerAuthzAuditSink(),
    )
    tenant_id = uuid7()
    client_id = uuid7()

    async def prepare():
        identity = await authorization.create_identity(
            issuer="https://issuer.example",
            subject="activity-user",
            occurred_at=now,
        )
        await authorization.add_membership(
            identity_id=identity.identity_id,
            tenant_id=tenant_id,
            client_id=client_id,
            roles=(CustomerRole.OWNER,),
            occurred_at=now,
        )
        return identity

    identity = asyncio.run(prepare())
    activity = InMemoryCustomerActivityStore()
    sessions = CustomerPortalWebSessionService(
        store=InMemoryCustomerPortalWebSessionStore(),
        authorization=authorization,
    )
    app = create_customer_portal_app(
        oidc=_Oidc(
            HumanAuthenticationContext(
                issuer=identity.issuer,
                subject=identity.subject,
                authenticated_at=now,
                mfa_satisfied=True,
                amr=("pwd", "mfa"),
                acr="urn:mfa",
            )
        ),
        authorization=authorization,
        sessions=sessions,
        allowed_origin="https://portal.example",
        activity=activity,
    )
    client = TestClient(
        app,
        base_url="https://portal.example",
        follow_redirects=False,
    )
    client.get("/portal/login")
    assert client.get(
        "/portal/callback?code=code&state=state"
    ).status_code == 303

    async def seed():
        from orqetia.audit import activity_event

        principal = await authorization.authenticate(
            context=HumanAuthenticationContext(
                issuer=identity.issuer,
                subject=identity.subject,
                authenticated_at=now,
                mfa_satisfied=True,
                amr=("pwd", "mfa"),
                acr="urn:mfa",
            ),
            tenant_id=tenant_id,
            client_id=client_id,
            occurred_at=now,
        )
        await activity.record(
            activity_event(
                tenant_id=tenant_id,
                client_id=client_id,
                identity_id=principal.identity_id,
                membership_id=principal.membership_id,
                action="TASK_CREATE",
                occurred_at=now,
                resource_type="task",
                resource_id=str(uuid7()),
            )
        )
        await activity.record(
            activity_event(
                tenant_id=uuid7(),
                client_id=uuid7(),
                identity_id=uuid7(),
                membership_id=uuid7(),
                action="OTHER_CLIENT_EVENT",
                occurred_at=now,
            )
        )

    asyncio.run(seed())

    page = client.get("/portal/activity")
    assert page.status_code == 200
    assert "TASK_CREATE" in page.text
    assert "OTHER_CLIENT_EVENT" not in page.text

    docs = client.get("/portal/docs")
    assert docs.status_code == 200
    assert "/openapi.json" in docs.text
    assert "/v1" in docs.text
    for forbidden in (
        "provider cost",
        "currency",
        "provider account",
        "provider credential",
        "client_charge",
    ):
        assert forbidden not in docs.text.lower()
