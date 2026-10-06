# ADR — Reporting read models and audience separation

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #26

## Decision

Reporting uses typed rollups in the `readmodel` schema. Normal dashboards and client
usage APIs do not full-scan the execution/accounting ledgers.

A rollup preserves typed dimensions needed by Backoffice:

- tenant/client;
- client access credential id;
- provider/model;
- provider account/credential id;
- period;
- status/error class.

It also preserves technical usage, latency/retry/cycle counters, estimated/observed
provider cost with their own currencies, and UNPRICED counts.

## Client projection

The client usage service always injects the authenticated tenant/client filter and
collapses provider/account/credential dimensions into technical period buckets.

Client payloads contain only:

- input/cached/output/reasoning/total tokens;
- native technical usage;
- period;
- freshness/as_of and pagination metadata.

Provider account/credential identifiers, client credential reporting identifiers,
financial amounts, currencies, pricing and external provider balances are absent by
construction.

## Backoffice projection

Authorized Backoffice reporting can filter by the internal dimensions above and may
receive financial fields when the caller has the explicit financial-reporting
permission.

Different currencies remain separate fields/facts; no implicit FX is introduced.
UNPRICED remains distinct from zero.

## Export

Sensitive Backoffice export is a separate authorized operation. It is row-bounded by
the read-model surface policy and always emits an audit event containing a safe filter
fingerprint and row count. Raw filter secrets/idempotency keys are never stored.

## External provider capacity

Provider credit/quota observations remain owned by #59. Reporting and Backoffice may
surface those official snapshots alongside reporting rollups, but they are not
fabricated or derived from client usage.

## Performance

`readmodel.report_rollups` carries typed, indexed dimensions for common owner,
provider/model and credential filters. Projection writers may rebuild/upsert rollups
from authoritative facts. Factual attempt detail remains in its owning context.

## Validation

The issue uses isolated synthetic reporting tests plus migration and client usage API
contract tests. No paid provider or production data is required.
