# ADR — Administrative tenant/client lifecycle

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #136

## Decision

Identity & Tenancy owns the authoritative tenant and service-client lifecycle.

Tenants and clients are server-identified, versioned administrative records with
ACTIVE/DISABLED state. A service client belongs to exactly one tenant for its whole
lifetime.

## Effective ownership

Other contexts use the typed `resolve_active_owner` contract instead of reading the
identity schema directly. Ownership resolution succeeds only when:

- the client exists;
- its tenant_id matches the requested tenant;
- the client is ACTIVE;
- the tenant is ACTIVE.

Disabling a tenant therefore disables effective access for all of its clients without
rewriting child rows.

## Administration

Backoffice may:

- create tenants;
- change tenant status;
- create clients under ACTIVE tenants;
- change client status.

A disabled client cannot be reactivated while its parent tenant is disabled.

Mutations use optimistic versions and emit safe administrative audit events. Names are
labels only and never authorization identifiers.

## Data ownership

Tables live in the `identity` schema. Execution, Accounting, Control Plane and
Read Models keep typed tenant/client references but do not mutate these records.

## Validation

The issue is validated through the isolated tenancy lifecycle tests and the Alembic
migration chain. No provider or external identity system is required.
