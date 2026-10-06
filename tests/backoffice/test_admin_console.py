from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from orqetia.control_plane import (
    ExecutionPolicyAdminService,
    InMemoryCredentialAuditSink,
    InMemoryExecutionPolicyRepository,
    InMemoryExternalCapacityRepository,
    InMemoryProviderAccountRepository,
    InMemoryProviderCatalogAuditSink,
    InMemoryProviderCatalogRepository,
    InMemoryProviderCredentialRepository,
    InMemoryProviderPricingAuditSink,
    InMemoryProviderPricingCatalogRepository,
    InMemoryProviderSecretStore,
    InMemoryQuotaPolicyRepository,
    ProviderAccountService,
    ProviderCatalogAdminService,
    ProviderCredentialService,
    ProviderPricingAdminService,
    QuotaPolicyAdminService,
)
from orqetia.identity import (
    BackofficeAuthorizationService,
    BackofficeRole,
    BackofficeWebSessionService,
    ClientAccessCredentialService,
    HumanAuthenticationContext,
    InMemoryBackofficeAuthzAuditSink,
    InMemoryBackofficeBindingRepository,
    InMemoryBackofficeWebSessionStore,
    InMemoryClientCredentialStore,
)
from orqetia.infrastructure.backoffice import (
    BackofficeAdminServices,
    OidcAuthorizationStart,
    create_backoffice_app,
)
from orqetia.read_models import (
    BackofficeReportingService,
    InMemoryReportExportAuditSink,
    InMemoryReportRollupStore,
    OperationalFinancialIntelligenceService,
    ReportRollup,
)
from orqetia.tenancy import (
    InMemoryTenancyAuditSink,
    InMemoryTenantClientRepository,
    TenancyAdminService,
)

ISSUER = "https://idp.example.com"
NOW = datetime.now(UTC)


class FakeOidcBroker:
    def __init__(self, *, subject: str = "admin-subject") -> None:
        self.subject = subject

    async def begin_login(self) -> OidcAuthorizationStart:
        return OidcAuthorizationStart(
            authorization_url="https://idp.example.com/authorize",
            transaction_token="opaque-transaction",
        )

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        assert code == "valid-code"
        assert state == "valid-state"
        assert transaction_token == "opaque-transaction"
        return HumanAuthenticationContext(
            issuer=ISSUER,
            subject=self.subject,
            authenticated_at=datetime.now(UTC),
            mfa_satisfied=True,
            amr=("pwd", "webauthn"),
            acr="urn:mfa",
        )


async def _fixture(*, role: BackofficeRole = BackofficeRole.ADMIN):
    bindings = InMemoryBackofficeBindingRepository()
    authorization = BackofficeAuthorizationService(
        repository=bindings,
        audit=InMemoryBackofficeAuthzAuditSink(),
    )
    await authorization.create_binding(
        issuer=ISSUER,
        subject="admin-subject",
        roles=(role,),
        occurred_at=datetime.now(UTC),
    )
    sessions = BackofficeWebSessionService(
        store=InMemoryBackofficeWebSessionStore(),
        authorization=authorization,
    )

    tenancy_repository = InMemoryTenantClientRepository()
    tenancy = TenancyAdminService(
        repository=tenancy_repository,
        audit=InMemoryTenancyAuditSink(),
    )
    execution_policies = ExecutionPolicyAdminService(
        InMemoryExecutionPolicyRepository(),
        owner_resolver=tenancy,
    )
    quotas = QuotaPolicyAdminService(
        repository=InMemoryQuotaPolicyRepository(),
        owner_resolver=tenancy,
    )
    provider_catalog = ProviderCatalogAdminService(
        repository=InMemoryProviderCatalogRepository(),
        audit=InMemoryProviderCatalogAuditSink(),
    )
    provider_pricing = ProviderPricingAdminService(
        repository=InMemoryProviderPricingCatalogRepository(),
        audit=InMemoryProviderPricingAuditSink(),
    )
    provider_account_repository = InMemoryProviderAccountRepository()
    provider_credential_repository = InMemoryProviderCredentialRepository()
    provider_accounts = ProviderAccountService(
        accounts=provider_account_repository,
        credentials=provider_credential_repository,
        capacity=InMemoryExternalCapacityRepository(),
    )
    provider_credentials = ProviderCredentialService(
        secrets=InMemoryProviderSecretStore(),
        repository=provider_credential_repository,
        audit=InMemoryCredentialAuditSink(),
    )
    client_credentials = ClientAccessCredentialService(
        InMemoryClientCredentialStore()
    )
    rollups = InMemoryReportRollupStore()
    intelligence = OperationalFinancialIntelligenceService(rollups=rollups)
    reporting = BackofficeReportingService(
        store=rollups,
        export_audit=InMemoryReportExportAuditSink(),
    )
    services = BackofficeAdminServices(
        tenancy=tenancy,
        tenancy_repository=tenancy_repository,
        bindings=bindings,
        execution_policies=execution_policies,
        quota_policies=quotas,
        provider_catalog=provider_catalog,
        provider_pricing=provider_pricing,
        provider_accounts=provider_accounts,
        provider_account_repository=provider_account_repository,
        provider_credentials=provider_credentials,
        provider_credential_repository=provider_credential_repository,
        client_credentials=client_credentials,
        intelligence=intelligence,
        reporting=reporting,
    )
    app = create_backoffice_app(
        oidc=FakeOidcBroker(),
        authorization=authorization,
        sessions=sessions,
        allowed_origin="https://test",
        enable_hsts=True,
        admin=services,
    )
    return app, services, rollups


async def _login(client: httpx.AsyncClient) -> str:
    start = await client.get("/backoffice/login", follow_redirects=False)
    assert start.status_code == 302
    callback = await client.get(
        "/backoffice/callback?code=valid-code&state=valid-state",
        follow_redirects=False,
    )
    assert callback.status_code == 303
    csrf = client.cookies.get("__Host-orqetia-bo-csrf")
    assert csrf
    return csrf


async def _post(
    client: httpx.AsyncClient,
    path: str,
    csrf: str,
    data: dict[str, str],
) -> httpx.Response:
    return await client.post(
        path,
        headers={"Origin": "https://test", "Sec-Fetch-Site": "same-origin"},
        data={"csrf_token": csrf, **data},
        follow_redirects=False,
    )


def _catalog_json() -> str:
    return json.dumps(
        [
            {
                "provider_id": "alpha",
                "display_name": "Alpha Provider",
                "default_model_id": "alpha-1",
                "approved": True,
                "auto_eligible": True,
                "explicit_eligible": True,
                "models": [
                    {
                        "model_id": "alpha-1",
                        "adapter": {
                            "adapter_key": "alpha.adapter",
                            "protocol_version": "v1",
                        },
                        "offered_capabilities": [
                            "structured_output",
                            "reasoning",
                        ],
                        "approved_capabilities": [
                            "structured_output",
                            "reasoning",
                        ],
                        "reasoning_profiles": [
                            {
                                "profile_id": "standard",
                                "approved": True,
                                "auto_eligible": True,
                                "explicit_eligible": True,
                            }
                        ],
                        "default_reasoning_profile": "standard",
                        "approved": True,
                        "auto_eligible": True,
                        "explicit_eligible": True,
                    }
                ],
            }
        ]
    )


def _pricing_json() -> str:
    return json.dumps(
        [
            {
                "rule_id": "alpha-request",
                "version": 1,
                "provider_id": "alpha",
                "model_id": "alpha-1",
                "pricing_model": "PER_REQUEST",
                "currency": "USD",
                "effective_from": (NOW - timedelta(hours=1)).isoformat(),
                "effective_to": None,
                "reasoning_profile": "standard",
                "token_rates": None,
                "context_tiers": [],
                "time_windows": [],
                "request_rate": "0.01",
                "native_unit": None,
                "native_rate": None,
                "source_reference": "contract-alpha",
            }
        ]
    )


@pytest.mark.asyncio
async def test_admin_web_executes_control_plane_without_exposing_provider_secret() -> None:
    app, services, rollups = await _fixture()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
    ) as client:
        csrf = await _login(client)

        created_tenant = await _post(
            client,
            "/backoffice/tenants/create",
            csrf,
            {"display_name": "Acme"},
        )
        assert created_tenant.status_code == 303
        tenants = await services.tenancy_repository.list_tenants()
        assert len(tenants) == 1
        tenant_id = tenants[0].tenant_id

        created_client = await _post(
            client,
            "/backoffice/clients/create",
            csrf,
            {
                "tenant_id": str(tenant_id),
                "display_name": "Acme Production",
            },
        )
        assert created_client.status_code == 303
        clients = await services.tenancy_repository.list_clients(
            tenant_id=tenant_id
        )
        client_id = clients[0].client_id

        catalog = await _post(
            client,
            "/backoffice/providers/catalog/publish",
            csrf,
            {
                "catalog_json": _catalog_json(),
                "endpoints_json": json.dumps(
                    [
                        {
                            "provider_id": "alpha",
                            "base_url": "https://api.alpha.example/v1",
                            "region": "us",
                        }
                    ]
                ),
            },
        )
        assert catalog.status_code == 303

        policy = await _post(
            client,
            "/backoffice/policies/publish",
            csrf,
            {
                "tenant_id": str(tenant_id),
                "client_id": str(client_id),
                "max_cycles": "3",
                "max_attempts": "6",
                "cycle_delay_seconds": "1",
                "retry_after_cap_seconds": "120",
                "targets": "alpha|alpha-1|standard",
            },
        )
        assert policy.status_code == 303
        effective_policy = await services.execution_policies.resolve_effective(
            tenant_id=tenant_id,
            client_id=client_id,
        )
        assert effective_policy.version.max_cycles == 3

        quota = await _post(
            client,
            "/backoffice/quotas/publish",
            csrf,
            {
                "scope": "CLIENT",
                "tenant_id": str(tenant_id),
                "client_id": str(client_id),
                "metric": "REQUESTS",
                "limit": "100",
                "burst": "10",
                "period_seconds": "60",
                "enforcement": "HARD",
                "provider_id": "",
                "native_unit": "",
            },
        )
        assert quota.status_code == 303

        pricing = await _post(
            client,
            "/backoffice/providers/pricing/publish",
            csrf,
            {"rules_json": _pricing_json()},
        )
        assert pricing.status_code == 303

        account_response = await _post(
            client,
            "/backoffice/providers/accounts/create",
            csrf,
            {
                "provider_id": "alpha",
                "display_label": "Alpha primary",
                "priority": "10",
                "commercial_mode": "PREPAID",
                "commercial_tier": "STANDARD",
                "region": "us",
                "contract_reference": "contract-123",
            },
        )
        assert account_response.status_code == 303
        accounts = await services.provider_account_repository.list_for_provider(
            "alpha"
        )
        assert len(accounts) == 1
        account_id = accounts[0].provider_account_id

        provider_secret = "provider-secret-must-never-render"
        credential_response = await _post(
            client,
            "/backoffice/providers/credentials/create",
            csrf,
            {
                "provider_id": "alpha",
                "provider_account_id": str(account_id),
                "secret": provider_secret,
            },
        )
        assert credential_response.status_code == 303

        providers_page = await client.get("/backoffice/providers")
        assert providers_page.status_code == 200
        assert provider_secret not in providers_page.text
        assert "secret_reference" not in providers_page.text.lower()
        active_credentials = await services.provider_credential_repository.list_active(
            provider_account_id=account_id
        )
        assert active_credentials[0].fingerprint in providers_page.text

        issued = await _post(
            client,
            "/backoffice/client-credentials/issue",
            csrf,
            {
                "tenant_id": str(tenant_id),
                "client_id": str(client_id),
                "display_label": "CI",
                "scopes": "usage:read,tasks:write",
                "idempotency_key": "browser-issue-1",
            },
        )
        assert issued.status_code == 200
        assert "One-time secret" in issued.text
        assert "secret_hash" not in issued.text
        assert "secret_salt" not in issued.text

        await rollups.put(
            ReportRollup(
                rollup_id=__import__("uuid").uuid4(),
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                as_of=NOW + timedelta(minutes=1),
                tenant_id=tenant_id,
                client_id=client_id,
                provider_id="alpha",
                model_id="alpha-1",
                status="SUCCESS",
                requests=1,
                tasks=1,
                attempts=1,
                input_tokens=100,
                cached_input_tokens=0,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120,
                latency_ms_total=50,
                cycles=1,
                retries=0,
                estimated_cost=Decimal("0.01"),
                estimated_currency="USD",
                observed_cost=Decimal("0.01"),
                observed_currency="USD",
                unpriced_attempts=0,
            )
        )
        intelligence = await client.get(
            "/backoffice/intelligence",
            params={
                "tenant_id": str(tenant_id),
                "client_id": str(client_id),
                "provider_id": "alpha",
            },
        )
        assert intelligence.status_code == 200
        assert "USD 0.01" in intelligence.text
        assert "source_rows=1" in intelligence.text

        exported = await _post(
            client,
            "/backoffice/intelligence/export",
            csrf,
            {
                "tenant_id": str(tenant_id),
                "client_id": str(client_id),
                "provider_id": "alpha",
                "model_id": "",
            },
        )
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("application/json")
        assert '"observed_currency":"USD"' in exported.text


@pytest.mark.asyncio
async def test_admin_mutation_requires_role_and_never_trusts_navigation_access() -> None:
    app, services, _rollups = await _fixture(role=BackofficeRole.SUPPORT_READONLY)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
    ) as client:
        csrf = await _login(client)
        page = await client.get("/backoffice/tenants")
        assert page.status_code == 403

        mutation = await _post(
            client,
            "/backoffice/tenants/create",
            csrf,
            {"display_name": "Forbidden"},
        )
        assert mutation.status_code == 403
        assert await services.tenancy_repository.list_tenants() == ()
