"""Identity & Tenancy administrative lifecycle contracts."""

from .admin import (
    ActiveOwner,
    AdministrativeStatus,
    InMemoryTenancyAuditSink,
    InMemoryTenantClientRepository,
    ServiceClientRecord,
    TenancyAdminService,
    TenancyAuditEvent,
    TenancyAuditSink,
    TenantClientRepository,
    TenantRecord,
)
from .postgres import PostgresTenantClientRepository
from .tables import service_clients, tenants

__all__ = [
    "ActiveOwner",
    "AdministrativeStatus",
    "InMemoryTenantClientRepository",
    "InMemoryTenancyAuditSink",
    "PostgresTenantClientRepository",
    "ServiceClientRecord",
    "TenantClientRepository",
    "TenantRecord",
    "TenancyAdminService",
    "TenancyAuditEvent",
    "TenancyAuditSink",
    "service_clients",
    "tenants",
]
