# ADR — Provider account, capacity and credential provenance control

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #59

## Decision

ORQETIA owns provider accounts and credentials in the baseline product. Tenants/clients
consume ORQETIA and do not bring or receive provider credentials.

The Control Plane owns:

- provider account metadata and operational status;
- provider credential metadata/rotation/revocation through #25;
- credential selection across active accounts;
- provider-observed external capacity/credit snapshots when a trustworthy source exists.

No balance is inferred when a provider does not expose a reliable observation.

## Credential selection

Selection is internal only. Accounts are ordered by administrative priority, then only
ACTIVE accounts with region-compatible, ACTIVE and non-expired credentials participate.
Credential health metadata may demote a credential after a failure.

The result is an internal provider account + credential identity. Secret material never
crosses the Control Plane secret-store boundary.

## Provenance

Every provider attempt and accounting entry may carry:

- provider_account_id;
- provider_credential_id.

A credential id without an account id is invalid in both the domain and database.
These identifiers are internal reporting dimensions; they are not client-facing.

## External capacity

Provider capacity/credit snapshots are append-only observations with:

- native unit;
- remaining and/or limit;
- reset timestamp when provided;
- observed_at;
- official source type and reference.

Unknown capacity remains unknown. Zero is valid only when the provider actually
reports zero.

## Exposure

Backoffice may consume safe ids/fingerprints and commercial metadata according to RBAC.
Client APIs/read models must not expose account ids, credential ids/fingerprints,
balances, internal pricing, currency or provider cost.

## Validation

Ordinary validation is isolated to provider-account/control-plane contract tests plus
the migration chain. No real provider, paid API or external secret manager is required.
