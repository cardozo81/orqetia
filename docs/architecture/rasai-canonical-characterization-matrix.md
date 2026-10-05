# RASAi → ORQETIA Canonical Characterization Matrix

## Status

Baseline characterized read-only from:

- repository: `cardozo81/RASAI-Readiness-Auditor`;
- commit: `c66188e6e3088603b08eb750eece272451d6342c`;
- package/runtime: `0.7.0`;
- canonical orchestration: RASAi #190 / PR #194;
- canonical economic telemetry: RASAi #195 / PR #196.

The RASAi repository is an immutable source for this project. No ORQETIA implementation may require a mutation there.

## Classification

- **CANONICAL_PRESERVE** — behavior must remain semantically equivalent.
- **GENERALIZE_NAME_ONLY** — naming/schema may become provider/domain neutral without changing behavior.
- **RASAI_DOMAIN_ONLY** — do not move into ORQETIA core.
- **EVOLUTION_REQUIRES_ADR** — intentional semantic change requires an explicit ADR.

## Characterization matrix

| ID | Behavior | RASAi owner / evidence | Observed contract | Classification | ORQETIA target | Risk |
|---|---|---|---|---|---|---|
| CHAR-001 | Adapter single-attempt | `provider_extensions.py::IsolatedStructuredSemanticProvider.analyze`; equivalent core/provider adapters | Adapter ignores logical `max_attempts`; one adapter invocation performs at most one external provider call. Retry/cadence is not adapter-owned. | CANONICAL_PRESERVE | Provider Adapter Protocol | High |
| CHAR-002 | One call/provider/cycle | `ai_canonical_orchestration.py::unique_cycle_candidates/run_ai_need` | Candidate pool is deduplicated by provider identity before every cycle; each candidate is invoked at most once in that cycle. | CANONICAL_PRESERVE | Orchestration Engine | High |
| CHAR-003 | Candidate pool re-evaluated every cycle | `run_ai_need` | `candidates()` is re-evaluated at cycle start and before sleeping, so health/configuration changes affect the next cycle. | CANONICAL_PRESERVE | Orchestration Engine | High |
| CHAR-004 | Single timer owner + bounded Retry-After | `effective_cycle_delay/run_ai_need` | Inter-cycle delay is `max(configured_delay, max(valid Retry-After hints bounded by cap))`; adapters do not sleep. Invalid/negative/non-finite hints are ignored. | CANONICAL_PRESERVE | Orchestration Engine / Scheduler | High |
| CHAR-005 | Terminal provider error classes | `TERMINAL_PROVIDER_ERROR_CLASSES` | `AUTH_ERROR`, `CREDIT_ERROR`, hard `QUOTA_ERROR`, `MODEL_ERROR`, `PERMISSION_ERROR` are terminal; normalized others are transient/no-progress according to adapter diagnostic. | CANONICAL_PRESERVE | Error Taxonomy / Health | High |
| CHAR-006 | Execution-session terminal quarantine | `dynamic_ai_routing.py::AiExecutionCoordinator.record_attempt` | Terminal error makes provider ineligible for the execution coordinator/session; future needs sharing that coordinator do not re-enable it. | CANONICAL_PRESERVE | Execution Session / Provider Health | High |
| CHAR-007 | Transient degradation is not terminal quarantine | `AiExecutionCoordinator.record_attempt` | Non-terminal failures update a rolling degradation state; provider remains distinguishable from terminally quarantined. | CANONICAL_PRESERVE | Provider Health / Circuit Breaker | High |
| CHAR-008 | Explicit provider = unitary pool | `provider_runtime_policy.py::build_semantic_provider` | Explicit selection receives the same execution coordinator/policy shape as AUTO, with a coordinator containing exactly that provider. | CANONICAL_PRESERVE | Policy Engine | High |
| CHAR-009 | AUTO eligibility is independent of pricing | `provider_registry.py`; `provider_runtime_policy.py::_build_auto_provider`; `dynamic_ai_routing.py::order_candidate_objects` | Integrated + model-eligible + configured + not administratively excluded + operationally eligible providers may participate. Missing pricing does not remove a provider. | CANONICAL_PRESERVE | Provider Registry / Policy Engine | High |
| CHAR-010 | AUTO cost ordering keeps UNPRICED eligible | `DynamicProviderRoutingSession.order_candidate_objects` | Candidates with priced USD estimate sort before UNPRICED; UNPRICED candidates remain in deterministic fallback order. `UNPRICED != 0`. | CANONICAL_PRESERVE | Routing / Forecast | High |
| CHAR-011 | Per-need cycle budget resets | `run_ai_need` | Every logical need starts again at cycle 1; session-level quarantine/health can survive across needs. | CANONICAL_PRESERVE | Task/Need Execution | High |
| CHAR-012 | Partial progress survives later failure | `run_ai_need` | Any `PARTIAL_PROGRESS` marks progress. If no later COMPLETE occurs, final state is PARTIAL rather than UNAVAILABLE. Accepted progress is not discarded by a later transient/terminal failure. | CANONICAL_PRESERVE | Task Result / Partial Completion | High |
| CHAR-013 | Input blocked terminates the current need | `run_ai_need` | `INPUT_BLOCKED` terminates immediately and preserves whether prior progress existed. | CANONICAL_PRESERVE | Task State Machine | Medium |
| CHAR-014 | Provider attempt provenance is per attempt | `dynamic_ai_routing.py`; `ai_orchestration_unification.py` | Provider/model/cycle/attempt/fallback/diagnostic/usage/pricing metadata belongs to individual attempts; fallback does not collapse provenance into the winner. | GENERALIZE_NAME_ONLY | Attempt Ledger | High |
| CHAR-015 | Canonical total token calculation | `ai_economic_telemetry.py::canonical_total_tokens` | Provider-reported `total_tokens` wins. Otherwise total is input + output. `reasoning_tokens` is not blindly added again. | CANONICAL_PRESERVE | Usage Accounting | High |
| CHAR-016 | Pricing is post-usage application, not execution admission | `price_provider_usage/price_attempt_usage`; AUTO repricing | Pricing metadata is applied to observed usage and used for telemetry/ranking. Missing price produces UNPRICED rather than execution rejection. | CANONICAL_PRESERVE | Usage & Accounting | High |
| CHAR-017 | Observed cost and estimated cost are distinct | `attempt_monetary_cost` | Provider-observed monetary cost has precedence when present; usage-derived estimate is separate fallback; legacy estimate is separately identified. | CANONICAL_PRESERVE | Accounting Ledger | High |
| CHAR-018 | Currency-safe aggregation | `aggregate_attempt_costs` | Monetary totals aggregate by currency. No implicit FX and no summing heterogeneous currencies into one scalar. Attempts with usage but no price increment an unpriced count. | CANONICAL_PRESERVE | Accounting / Reports | High |
| CHAR-019 | Mixed token + native usage is not priced by invention | `ai_cost_policy.py::resolve_observed_cost` | If token and native commercial components coexist without a defined combined pricing model, result remains UNPRICED. | CANONICAL_PRESERVE | Pricing Engine | High |
| CHAR-020 | Missing cached-token split is fail-safe | `resolve_observed_cost` | Missing cached usage may be priced only when cached and uncached input rates are equal; otherwise UNPRICED. | CANONICAL_PRESERVE | Pricing Engine | High |
| CHAR-021 | Reasoning billing is catalog-controlled | `_billable_output_tokens`; pricing catalog | Reasoning is added to billable output only when pricing policy explicitly says `ADD_REASONING_TO_OUTPUT`; otherwise it remains an output breakdown. | CANONICAL_PRESERVE | Pricing Engine | High |
| CHAR-022 | Model/provider catalog validates effective configuration | `ai_model_catalog.py`; `provider_registry.py` | Provider/model identity, effective dates, enabled/selectable/default/AUTO eligibility and reasoning contract are validated; unknown provider without integrated adapter is rejected. | GENERALIZE_NAME_ONLY | Provider Registry / Model Catalog | Medium |
| CHAR-023 | Usage projection is derived, not source of truth | `platform/usage_ingestion.py` | Historical execution telemetry is read-only source; usage ledger projection is idempotent and does not mutate historical domain records. | CANONICAL_PRESERVE | Usage Ingestion / Read Models | High |
| CHAR-024 | Large/domain-specific semantic rules stay outside core | RASAi #190/#191; `ai_orchestration_unification.py` | AUD/RPR/M7/M20/M24, evidence/scoring/SARI/SCORE-GEO and client validation semantics are consumer-domain responsibilities, not orchestration-core semantics. | RASAI_DOMAIN_ONLY | External client/domain adapters only | High |
| CHAR-025 | Search Intelligence Perplexity is not canonical LLM registry behavior | RASAi #178/#180 and ORQETIA boundary issue #29 | Domain-specific search integration is a distinct boundary; it must not be silently admitted into generic LLM AUTO merely because it uses AI. | EVOLUTION_REQUIRES_ADR | Separate capability boundary | Medium |
| CHAR-026 | Provider-specific wire behavior is reusable behind a neutral protocol | `provider_extensions.py`, core providers, Copilot provider | Endpoint/auth/payload/error/usage extraction can be characterized and reused, but concrete SDK/env/domain coupling must not leak into consumer contracts. | GENERALIZE_NAME_ONLY | Provider Adapters | High |

## State semantics frozen for #34

The characterization suite must cover, at minimum:

1. adapter invocation does not perform logical retry;
2. provider deduplication inside a cycle;
3. pool re-evaluation between cycles;
4. sole timer ownership and bounded Retry-After;
5. terminal vs transient classification;
6. terminal quarantine surviving across needs in one session;
7. explicit provider as a one-element pool;
8. priced-before-UNPRICED ordering without pricing gate;
9. partial progress retained when completion is not reached;
10. total-token reasoning non-double-count;
11. per-attempt monetary precedence;
12. multi-currency separation;
13. UNPRICED counted separately from zero;
14. mixed native/token pricing remains UNPRICED without an explicit rule.

## RASAi coupling that must not enter ORQETIA core

Do not port core dependencies on:

- AUD/RPR terminology;
- M7/M18/M20/M24/etc. module semantics;
- evidence seal IDs as orchestration semantics;
- SARI/SCORE-GEO/Apdex/formula ownership;
- console/environment variable naming as a public ORQETIA contract;
- SQLite historical domain tables;
- report-specific schemas;
- provider secrets sourced directly from consumer environment variables.

## Portable elements

Suitable for extraction/generalization:

- execution policy data structures and cycle algorithm;
- normalized error taxonomy;
- bounded Retry-After calculation;
- health/quarantine algorithm;
- candidate ordering and UNPRICED semantics;
- provider/model/pricing catalog validation;
- usage/pricing pure calculations;
- provider wire contracts;
- attempt-level provenance concepts.

## Target issue mapping

- #34 — characterization suite for CHAR-001..CHAR-021 core invariants;
- #8 — execution policy/orchestration engine;
- #9/#10 — registry/adapters;
- #16 — usage/pricing/accounting;
- #6/#7/#15 — session/task/worker durability;
- #29 — Perplexity/search boundary;
- #43 — reuse strategy by source module.

## Change rule

If implementation intentionally differs from a **CANONICAL_PRESERVE** row, stop that port and open/resolve an ADR before merging the semantic change.
