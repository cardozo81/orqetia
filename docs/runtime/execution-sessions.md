# Durable Execution Sessions — #6

## Scope

`ExecutionSession` is the durable Execution-owned aggregate that groups multiple future Tasks
for one authenticated tenant/service-client pair. It is not an orchestration loop and does not
own Task state transitions.

The canonical client API remains the existing `POST /v1/sessions` and
`GET /v1/sessions/{session_id}` contract. This issue does not fabricate an effective policy
inside the HTTP layer: the effective policy and authorized target snapshot must come from the
Control Plane contract defined by #21/#50. The Phase-1 HTTP shell therefore remains fail-closed
until that application adapter and the #14 idempotent create boundary are composed.

## Ownership and persisted shape

Authoritative tables live only in the `execution` PostgreSQL schema:

- `execution.execution_sessions`;
- `execution.session_target_runtime`.

A session persists:

- `session_id`, `tenant_id`, `client_id`;
- lifecycle status;
- requested policy version reference when applicable;
- effective policy version reference;
- immutable authorized provider/model/reasoning target snapshot;
- optional external reference;
- opaque usage-fact references;
- opaque internal-cost fact references;
- creation/update/expiry/terminal timestamps;
- optimistic aggregate `version`;
- per-target health and quarantine state.

Financial amounts, provider credentials, provider-account identifiers and secret references are
not stored in the client-facing session aggregate. Usage and monetary truth remain owned by
Usage & Accounting; Execution stores only opaque references needed to reconstruct provenance.

## Lifecycle states

The session states are the states already published by the v1 OpenAPI contract:

- `ACTIVE`: tasks may be attached when later Task policy allows it;
- `EXPIRED`: execution validity ended by policy/time;
- `CANCELLED`: the session was cancelled under a future command contract;
- `CLOSED`: normal explicit terminal closure.

`EXPIRED`, `CANCELLED`, and `CLOSED` are terminal. A terminal row must have
`terminal_at`; an `ACTIVE` row must not.

This issue intentionally does not add session transition HTTP endpoints that are absent from the
canonical OpenAPI.

## Tenant/client isolation

Every authoritative session row carries both `tenant_id` and `client_id`.
Repository reads require the complete ownership scope and provide no unrestricted client-facing
`get(session_id)` helper.

`session_target_runtime` repeats tenant/client ownership and has a same-context composite
foreign key to the parent session. A child row with a mismatched tenant/client is rejected by
PostgreSQL. This is database defense in addition to application authorization; UUID secrecy is
never treated as authorization.

PostgreSQL RLS is not enabled by this issue because the runtime does not yet have the trusted
per-transaction tenant/client session-setting and least-privilege role composition needed to
make RLS safe. Adding an incomplete permissive RLS policy would weaken rather than strengthen
the boundary. The primary isolation contract here is ownership-scoped repository access plus
database consistency constraints.

## Health and quarantine

Health is persisted per authorized provider/model/reasoning target as
`UNKNOWN | HEALTHY | DEGRADED | UNAVAILABLE`.

Quarantine is `NONE | TEMPORARY | TERMINAL`.

- temporary quarantine requires an expiry timestamp;
- terminal quarantine has no expiry timestamp;
- once a target reaches `TERMINAL` quarantine in a session, the persistence adapter refuses
  an update that would weaken it to temporary/no quarantine;
- therefore terminal quarantine survives process restarts and future Tasks in the same session.

This issue persists the state only. Selection, retry and fallback decisions remain Phase 3 policy
engine work.

## Task relationship and cycle budget

The cardinality is session 1 → N Tasks. #7 will create the Task table and must repeat
`tenant_id`/`client_id` with a same-context ownership constraint to the session.

There is deliberately **no session-level cycle counter or cycle budget**. Each Task starts its
own policy-defined cycle budget. Session quarantine/health survives between Tasks; Task cycles do
not.

## Indexes and concurrency

Indexes support:

- tenant/client/status/creation-time owned-session access;
- expiry scanning;
- tenant/client/quarantine operational filtering.

A monotonically increasing session `version` changes when target runtime state changes.
PostgreSQL `READ COMMITTED` remains the repository default. Terminal quarantine is enforced by
a conditional UPSERT so a concurrent stale writer cannot silently clear it.

## Retention and privacy

This schema stores execution metadata and references, not raw prompts/outputs.

`expires_at` is an execution-validity timestamp, **not** the privacy-retention deadline.
Retention/deletion duration is controlled by the versioned #55 privacy/retention policy and must
not be inferred from a null expiry. No new "retain forever" default is introduced here.
Deletion/anonymization jobs remain future lifecycle implementation and must be idempotent and
auditable.

## Idempotency boundary

The canonical `POST /v1/sessions` requires `Idempotency-Key`. #14 requires the key mapping,
request fingerprint and created logical resource to be committed atomically. This issue does not
introduce a weaker ad-hoc mapping. HTTP creation remains unimplemented until the generic
execution-owned idempotency record/application transaction is composed with the Control Plane
policy snapshot.

## Non-goals

- Task schema/state machine (#7);
- provider simulator (#19);
- worker recovery orchestration (#15);
- routing/cost escalation/retry/cycles (#8, Phase 3);
- provider calls or paid credentials;
- financial truth or pricing formulas;
- modifications to RASAi.
