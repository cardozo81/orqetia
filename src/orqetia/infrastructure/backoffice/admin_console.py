"""Typed composition bundle for the secure Backoffice administrative web surface."""

from __future__ import annotations

from dataclasses import dataclass

from orqetia.control_plane import (
    ExecutionPolicyAdminService,
    ProviderAccountRepository,
    ProviderAccountService,
    ProviderCatalogAdminService,
    ProviderCredentialRepository,
    ProviderCredentialService,
    ProviderPricingAdminService,
    QuotaPolicyAdminService,
)
from orqetia.identity import (
    BackofficeBindingRepository,
    ClientAccessCredentialService,
)
from orqetia.read_models import (
    BackofficeReportingService,
    OperationalFinancialIntelligenceService,
)
from orqetia.tenancy import TenancyAdminService, TenantClientRepository

@dataclass(frozen=True)
class BackofficeAdminServices:
    tenancy: TenancyAdminService
    tenancy_repository: TenantClientRepository
    bindings: BackofficeBindingRepository
    execution_policies: ExecutionPolicyAdminService
    quota_policies: QuotaPolicyAdminService
    provider_catalog: ProviderCatalogAdminService
    provider_pricing: ProviderPricingAdminService
    provider_accounts: ProviderAccountService
    provider_account_repository: ProviderAccountRepository
    provider_credentials: ProviderCredentialService
    provider_credential_repository: ProviderCredentialRepository
    client_credentials: ClientAccessCredentialService
    intelligence: OperationalFinancialIntelligenceService
    reporting: BackofficeReportingService
