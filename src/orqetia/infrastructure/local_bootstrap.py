"""Idempotent LOCAL/TEST bootstrap for operational Docker homologation."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.control_plane import (
    AuthorizedExecutionTarget,
    ExecutionPolicyAdminService,
    PostgresExecutionPolicyRepository,
    PostgresExternalCapacityRepository,
    PostgresProviderAccountRepository,
    PostgresProviderCatalogRepository,
    PostgresProviderCredentialRepository,
    PostgresProviderPricingCatalogRepository,
    PostgresQuotaPolicyRepository,
    ProviderAccountService,
    ProviderCatalogAdminService,
    ProviderCredentialService,
    ProviderPricingAdminService,
    ProviderSecretStore,
    QuotaEnforcementMode,
    QuotaMetric,
    QuotaPolicyAdminService,
    QuotaScope,
    SecretValue,
)
from orqetia.identity import (
    BackofficeAuthorizationService,
    BackofficeRole,
    ClientAccessCredential,
    ClientAccessCredentialService,
    ClientCredentialStatus,
    CustomerAuthorizationService,
    CustomerIdentity,
    CustomerMembership,
    CustomerRole,
    PostgresBackofficeBindingRepository,
    PostgresClientCredentialStore,
    PostgresCustomerIdentityRepository,
    StoredClientCredentialAuthenticator,
)
from orqetia.infrastructure.local_audit import LocalJsonlAuditSink
from orqetia.infrastructure.local_oidc import (
    LOCAL_LOGIN_TOKENS_PATH,
    LOCAL_OIDC_ISSUER,
    LOCAL_OIDC_PROFILES,
    LOCAL_OIDC_SIGNING_KEY_PATH,
)
from orqetia.providers import (
    ORQETIA_TEST_PROVIDER,
    AdapterResolution,
    ProviderCapability,
    ProviderModelSpec,
    ProviderSpec,
    ReasoningProfileSpec,
)
from orqetia.tenancy import (
    PostgresTenantClientRepository,
    ServiceClientRecord,
    TenancyAdminService,
    TenantRecord,
)
from orqetia.usage_accounting import PricingModel, PricingRule

SessionFactory = async_sessionmaker[AsyncSession]

_LOCAL_ROOT = Path("/var/lib/orqetia")
_BOOTSTRAP_DIR = _LOCAL_ROOT / "bootstrap"
_TOKEN_PATH = _BOOTSTRAP_DIR / "client-api-tokens.json"
_MANIFEST_PATH = _BOOTSTRAP_DIR / "manifest.json"
_PROVIDER_SECRET_ROOT = _LOCAL_ROOT / "provider-secrets"
_AUDIT_PATH = _LOCAL_ROOT / "audit" / "bootstrap.jsonl"

_FULL_SCOPES = (
    "catalog:read",
    "credentials:read",
    "credentials:write",
    "estimates:write",
    "sessions:read",
    "sessions:write",
    "tasks:cancel",
    "tasks:read",
    "tasks:target",
    "tasks:write",
    "usage:read",
)
_READ_SCOPES = (
    "catalog:read",
    "sessions:read",
    "tasks:read",
    "usage:read",
)


@dataclass(frozen=True)
class BootstrapResult:
    manifest_path: Path
    token_path: Path


def ensure_local_oidc_signing_key() -> Path:
    """Create the local IdP key once with private permissions."""

    path = LOCAL_OIDC_SIGNING_KEY_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if len(value.encode("utf-8")) < 32:
            raise RuntimeError("persisted local OIDC signing key is invalid")
        os.chmod(path, 0o600)
        return path

    value = secrets.token_urlsafe(48) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _atomic_private_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    ).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _read_tokens() -> dict[str, str]:
    try:
        raw = json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("local bootstrap token file is invalid") from error
    if not isinstance(raw, dict):
        raise RuntimeError("local bootstrap token file must be an object")
    output: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value:
            output[key] = value
    return output


async def _ensure_named_tenant(
    *,
    tenancy: TenancyAdminService,
    repository: PostgresTenantClientRepository,
    display_name: str,
    occurred_at: datetime,
) -> TenantRecord:
    matches = tuple(
        item
        for item in await repository.list_tenants()
        if item.display_name == display_name
    )
    if len(matches) > 1:
        raise RuntimeError(f"duplicate local tenant display name: {display_name}")
    if matches:
        return matches[0]
    return await tenancy.create_tenant(
        display_name=display_name,
        occurred_at=occurred_at,
    )


async def _ensure_named_client(
    *,
    tenancy: TenancyAdminService,
    repository: PostgresTenantClientRepository,
    tenant_id: UUID,
    display_name: str,
    occurred_at: datetime,
) -> ServiceClientRecord:
    matches = tuple(
        item
        for item in await repository.list_clients(tenant_id=tenant_id)
        if item.display_name == display_name
    )
    if len(matches) > 1:
        raise RuntimeError(f"duplicate local client display name: {display_name}")
    if matches:
        return matches[0]
    return await tenancy.create_client(
        tenant_id=tenant_id,
        display_name=display_name,
        occurred_at=occurred_at,
    )


async def _ensure_customer(
    *,
    authorization: CustomerAuthorizationService,
    repository: PostgresCustomerIdentityRepository,
    subject: str,
    tenant_id: UUID,
    client_id: UUID,
    roles: tuple[CustomerRole, ...],
    occurred_at: datetime,
) -> tuple[CustomerIdentity, CustomerMembership]:
    identity = await repository.get_by_external_identity(
        issuer=LOCAL_OIDC_ISSUER,
        subject=subject,
    )
    if identity is None:
        identity = await authorization.create_identity(
            issuer=LOCAL_OIDC_ISSUER,
            subject=subject,
            occurred_at=occurred_at,
        )
    membership = await repository.find_membership(
        identity_id=identity.identity_id,
        tenant_id=tenant_id,
        client_id=client_id,
    )
    if membership is None:
        membership = await authorization.add_membership(
            identity_id=identity.identity_id,
            tenant_id=tenant_id,
            client_id=client_id,
            roles=roles,
            occurred_at=occurred_at,
        )
    return identity, membership


def _local_provider() -> ProviderSpec:
    capabilities = frozenset(
        {
            ProviderCapability.SYNCHRONOUS,
            ProviderCapability.USAGE_REPORTING,
            ProviderCapability.REQUEST_PRICING,
        }
    )
    profile = (ReasoningProfileSpec(profile_id="standard"),)
    models = tuple(
        ProviderModelSpec(
            model_id=model_id,
            adapter=AdapterResolution(adapter_key="orqetia_test_provider"),
            offered_capabilities=capabilities,
            approved_capabilities=capabilities,
            reasoning_profiles=profile,
            default_reasoning_profile="standard",
        )
        for model_id in ("economy", "premium")
    )
    return ProviderSpec(
        provider_id=ORQETIA_TEST_PROVIDER,
        display_name="ORQETIA Deterministic Test Provider",
        models=models,
        default_model_id="economy",
    )


def _pricing_rules(occurred_at: datetime) -> tuple[PricingRule, ...]:
    return (
        PricingRule(
            rule_id="local-test-economy",
            version=1,
            provider_id=ORQETIA_TEST_PROVIDER,
            model_id="economy",
            reasoning_profile="standard",
            pricing_model=PricingModel.PER_REQUEST,
            currency="USD",
            request_rate=Decimal("0.001"),
            effective_from=occurred_at,
            source_reference="local://orqetia-test-provider/economy",
        ),
        PricingRule(
            rule_id="local-test-premium",
            version=1,
            provider_id=ORQETIA_TEST_PROVIDER,
            model_id="premium",
            reasoning_profile="standard",
            pricing_model=PricingModel.PER_REQUEST,
            currency="USD",
            request_rate=Decimal("0.010"),
            effective_from=occurred_at,
            source_reference="local://orqetia-test-provider/premium",
        ),
    )


async def _ensure_api_token(
    *,
    service: ClientAccessCredentialService,
    store: PostgresClientCredentialStore,
    tokens: dict[str, str],
    token_key: str,
    tenant_id: UUID,
    client_id: UUID,
    display_label: str,
    scopes: tuple[str, ...],
    occurred_at: datetime,
) -> ClientAccessCredential:
    authenticator = StoredClientCredentialAuthenticator(store=store)
    token = tokens.get(token_key)
    if token:
        try:
            principal = await authenticator.authenticate_bearer(token)
        except Exception:
            token = None
        else:
            if (
                principal.tenant_id == str(tenant_id)
                and principal.client_id == str(client_id)
                and principal.credential_id is not None
                and frozenset(scopes) <= principal.scopes
            ):
                credential_id = UUID(principal.credential_id)
                credential = await store.get(credential_id)
                if credential is not None:
                    return credential
            token = None

    owned = await service.list_owned(
        tenant_id=tenant_id,
        client_id=client_id,
    )
    active = next(
        (
            item
            for item in reversed(owned)
            if item.display_label == display_label
            and item.status is ClientCredentialStatus.ACTIVE
        ),
        None,
    )

    if active is None:
        result = await service.issue(
            tenant_id=tenant_id,
            client_id=client_id,
            display_label=display_label,
            scopes=scopes,
            idempotency_key=f"local-bootstrap-issue:{display_label}",
            occurred_at=occurred_at,
        )
        active = result.credential
        if result.secret is not None:
            token = result.secret.reveal_once()

    if token is None:
        result = await service.rotate(
            tenant_id=tenant_id,
            client_id=client_id,
            credential_id=active.credential_id,
            idempotency_key=(
                f"local-bootstrap-rotate:{active.credential_id}:"
                f"{active.key_version + 1}"
            ),
            occurred_at=occurred_at,
        )
        active = result.credential
        if result.secret is None:
            raise RuntimeError("local bootstrap could not recover client API token")
        token = result.secret.reveal_once()

    tokens[token_key] = token
    return active


async def bootstrap_local(
    session_factory: SessionFactory,
    *,
    secret_store: ProviderSecretStore,
    environment: str,
    occurred_at: datetime | None = None,
) -> BootstrapResult:
    """Create the minimal reproducible LOCAL/TEST homologation dataset."""

    normalized = environment.strip().lower()
    if normalized not in {"local", "test"}:
        raise RuntimeError("local bootstrap is restricted to LOCAL/TEST")
    ensure_local_oidc_signing_key()
    if not LOCAL_LOGIN_TOKENS_PATH.exists():
        _atomic_private_json(
            LOCAL_LOGIN_TOKENS_PATH,
            {profile.profile_id: secrets.token_urlsafe(32) for profile in LOCAL_OIDC_PROFILES},
        )
    now = occurred_at or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("bootstrap occurred_at must be timezone-aware")

    audit = LocalJsonlAuditSink(_AUDIT_PATH, environment=normalized)
    tenancy_repository = PostgresTenantClientRepository(session_factory)
    tenancy = TenancyAdminService(repository=tenancy_repository, audit=audit)

    tenant_a = await _ensure_named_tenant(
        tenancy=tenancy,
        repository=tenancy_repository,
        display_name="ORQETIA Local Tenant A",
        occurred_at=now,
    )
    client_a1 = await _ensure_named_client(
        tenancy=tenancy,
        repository=tenancy_repository,
        tenant_id=tenant_a.tenant_id,
        display_name="ORQETIA Local Client A1",
        occurred_at=now,
    )
    client_a2 = await _ensure_named_client(
        tenancy=tenancy,
        repository=tenancy_repository,
        tenant_id=tenant_a.tenant_id,
        display_name="ORQETIA Local Client A2",
        occurred_at=now,
    )
    tenant_b = await _ensure_named_tenant(
        tenancy=tenancy,
        repository=tenancy_repository,
        display_name="ORQETIA Local Tenant B",
        occurred_at=now,
    )
    client_b1 = await _ensure_named_client(
        tenancy=tenancy,
        repository=tenancy_repository,
        tenant_id=tenant_b.tenant_id,
        display_name="ORQETIA Local Client B1 — quota blocked",
        occurred_at=now,
    )

    backoffice_repository = PostgresBackofficeBindingRepository(session_factory)
    backoffice = BackofficeAuthorizationService(
        repository=backoffice_repository,
        audit=audit,
    )
    backoffice_binding = await backoffice_repository.get_by_identity(
        issuer=LOCAL_OIDC_ISSUER,
        subject="local-backoffice-admin",
    )
    if backoffice_binding is None:
        backoffice_binding = await backoffice.create_binding(
            issuer=LOCAL_OIDC_ISSUER,
            subject="local-backoffice-admin",
            roles=(BackofficeRole.ADMIN,),
            occurred_at=now,
        )

    customer_repository = PostgresCustomerIdentityRepository(session_factory)
    customer = CustomerAuthorizationService(
        repository=customer_repository,
        owner_resolver=tenancy,
        audit=audit,
    )
    viewer_identity, viewer_membership = await _ensure_customer(
        authorization=customer,
        repository=customer_repository,
        subject="local-client-viewer",
        tenant_id=tenant_a.tenant_id,
        client_id=client_a1.client_id,
        roles=(CustomerRole.VIEWER,),
        occurred_at=now,
    )
    operator_identity, operator_membership = await _ensure_customer(
        authorization=customer,
        repository=customer_repository,
        subject="local-client-operator",
        tenant_id=tenant_a.tenant_id,
        client_id=client_a1.client_id,
        roles=(CustomerRole.DEVELOPER,),
        occurred_at=now,
    )
    admin_identity, admin_membership = await _ensure_customer(
        authorization=customer,
        repository=customer_repository,
        subject="local-client-admin",
        tenant_id=tenant_a.tenant_id,
        client_id=client_a1.client_id,
        roles=(CustomerRole.OWNER,),
        occurred_at=now,
    )

    catalog_repository = PostgresProviderCatalogRepository(session_factory)
    catalog = await catalog_repository.get_effective()
    if catalog is None:
        catalog = await ProviderCatalogAdminService(
            repository=catalog_repository,
            audit=audit,
        ).publish_and_activate(
            providers=(_local_provider(),),
            endpoints=(),
            occurred_at=now,
        )

    pricing_repository = PostgresProviderPricingCatalogRepository(session_factory)
    pricing = await pricing_repository.get_effective()
    if pricing is None:
        pricing = await ProviderPricingAdminService(
            repository=pricing_repository,
            audit=audit,
        ).publish_and_activate(
            rules=_pricing_rules(now),
            occurred_at=now,
        )

    expected_targets = (
        AuthorizedExecutionTarget(
            ORQETIA_TEST_PROVIDER,
            "economy",
            "standard",
        ),
        AuthorizedExecutionTarget(
            ORQETIA_TEST_PROVIDER,
            "premium",
            "standard",
        ),
    )
    policy_repository = PostgresExecutionPolicyRepository(session_factory)
    policy_admin = ExecutionPolicyAdminService(
        policy_repository,
        owner_resolver=tenancy,
    )
    for owner in (client_a1, client_a2, client_b1):
        effective = await policy_repository.get_effective(
            tenant_id=owner.tenant_id,
            client_id=owner.client_id,
        )
        if effective is None:
            await policy_admin.publish_and_activate(
                tenant_id=owner.tenant_id,
                client_id=owner.client_id,
                max_cycles=2,
                max_attempts=4,
                cycle_delay_seconds=0,
                retry_after_cap_seconds=5,
                authorized_targets=expected_targets,
                occurred_at=now,
            )

    quota_repository = PostgresQuotaPolicyRepository(session_factory)
    quota_admin = QuotaPolicyAdminService(
        repository=quota_repository,
        owner_resolver=tenancy,
    )
    for owner, limit in ((client_a1, Decimal("1000")), (client_b1, Decimal("0"))):
        existing = tuple(
            item
            for item in await quota_repository.list_for_subject(
                tenant_id=owner.tenant_id,
                client_id=owner.client_id,
            )
            if item.metric is QuotaMetric.TASKS
            and item.enforcement is QuotaEnforcementMode.HARD
        )
        if not existing:
            await quota_admin.publish(
                scope=QuotaScope.CLIENT,
                tenant_id=owner.tenant_id,
                client_id=owner.client_id,
                metric=QuotaMetric.TASKS,
                limit=limit,
                enforcement=QuotaEnforcementMode.HARD,
                period_seconds=3600,
                effective_from=now,
            )

    account_repository = PostgresProviderAccountRepository(session_factory)
    credential_repository = PostgresProviderCredentialRepository(session_factory)
    accounts = await account_repository.list_for_provider(ORQETIA_TEST_PROVIDER)
    account = next(
        (
            item
            for item in accounts
            if item.display_label == "ORQETIA Local Simulator Account"
        ),
        None,
    )
    if account is None:
        account = await ProviderAccountService(
            accounts=account_repository,
            credentials=credential_repository,
            capacity=PostgresExternalCapacityRepository(session_factory),
        ).create_account(
            provider_id=ORQETIA_TEST_PROVIDER,
            display_label="ORQETIA Local Simulator Account",
            occurred_at=now,
            priority=0,
            commercial_mode="SYNTHETIC",
            commercial_tier="LOCAL",
            region="local",
            contract_reference="local-homologation-only",
        )

    provider_credentials = await credential_repository.list_active(
        provider_account_id=account.provider_account_id
    )
    if not provider_credentials:
        await ProviderCredentialService(
            secrets=secret_store,
            repository=credential_repository,
            audit=audit,
        ).create(
            provider_id=ORQETIA_TEST_PROVIDER,
            provider_account_id=account.provider_account_id,
            secret=SecretValue(
                "local-simulator-" + secrets.token_urlsafe(32)
            ),
            occurred_at=now,
        )

    token_store = PostgresClientCredentialStore(session_factory)
    client_credentials = ClientAccessCredentialService(token_store)
    tokens = _read_tokens()
    credential_a1 = await _ensure_api_token(
        service=client_credentials,
        store=token_store,
        tokens=tokens,
        token_key="client_a1_full",
        tenant_id=tenant_a.tenant_id,
        client_id=client_a1.client_id,
        display_label="local-bootstrap-a1-full",
        scopes=_FULL_SCOPES,
        occurred_at=now,
    )
    credential_a1_read = await _ensure_api_token(
        service=client_credentials,
        store=token_store,
        tokens=tokens,
        token_key="client_a1_readonly",
        tenant_id=tenant_a.tenant_id,
        client_id=client_a1.client_id,
        display_label="local-bootstrap-a1-readonly",
        scopes=_READ_SCOPES,
        occurred_at=now,
    )
    credential_b1 = await _ensure_api_token(
        service=client_credentials,
        store=token_store,
        tokens=tokens,
        token_key="client_b1_full",
        tenant_id=tenant_b.tenant_id,
        client_id=client_b1.client_id,
        display_label="local-bootstrap-b1-full",
        scopes=_FULL_SCOPES,
        occurred_at=now,
    )
    _atomic_private_json(_TOKEN_PATH, tokens)

    manifest: dict[str, object] = {
        "schema_version": 1,
        "environment": normalized,
        "generated_at": now.isoformat(),
        "oidc": {
            "issuer": LOCAL_OIDC_ISSUER,
            "profiles": {
                "backoffice_admin": "backoffice-admin",
                "client_viewer": "client-viewer",
                "client_operator": "client-operator",
                "client_admin": "client-admin",
            },
        },
        "tenants": {
            "tenant_a": str(tenant_a.tenant_id),
            "tenant_b": str(tenant_b.tenant_id),
        },
        "clients": {
            "client_a1": str(client_a1.client_id),
            "client_a2": str(client_a2.client_id),
            "client_b1_quota_blocked": str(client_b1.client_id),
        },
        "memberships": {
            "viewer": str(viewer_membership.membership_id),
            "operator": str(operator_membership.membership_id),
            "admin": str(admin_membership.membership_id),
        },
        "identities": {
            "viewer": str(viewer_identity.identity_id),
            "operator": str(operator_identity.identity_id),
            "admin": str(admin_identity.identity_id),
            "backoffice_binding": str(backoffice_binding.binding_id),
        },
        "provider": {
            "provider_id": ORQETIA_TEST_PROVIDER,
            "models": ["economy", "premium"],
            "reasoning_profile": "standard",
            "provider_account_id": str(account.provider_account_id),
            "catalog_version_id": str(catalog.version.catalog_version_id),
            "pricing_catalog_version_id": str(pricing.version.catalog_version_id),
        },
        "client_api_credentials": {
            "client_a1_full": credential_a1.safe_view(),
            "client_a1_readonly": credential_a1_read.safe_view(),
            "client_b1_full": credential_b1.safe_view(),
            "token_material_path": str(_TOKEN_PATH),
        },
        "quota": {
            "client_a1": "HARD TASKS 1000/hour",
            "client_b1": "HARD TASKS 0/hour",
        },
    }
    _atomic_private_json(_MANIFEST_PATH, manifest)
    return BootstrapResult(manifest_path=_MANIFEST_PATH, token_path=_TOKEN_PATH)
