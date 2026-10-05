# ORQETIA Architecture

Status: **DEVELOPMENT**.

This repository is the only writable boundary for ORQETIA. The RASAi repository is read-only technical reference material.

## Core topology

```text
Backoffice Web ─┐
Client Portal ──┼──> API/Auth ─> Persistence ─> Queue/Scheduler ─> Workers
External Clients┘                                             │
                                                               ▼
                                                     Policy / Providers
                                                               │
                                                               ▼
                                                  Usage / Accounting
```

## Execution selection

### AUTO — default

If no target is requested, ORQETIA:
1. filters to integrated, configured, capability-compatible, client-authorized and healthy candidates;
2. orders economically comparable candidates from lower estimated provider cost to higher;
3. attempts the cheapest first;
4. escalates only if the candidate does not satisfy the remaining requirement;
5. preserves partial accepted progress;
6. recomputes eligibility/order on each cycle.

Pricing is not an eligibility gate. UNPRICED candidates remain eligible. Currencies/native units are not compared through invented FX.

### EXPLICIT_TARGET — optional

A service client may request an authorized provider, model and reasoning/depth profile. The effective target is then immutable for that task. Retry, Retry-After, timeout, cycles and validation remain ORQETIA responsibilities, but no cross-provider/model/depth fallback is allowed because of cost.

Normal retries create new `attempt_id` values while preserving the same explicit effective target. For AUTO, there is no single fixed task-level target; actual target provenance is stored per attempt.

The full selection contract, economic comparison-group rules and explicit-target hard constraint are defined by ADR-0020 / #80.

## Canonical execution invariants

Unless an ADR explicitly changes them:
- adapters are single-attempt;
- a provider is called at most once per cycle;
- candidate eligibility is re-evaluated between cycles;
- explicit-provider mode uses the same orchestration semantics as AUTO with a one-provider pool;
- timers, retries, waits and cycles belong to ORQETIA;
- Retry-After is bounded;
- terminal failures can quarantine a provider for the execution session;
- transient failures remain eligible according to policy;
- partial completion preserves accepted/missing progress;
- pricing does not create eligibility;
- UNPRICED is not zero;
- currencies are not implicitly converted or combined;
- reasoning tokens are not double-counted.

See #31, #34 and #43.

## Attempt identity and operation boundary

RASAi PR #201 / main `8008a3e24550f7c4b199beb0adeefa9c4e618538` is a read-only post-baseline requirement source.

ORQETIA generic contracts:
- each provider dispatch has a stable `attempt_id` created before dispatch;
- exchange, usage, pricing and diagnostic facts use that same ID;
- normal retry/fallback creates a new attempt even for an identical request payload;
- request fingerprints are not attempt identities;
- an operation may be taskless when its ORQETIA operation contract explicitly allows it;
- `operation` remains explicit and no RASAi-style generic round is introduced.

See ADR-0018 / #86 / #87.

## Exchange evidence integrity

Provider communication evidence is sanitized **before** persistence. The persisted sanitized body is the source of truth for any raw-evidence API/UI surface.

Presentation may humanize labels, titles, statuses and provider display names, but must not semantically rewrite tokens inside request/response/prompt/schema evidence. Transport escaping is allowed when it round-trips to the persisted sanitized content.

`provider_id` is a typed Provider Registry identity; `provider_name` is separate display metadata. Provider IDs never use a generic status/enum fallback.

See ADR-0019 / #88.

## Administration boundary

Only authorized Backoffice users administer tenants, clients, Backoffice users, execution policies, orchestration parameters, providers/models/endpoints, provider credentials, pricing, quotas, rate limits and scopes.

Client-facing surfaces do not administer orchestration policy.

## Economic separation

```text
technical_usage
      ↓
provider_cost
      ↓
future commercial plan
      ↓
client_charge
```

Provider cost is internal. Client-facing APIs and reports expose no monetary values in the current phase.

## Data-plane direction

Initial hypothesis:
- PostgreSQL OLTP;
- append-heavy execution/usage ledger;
- immutable policy/pricing snapshots;
- incremental statistical rollups;
- read models for UI/API;
- tenant-safe CLIENT_ONLY and GLOBAL_PUBLIC aggregates.

Heavy aggregation must not execute synchronously on the hot insert path.

See #33, #44 and #45.

## Stack decision

Python is currently favored because substantial canonical logic appears reusable. It is not mandated.

If a proposed stack implies near-total rewrite, apply the stop-and-review gate in #43.
