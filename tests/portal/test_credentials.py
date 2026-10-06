from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid7

from fastapi.testclient import TestClient

from orqetia.identity import (
    ClientAccessCredentialService,
    InMemoryClientCredentialStore,
)
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


def _client(*, mfa: bool) -> TestClient:
    now = datetime.now(UTC)
    repository = InMemoryCustomerIdentityRepository()
    authorization = CustomerAuthorizationService(
        repository=repository,
        owner_resolver=_OwnerResolver(),
        audit=InMemoryCustomerAuthzAuditSink(),
    )

    async def prepare():
        identity = await authorization.create_identity(
            issuer="https://issuer.example",
            subject="credential-user",
            occurred_at=now,
        )
        await authorization.add_membership(
            identity_id=identity.identity_id,
            tenant_id=uuid7(),
            client_id=uuid7(),
            roles=(CustomerRole.DEVELOPER,),
            occurred_at=now,
        )
        return identity

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
                authenticated_at=now,
                mfa_satisfied=mfa,
                amr=("pwd", "mfa") if mfa else ("pwd",),
                acr="urn:mfa" if mfa else None,
            )
        ),
        authorization=authorization,
        sessions=sessions,
        allowed_origin="https://portal.example",
        credentials=ClientAccessCredentialService(
            InMemoryClientCredentialStore()
        ),
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
    return client


def _mutation_headers() -> dict[str, str]:
    return {
        "origin": "https://portal.example",
        "sec-fetch-site": "same-origin",
    }


def test_portal_credential_secret_is_one_time_and_scopes_are_bounded() -> None:
    client = _client(mfa=True)
    csrf = client.cookies.get("__Host-orqetia-portal-csrf")
    response = client.post(
        "/portal/credentials",
        headers=_mutation_headers(),
        data={
            "csrf_token": csrf,
            "idempotency_key": "issue-1",
            "display_label": "automation",
            "scopes": "sessions:read,tasks:read,tasks:write,tasks:target",
        },
    )
    assert response.status_code == 200
    assert "shown once" in response.text
    assert "oqt_" in response.text

    listing = client.get("/portal/credentials")
    assert listing.status_code == 200
    assert "automation" in listing.text
    assert "oqt_" not in listing.text
    assert "secret_hash" not in listing.text
    assert "secret_salt" not in listing.text

    denied = client.post(
        "/portal/credentials",
        headers=_mutation_headers(),
        data={
            "csrf_token": csrf,
            "idempotency_key": "issue-2",
            "display_label": "too-powerful",
            "scopes": "credentials:write",
        },
    )
    assert denied.status_code == 403


def test_portal_credential_mutation_requires_recent_mfa() -> None:
    client = _client(mfa=False)
    csrf = client.cookies.get("__Host-orqetia-portal-csrf")
    response = client.post(
        "/portal/credentials",
        headers=_mutation_headers(),
        data={
            "csrf_token": csrf,
            "idempotency_key": "issue-no-mfa",
            "display_label": "blocked",
            "scopes": "tasks:read",
        },
    )
    assert response.status_code == 403
