# Canonical orchestration policy — #8

## Scope

Issue #8 owns the canonical orchestration decision policy for task execution. It
implements the accepted AUTO / EXPLICIT_TARGET contract without moving provider I/O
out of the durable worker path introduced by #15.

The policy engine is deliberately **stateless**. It consumes durable facts and returns
the next decision. A process restart must reconstruct the same decision from:

- the owned ExecutionSession and immutable policy version;
- the durable ExecutionTask requirement partition and current cycle;
- the persisted TaskCycleDecision candidate order;
- terminal provider-attempt facts;
- session-scoped health/quarantine state;
- the resolved administrative policy limits and candidate metadata.

No hidden in-process retry counter, health map or cycle state is authoritative.

## AUTO

AUTO filters candidates by:

- session authorization;
- candidate eligibility supplied by the control/capability boundary;
- session health;
- temporary/terminal quarantine.

Pricing never creates eligibility.

Within an administrative comparison group, priced candidates are ordered by lower
estimated provider cost and then deterministic policy/target tie breakers. Different
comparison groups are ordered only by explicit administrative
`comparison_group_rank`; the engine does not invent FX or native-unit conversion.
UNPRICED remains eligible and never means zero cost.

Each TaskCycleDecision freezes one candidate order. A target can appear at most once in
that cycle. A later cycle rebuilds and re-ranks the candidate pool, so cost, health,
quarantine and remaining requirements may change the order.

## EXPLICIT_TARGET

EXPLICIT_TARGET filters the candidate pool to the task's already-authorized frozen
effective target. Cost metadata is ignored for target selection.

Transient/no-progress outcomes may create a later cycle for the same target while
policy budget remains. A terminal provider outcome terminally quarantines the target
and ends the explicit execution without cross-provider/model/profile fallback.

## Requirement progress

Provider SUCCESS must satisfy every currently missing requirement.

PARTIAL must accept a strict non-empty subset and return the exact remaining missing
partition. Accepted requirements are preserved and the next AUTO candidate receives
only the remaining work.

Non-progress outcomes cannot claim accepted requirements or rewrite the missing set.

## Retry, delay and budgets

ORQETIA remains the single timer owner.

The engine:

- applies `max_attempts`;
- applies `max_cycles`;
- applies an optional absolute deadline;
- bounds Retry-After by the administrative cap (maximum 300 seconds);
- combines the bounded Retry-After with the configured cycle delay;
- never sleeps inside a provider adapter or request handler.

The returned delay is materialized later through the existing durable
`WorkItem.available_at` path.

## Terminal provider outcomes and quarantine

These normalized outcomes request terminal session quarantine:

- TERMINAL_ERROR;
- CREDIT_EXHAUSTED;
- QUOTA_EXHAUSTED;
- AUTH_FAILURE.

AUTO may continue to the next already-frozen candidate in the same cycle. Before a new
cycle the caller persists the quarantine update, so the next candidate rebuild excludes
that target.

Transient/rate-limit/timeout/unavailable results do not create terminal quarantine.

## Crash ambiguity

An AMBIGUOUS durable provider attempt immediately produces
`AMBIGUOUS_PROVIDER_EFFECT` and no automatic redispatch. This preserves #15's
no-duplicate-provider-effect boundary.

## Worker boundary

The policy engine never invokes a provider.

A DISPATCH decision supplies the exact target, cycle, attempt index and current missing
requirements. The application/runtime layer materializes a new durable ProviderAttempt
and provider work item. The existing worker then performs the single external attempt.

Therefore:

- provider adapters remain single-attempt;
- worker infrastructure retry stays separate from logical retry;
- normal logical retry/fallback creates a new attempt_id;
- the same provider is never selected twice in one frozen cycle;
- #9 remains responsible for the real provider registry/capability implementation.

## Observability

The caller can combine the cycle decision and resulting provider-attempt facts with the
#18 correlation contract. #8 does not log provider cost values or expose financial
metadata to service clients.

## Test contract

The dedicated orchestration gate uses ORQETIA_TEST_PROVIDER only and proves:

- lower comparable AUTO cost first;
- cheap success prevents expensive dispatch;
- no-progress escalates to the next AUTO candidate;
- partial progress preserves accepted/missing;
- each new cycle re-evaluates ordering;
- UNPRICED remains eligible;
- different economic groups never use implicit FX;
- EXPLICIT_TARGET ignores cheaper alternatives;
- explicit retry stays on the same provider/model/reasoning target;
- explicit terminal failure never falls back cross-target;
- terminal AUTO failure requests quarantine and may continue;
- health/quarantine remove candidates;
- Retry-After is bounded;
- attempt/cycle/deadline budgets terminate deterministically;
- AMBIGUOUS never silently redispatches;
- cancellation prevents a new provider attempt.

All tests are offline and have zero variable provider cost.
