from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
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
from orqetia.infrastructure.http.execution_runtime import (
    ClientModelView,
    ClientProviderView,
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
        del code, state, transaction_token
        return self.context


class _Status:
    value = "ACTIVE"
    terminal = False


class _Execution:
    def __init__(self) -> None:
        self.scopes = []
        self.session_id = uuid7()

    async def create_session(self, **kwargs):
        self.scopes.append(kwargs["scope"])
        return SimpleNamespace(session_id=self.session_id)

    async def list_providers(self, *, scope):
        self.scopes.append(scope)
        return (
            ClientProviderView(
                provider_id="safe-provider",
                provider_name="Safe Provider",
                capabilities=("TEXT",),
            ),
        )

    async def list_models(self, *, scope, provider_id=None):
        del provider_id
        self.scopes.append(scope)
        return (
            ClientModelView(
                provider_id="safe-provider",
                model_id="safe-model",
                model_name="Safe Model",
                capabilities=("TEXT",),
                reasoning_profiles=("standard",),
            ),
        )


def _portal():
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
            subject="portal-execution-user",
            occurred_at=NOW,
        )
        await authorization.add_membership(
            identity_id=identity.identity_id,
            tenant_id=tenant_id,
            client_id=client_id,
            roles=(CustomerRole.DEVELOPER,),
            occurred_at=NOW,
        )
        return identity

    identity = asyncio.run(prepare())
    execution = _Execution()
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
        execution=execution,
    )
    client = TestClient(
        app,
        base_url="https://portal.example",
        follow_redirects=False,
    )
    client.get("/portal/login")
    callback = client.get("/portal/callback?code=code&state=state")
    assert callback.status_code == 303
    return client, execution, tenant_id, client_id


def test_portal_execution_scope_ignores_browser_owner_fields() -> None:
    client, execution, tenant_id, client_id = _portal()
    csrf = client.cookies.get("__Host-orqetia-portal-csrf")
    response = client.post(
        "/portal/sessions",
        headers={
            "origin": "https://portal.example",
            "sec-fetch-site": "same-origin",
        },
        data={
            "csrf_token": csrf,
            "idempotency_key": "portal-session-1",
            "external_reference": "portal",
            "tenant_id": str(uuid7()),
            "client_id": str(uuid7()),
        },
    )
    assert response.status_code == 303
    scope = execution.scopes[-1]
    assert scope.tenant_id == tenant_id
    assert scope.client_id == client_id


def test_portal_provider_page_renders_only_client_safe_catalog_fields() -> None:
    client, execution, tenant_id, client_id = _portal()
    response = client.get("/portal/providers")
    assert response.status_code == 200
    assert "Safe Provider" in response.text
    assert "Safe Model" in response.text
    assert "provider_account" not in response.text
    assert "credential" not in response.text.lower()
    assert "provider cost" not in response.text.lower()
    assert "currency" not in response.text.lower()
    assert execution.scopes[-1].tenant_id == tenant_id
    assert execution.scopes[-1].client_id == client_id
