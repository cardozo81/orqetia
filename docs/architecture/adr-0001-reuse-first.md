# ADR-0001 — Reuse-first extraction from RASAi

- Status: **Accepted**
- Issue: #43
- Depends on: #31, #34
- Baseline source: `cardozo81/RASAI-Readiness-Auditor@c66188e6e3088603b08eb750eece272451d6342c`

## Context

ORQETIA externalizes a behavior boundary already validated in RASAi. Rewriting that behavior without a material technical benefit increases semantic drift risk, especially around retries, cycle ownership, partial completion, health/quarantine and accounting.

The source repository is read-only for ORQETIA. Reuse means extraction/adaptation into this repository, never shared mutable ownership.

## Decision

ORQETIA adopts **reuse-first**.

For every canonical source module, choose one of:

- `EXTRACT_PURE` — move/generalize pure logic with minimal semantic change;
- `COPY_ADAPT` — copy a bounded implementation and remove RASAi-specific configuration/domain coupling;
- `PORT_ALGORITHM` — preserve the algorithm while replacing state/storage/runtime boundaries;
- `REIMPLEMENT_BOUNDARY` — keep contracts/behavior but build a new ORQETIA-specific infrastructure boundary;
- `DO_NOT_PORT_DOMAIN` — do not move RASAi domain behavior into ORQETIA core.

No percentage below is a source-code coverage claim. It is a planning estimate of **semantic/algorithmic reuse potential**.

## Reuse matrix

| RASAi source | Strategy | Reuse potential | Preserve | Replace/remove | ORQETIA destination |
|---|---|---:|---|---|---|
| `ai_canonical_orchestration.py` | EXTRACT_PURE | Very high / ~85–95% | execution policy, outcomes, terminal classification, unique candidates, bounded Retry-After, cycle algorithm | RASAi env naming/default wiring | `execution/orchestration` |
| `ai_economic_telemetry.py` | EXTRACT_PURE | Very high / ~80–90% | canonical token total, pricing application boundary, observed-vs-estimated precedence, currency-safe aggregate, UNPRICED | legacy field compatibility when no longer needed | `usage_accounting` |
| `ai_cost_policy.py` | COPY_ADAPT | High / ~70–85% | pricing math, rule resolution, reasoning billing, UNPRICED fail-safe, candidate forecast | RASAi scopes/default estimates/config loading | `usage_accounting/pricing` |
| `ai_pricing_catalog.py` | COPY_ADAPT | High / ~70–85% | schema validation, effective dates, conditions, rule resolution | local file/env ownership as public contract | `control_plane/pricing` |
| `ai_model_catalog.py` | COPY_ADAPT | High / ~65–80% | enabled/selectable/default/effective-date/reasoning validation | RASAi classification/recommended-use fields that are domain-only | `control_plane/models` |
| `dynamic_ai_routing.py` | PORT_ALGORITHM | Medium-high / ~50–65% | terminal quarantine, transient degradation, usage hints, candidate ranking, deterministic fallback | in-process session state; RASAi context schemas/hooks | `execution/health`, `execution/routing` |
| `provider_runtime_policy.py` | PORT_ALGORITHM | Medium / ~45–60% | AUTO eligibility logic, explicit unitary pool, timeout/policy application | consumer env vars, console integration, RASAi public defaults | `control_plane/policy`, `execution/routing` |
| `provider_registry.py` | REIMPLEMENT_BOUNDARY | Medium / ~35–50% | provider/model validation concepts, canonical IDs, model eligibility | env-centric registry construction, CLI aliases as core contract | `providers/registry` |
| core/extension provider adapters | COPY_ADAPT per provider | Medium-high / ~50–75% | auth/header/payload/wire schema/error normalization/usage extraction; single-attempt rule | direct env lookup, RASAi semantic input/output types, local persistence | `providers/adapters/<provider>` |
| `ai_orchestration_unification.py` | DO_NOT_PORT_DOMAIN + selective helpers | Low / ~10–25% | generic adapter-call/provenance patterns only | specialist/AUD/RPR/source-quality/improvement hooks and schemas | generic execution helpers only |
| `ai_catalog_control_plane.py` | REIMPLEMENT_BOUNDARY | Low-medium / ~25–40% | publication/version/hash/immutability concepts | RASAi tables/SQLite/control-plane schema | `control_plane` persistence |
| `platform/usage_ingestion.py` | REIMPLEMENT_BOUNDARY | Low-medium / ~20–35% | idempotent projection concept and metadata dimensions | RASAi historical tables/component names | `usage_accounting/ingestion` |

## Estimated aggregate effect

For the **canonical orchestration + economic core**, Python permits substantial direct semantic reuse: roughly **70–90% of the pure algorithmic behavior** can be extracted or adapted without language translation.

For **infrastructure boundaries** — tenancy, persistence, web API, queue/worker, AuthN/AuthZ, secret management and ORQETIA control plane — reuse from RASAi is intentionally low. Those are new product boundaries.

For **provider adapters**, reuse is provider-by-provider and must be measured through contract tests rather than line count.

## Gate against a near-total rewrite

Before choosing a stack or architecture that makes substantial reuse impossible, stop implementation and record:

1. modules that can no longer be extracted/adapted;
2. affected CHAR contracts from #31/#34;
3. expected semantic rewrite surface;
4. new test/parity burden;
5. operational benefit that compensates the rewrite;
6. alternative retaining Python;
7. explicit human architectural decision.

A language change alone is not a sufficient benefit.

## Port sequence

1. keep #34 as the independent test oracle;
2. establish ORQETIA module boundaries and persistence contracts;
3. extract pure orchestration/economic functions first;
4. replace memory-bound health/session state with durable ORQETIA state;
5. define neutral Adapter Protocol;
6. port one provider at a time;
7. prove provider parity with fakes/mock HTTP;
8. never import RASAi package at ORQETIA runtime.

## Dependency rule

ORQETIA may copy/adapt characterized source under the repository's applicable licensing/ownership constraints, but production must not depend on a live RASAi checkout, its database, its environment variables or its domain modules.

## Canonical test rule

Each port PR must list the `CHAR-xxx` contracts it affects.

If a `CANONICAL_PRESERVE` behavior changes intentionally, the PR is blocked until an ADR explicitly accepts that semantic change.

## Consequences

### Positive

- lowers semantic-regression risk;
- preserves already-homologated orchestration behavior;
- makes provider migration incremental;
- lets tests compare behavior rather than implementation style.

### Costs

- some existing code must be cleaned of RASAi naming/configuration;
- durable distributed state cannot be copied directly from in-memory RASAi runtime;
- temporary duplication exists while RASAi remains the first future client.

## Stack implication

Python is the **strong default** for #2 because it preserves the highest-value reuse with the lowest translation risk.

This ADR does not itself choose the complete application stack. #2 must still evaluate framework, persistence, worker/queue, observability and testing, but a non-Python result triggers the rewrite gate above.
