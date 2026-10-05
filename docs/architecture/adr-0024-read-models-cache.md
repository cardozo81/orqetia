# ADR — Secure read models and cache boundaries

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #45

## Principle

Interactive UI/report surfaces consume rebuildable read models. They do not perform
full scans of high-cardinality execution/accounting ledgers for normal dashboard
requests. Authoritative detail remains with the owning context.

A read model is never an authorization source. Mutation paths revalidate authoritative
tenant/client/RBAC/ABAC state.

## Query/read-model map

| Surface | Audience | Classification | Freshness | SWR | Primary source |
|---|---|---|---|---|---|
| Overview | Client/Backoffice | CLIENT_PRIVATE/INTERNAL | 30s | yes | execution + usage rollups |
| Task list | Client/Backoffice | CLIENT_PRIVATE | 10s | no | execution projection |
| Usage summary | Client/Backoffice | CLIENT_PRIVATE | 60s | yes | accounting rollup |
| Quota utilization | Client/Backoffice | CLIENT_PRIVATE | 5s | no | quota ledger projection |
| Estimate benchmark | Client/Backoffice | CLIENT_PRIVATE/INTERNAL | 300s | yes | estimation snapshot |
| Provider cost summary | Backoffice only | CONFIDENTIAL | 60s | yes | accounting rollup |
| Credential status | Client/Backoffice | CLIENT_PRIVATE/RESTRICTED | no cache | no | identity/control projection |

## Isolation

Projection/cache identity includes:

- surface;
- audience;
- tenant;
- client;
- normalized role scope;
- query fingerprint.

A cache entry from one owner/role/query therefore cannot alias another partition.
Client projections require tenant+client and reject financial/provider-account fields
recursively. Provider cost/currency/pricing/account/credit data remains Backoffice-only.

## Freshness and invalidation

Every projection carries `as_of`, source watermark and refreshed timestamp.
Stale-while-revalidate is allowed only on explicitly non-critical surfaces.
Quota and credential status fail closed on staleness instead of serving a stale value.

Invalidation can target tenant/client/surface. Event consumers may rebuild projections
incrementally from owner facts; replay must be idempotent and version-monotonic.

## Pagination and export

Cursor pagination is bound to the query fingerprint. Cursor contents are opaque
navigation state only and never carry authorization. Authorization scopes are supplied
from the current authenticated context on every query.

Surface policies cap page size and export rows. Large exports are separate bounded
jobs in future reporting work rather than unbounded synchronous queries.

## Physical model

`readmodel.projection_documents` stores typed owner/audience/surface/query dimensions
plus JSONB aggregate payload. JSONB is used only for projection-specific aggregate
shape; authorization/filter dimensions stay typed and indexed.

Updates are version-monotonic. Projections are disposable and rebuildable.

## Reproducible synthetic SLO

`scripts/benchmark_read_models.py` creates, caches and reads 10,000 isolated synthetic
documents plus a cursor page. CI budget is 10 seconds on the standard GitHub runner.
This is a regression guard, not a production latency claim.

Production SLOs are measured separately by surface after deployment sizing is known.
No paid infrastructure is required for the synthetic benchmark.
