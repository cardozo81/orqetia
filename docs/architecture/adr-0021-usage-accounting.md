# ADR — Canonical usage, pricing and internal provider accounting

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #16

## Decision

The `usage_accounting` bounded context owns immutable technical usage and internal
provider-cost facts. Execution/provider transports may report usage, but they do not
own pricing policy or monetary aggregation.

The baseline separates:

1. technical usage;
2. internal provider cost estimate;
3. provider-observed monetary cost when available.

Commercial `client_charge` remains outside this boundary and is future work (#41).

## Canonical invariants

- provider-reported total tokens win over local token recomputation;
- reasoning tokens are not automatically added to canonical total tokens;
- reasoning billing is controlled by the versioned pricing rule;
- `UNPRICED` is a first-class state and never means zero;
- missing cache split fails closed whenever cached and uncached rates differ;
- mixed token/native usage is not monetized without an explicit compatible contract;
- observed provider monetary cost takes precedence over usage-derived estimate;
- currencies are aggregated independently and never converted implicitly;
- pricing rule id/version/reference are snapshotted on the ledger fact;
- attempt+fragment identity is immutable/idempotent;
- client projections expose technical usage only, never provider cost, currency,
  pricing rules, account balances or commercial values.

## Supported technical pricing models

- `TOKEN_STANDARD`;
- `TOKEN_CONTEXT_TIERED`;
- `TOKEN_TIME_WINDOW`;
- `PER_REQUEST`;
- `PROVIDER_CREDITS`.

Rates are represented with `Decimal`; token rates are per million tokens. Context
tiers and UTC time windows resolve deterministic token-rate sets.

## Persistence

Facts are stored in `accounting.usage_ledger`. The table deliberately has no
cross-context foreign keys. Tenant/client/session/task/attempt identifiers are copied
as immutable references under the data-ownership rules from #33/#50.

The ledger stores estimated and observed currencies separately because a provider
observation can differ from the estimate. Read/reporting aggregation must choose the
observed value when present, otherwise the estimate, and group strictly by currency.

## Routing boundary

AUTO routing may consume an internal pricing quote to construct its candidate
comparison metadata. EXPLICIT_TARGET can still be accounted and priced, but the
monetary value must never alter the explicit target chosen by the service client.

## Validation and cost

Ordinary CI uses only deterministic fixtures/fakes plus the repository PostgreSQL
service. No paid provider call, provider credential or variable-cost resource is
required.
