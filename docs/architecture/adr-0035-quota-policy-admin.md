# ADR — Administrative quota-policy versioning

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #139

## Decision

Control Plane administers quota definitions through immutable versions of the existing
`QuotaPolicySnapshot` contract from #17. Enforcement remains owned by Usage &
Accounting and is not duplicated here.

The existing `control.quota_policies` table is reused; no new physical schema or
migration is required for this issue.

## Series identity

A policy series keeps the same policy_id across versions. Its semantic key is immutable:

- scope;
- tenant/client owner;
- metric;
- provider_id when applicable;
- native_unit when applicable.

Changing one of those creates a new policy series rather than rewriting history.

## Ownership

CLIENT policies validate the exact active tenant/client through #136.
TENANT policies validate the active tenant itself. Disabled or cross-tenant subjects
fail closed.

Quota policy never grants provider/model authorization. Provider-specific quotas only
restrict an already-authorized target.

## Resolution

Effective resolution matches the exact semantic key and time window, choosing the most
recent effective version. Missing effective policy is explicit; there is no implicit
unlimited/default policy in the administrative service.

## Validation

The isolated gate runs the Alembic chain plus quota-admin tests for versioning,
ownership, semantic-key immutability and the existing #17 metric/scope invariants.
