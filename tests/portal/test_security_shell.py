from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid7

from fastapi.testclient import TestClient

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
        assert code == "code"
        assert state == "state"
        assert transaction_token == "transaction"
        return self.context


def _client(role: CustomerRole) -> TestClient:
    repository = InMemoryCustomerIdentityRepository()
    authorization = CustomerAuthorizationService(
        repository=repository,
        owner_resolver=_OwnerResolver(),
        audit=InMemoryCustomerAuthzAuditSink(),
    )

    async def prepare():
        identity = await authorization.create_identity(
            issuer="https://issuer.example",
            subject="portal-user",
            occurred_at=NOW,
        )
        await authorization.add_membership(
            identity_id=identity.identity_id,
            tenant_id=uuid7(),
            client_id=uuid7(),
            roles=(role,),
            occurred_at=NOW,
        )
        return identity

    import asyncio

    identity = asyncio.run(prepare())
    sessions = CustomerPortalWebSessionService(
        store=InMemoryCustomerPortalWebSessionStore(),
        authorization=authorization,
    )
    app = create_customer_portal_app(
        oidc=_Oidc(
            HumanAuthenticationContext(
                issuer=identity.issuer,
                subject=identity.subject,
                authenticated_at=NOW,
                mfa_satisfied=False,
                amr=("pwd",),
                acr=None,
            )
        ),
        authorization=authorization,
        sessions=sessions,
        allowed_origin="https://portal.example",
        enable_hsts=True,
    )
    return TestClient(
        app,
        base_url="https://portal.example",
        follow_redirects=False,
    )


def _login(client: TestClient) -> None:
    start = client.get("/portal/login")
    assert start.status_code == 302
    callback = client.get("/portal/callback?code=code&state=state")
    assert callback.status_code == 303
    assert callback.headers["location"] == "/portal"


def test_customer_portal_security_shell_and_viewer_navigation() -> None:
    client = _client(CustomerRole.VIEWER)
    _login(client)

    response = client.get("/portal")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"].startswith(
        "max-age=31536000"
    )
    body = response.text
    assert "Sessions" in body
    assert "Tasks" in body
    assert "Providers &amp; Models" in body
    assert "Documentation" in body
    assert "API Credentials" not in body
    assert "Usage" not in body
    assert "Token Estimates" not in body
    assert "Backoffice" not in body
    assert "provider cost" not in body.lower()
    assert "currency" not in body.lower()
    assert "pricing" not in body.lower()


def test_customer_portal_rejects_cross_origin_mutation() -> None:
    client = _client(CustomerRole.OWNER)
    _login(client)

    response = client.post(
        "/portal/logout",
        headers={
            "origin": "https://evil.example",
            "sec-fetch-site": "cross-site",
        },
        data={"csrf_token": "irrelevant"},
    )
    assert response.status_code == 200
    assert "Unauthorized" in response.text
