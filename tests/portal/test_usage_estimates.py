from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid7

from fastapi.testclient import TestClient

from orqetia.estimation import (
    EstimatedTechnicalUsage,
    EstimateResult,
    EstimateSubject,
    EstimateTarget,
    ReferenceScope,
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
from orqetia.read_models import ClientUsageBucket, ClientUsagePage
from orqetia.usage_accounting import NativeUsageQuantity


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


class _Usage:
    def __init__(self) -> None:
        self.owner = None

    async def read(
        self,
        *,
        tenant_id,
        client_id,
        period_from,
        period_to,
        cursor,
        limit,
    ):
        del period_from, period_to, cursor, limit
        self.owner = (tenant_id, client_id)
        start = datetime(2026, 10, 6, 12, tzinfo=UTC)
        return ClientUsagePage(
            items=(
                ClientUsageBucket(
                    period_start=start,
                    period_end=start + timedelta(hours=1),
                    input_tokens=10,
                    cached_input_tokens=2,
                    output_tokens=5,
                    reasoning_tokens=1,
                    total_tokens=15,
                    native_usage=(
                        NativeUsageQuantity(
                            name="requests",
                            unit="REQUEST",
                            quantity=Decimal("1"),
                        ),
                    ),
                ),
            ),
            next_cursor=None,
            as_of=start + timedelta(hours=1, minutes=1),
        )


class _Estimation:
    def __init__(self) -> None:
        self.subject = None
        self.spec = None

    async def estimate(self, *, subject, spec):
        self.subject = subject
        self.spec = spec
        target = spec.target or EstimateTarget(
            "safe-provider",
            "safe-model",
            "standard",
        )
        return EstimateResult(
            usage=EstimatedTechnicalUsage(
                input_tokens=4,
                cached_input_tokens=1,
                output_tokens=6,
                reasoning_tokens=2,
                total_tokens=10,
            ),
            requested_execution_mode=spec.execution_mode,
            effective_execution_mode=spec.execution_mode,
            effective_target=target,
            reference_scope=spec.reference_scope,
            estimation_method="SYNTHETIC",
            methodology_version="v1",
            benchmark_version="b1",
            as_of=datetime(2026, 10, 6, 12, tzinfo=UTC),
            sample_size=20,
            cohort_size=1,
            confidence="MEDIUM",
            fallback_available=None,
            limitations=(),
        )


def _portal():
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
            subject="usage-user",
            occurred_at=now,
        )
        await authorization.add_membership(
            identity_id=identity.identity_id,
            tenant_id=tenant_id,
            client_id=client_id,
            roles=(CustomerRole.DEVELOPER,),
            occurred_at=now,
        )
        return identity

    identity = asyncio.run(prepare())
    usage = _Usage()
    estimation = _Estimation()
    app = create_customer_portal_app(
        oidc=_Oidc(
            HumanAuthenticationContext(
                issuer=identity.issuer,
                subject=identity.subject,
                authenticated_at=now,
                mfa_satisfied=False,
                amr=("pwd",),
                acr=None,
            )
        ),
        authorization=authorization,
        sessions=CustomerPortalWebSessionService(
            store=InMemoryCustomerPortalWebSessionStore(),
            authorization=authorization,
        ),
        allowed_origin="https://portal.example",
        usage=usage,
        estimation=estimation,
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
    return client, usage, estimation, tenant_id, client_id


def test_usage_page_is_owner_scoped_and_non_financial() -> None:
    client, usage, _estimation, tenant_id, client_id = _portal()
    response = client.get("/portal/usage")
    assert response.status_code == 200
    assert usage.owner == (tenant_id, client_id)
    assert "15" in response.text
    for forbidden in (
        "provider_account",
        "provider credential",
        "currency",
        "pricing",
        "client_charge",
    ):
        assert forbidden not in response.text.lower()


def test_estimate_uses_membership_owner_and_client_safe_payload() -> None:
    client, _usage, estimation, tenant_id, client_id = _portal()
    csrf = client.cookies.get("__Host-orqetia-portal-csrf")
    response = client.post(
        "/portal/estimates",
        headers={
            "origin": "https://portal.example",
            "sec-fetch-site": "same-origin",
        },
        data={
            "csrf_token": csrf,
            "operation": "TASK_EXECUTION",
            "input_json": '{"input_text":"hello"}',
            "reference_scope": "CLIENT_ONLY",
            "tenant_id": str(uuid7()),
            "client_id": str(uuid7()),
        },
    )
    assert response.status_code == 200
    assert estimation.subject == EstimateSubject(
        tenant_id=str(tenant_id),
        client_id=str(client_id),
    )
    assert estimation.spec.reference_scope is ReferenceScope.CLIENT_ONLY
    assert "estimated_total_tokens" in response.text
    for forbidden in ("cost", "currency", "price", "charge", "credit"):
        assert forbidden not in response.text.lower()
