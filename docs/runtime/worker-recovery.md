# Worker, lease and provider-dispatch recovery — #15

## Scope

#15 composes the PostgreSQL work-queue foundation (#104), process shells (#105),
durable Tasks (#7) and the deterministic provider adapter (#19) into a crash-safe
single-attempt execution boundary.

It does **not** implement orchestration policy. Candidate ranking, provider fallback,
logical retry count, cycles and cost escalation remain #8.

## Durable work semantics

`messaging.work_items` remains the authoritative infrastructure work record.

Claiming uses `FOR UPDATE SKIP LOCKED` and:

- only due work is claimable;
- active leases are exclusive;
- expired leases may be reclaimed while infrastructure attempts remain;
- an expired lease whose `attempt_count` reached
  `max_infrastructure_attempts` is terminalized as `DEAD` instead of being
  reclaimed forever;
- deterministic poison/unsupported operation is explicitly dead-lettered;
- stale lease owners cannot complete/requeue/dead-letter after losing ownership.

A handler exception does not mean provider retry. The lease is allowed to expire unless
the handler explicitly classifies the error as an infrastructure requeue.

## Provider attempt persistence

Execution owns `execution.provider_attempts`.

A provider attempt stores:

- stable `attempt_id` from ADR-0018;
- exactly one durable `work_id`;
- tenant/client/session/task scope when task-scoped;
- operation;
- provider/model/reasoning target;
- cycle and attempt index;
- request reference + fingerprint;
- retry/fallback lineage IDs;
- normalized result metadata;
- timestamps and optimistic version.

Taskless shape remains representable for future operation contracts, but ordinary Phase-2
task execution schedules attempts under an owned RUNNING task.

## Atomic scheduling

`PostgresProviderAttemptStore.schedule()` uses one PostgreSQL transaction to:

1. lock/validate the task when task-scoped;
2. validate task/session ownership;
3. validate operation;
4. require task state `RUNNING`;
5. enforce EXPLICIT_TARGET exact target equality or AUTO session-policy target admission;
6. append the stable attempt ID to the Task provenance reference list;
7. insert the provider-attempt row;
8. insert exactly one execution work item.

If the work insert fails, the attempt insert and Task reference update roll back together.

This is intentionally not the public API idempotency transaction from #14; it is the internal
dispatch transaction used after the logical attempt already exists.

## Provider-dispatch state machine

Attempt states:

- `READY`: persisted and queued, provider dispatch not started;
- `DISPATCHING`: the durable pre-dispatch marker has committed;
- `COMPLETED`: normalized provider result persisted;
- `AMBIGUOUS`: dispatch may have happened but a safe durable result cannot be proven.

### Why DISPATCHING is committed before the call

A worker changes `READY -> DISPATCHING` before invoking the adapter.

If the process crashes:

- before that commit: redelivery still sees `READY` and may dispatch;
- after that commit but before/during/after the external call: redelivery sees
  `DISPATCHING` and **does not invoke the provider again**;
- the redelivery terminalizes the attempt as `AMBIGUOUS`.

This is intentionally conservative. Without verified upstream idempotency there is no safe way
to distinguish "crashed one instruction before the network call" from "provider completed but
the response was lost". False ambiguity is preferable to an untracked duplicate provider call
or duplicate accounting effect.

A future real adapter that supports verified upstream idempotency may add an explicit replay
contract. #15 does not assume one.

## Duplicate work after a completed result

If the same durable work is reintroduced/redelivered after `COMPLETED`, the handler sees the
terminal attempt and completes the work without calling the adapter again.

Therefore queue at-least-once delivery does not imply repeated provider dispatch after a durable
attempt result.

## Adapter exceptions

Once `DISPATCHING` has committed, an unexpected adapter exception is treated as an ambiguous
external outcome. The attempt becomes `AMBIGUOUS` and the work is completed.

The queue does not convert that exception into an orchestration retry.

Provider-normalized outcomes such as rate limit, timeout, quota, auth failure or transient error
are persisted as `COMPLETED` attempt outcomes. #8 later decides whether those facts create a
new logical attempt/cycle.

## Target boundary

For task-scoped scheduling:

### EXPLICIT_TARGET

The provider/model/reasoning target must equal the frozen Task effective target exactly.
Infrastructure cannot replace the target.

### AUTO

The chosen attempt target must exist in the Session authorized-target snapshot. #15 does not
rank that pool; it only prevents an infrastructure caller from dispatching outside it.

## Cancellation

Task completion/cancellation arbitration remains the compare-and-set state machine from #7.
The provider-attempt handler does not transition Task terminal state and therefore cannot
overwrite a cancellation winner.

A provider call already in flight cannot be made "uncalled". Its result/ambiguity remains
durable evidence even if the owning Task subsequently becomes CANCELLED.

## Delayed work and Retry-After

The transport already supports `available_at`. #15 proves delayed work is not claimable early.

A provider `Retry-After` result is only persisted as attempt metadata. #15 does not schedule
the next provider attempt from that metadata. #8 owns the logical retry decision and will
materialize future work with the chosen `available_at`.

No web-process `sleep` owns canonical retry timing.

## Graceful shutdown and heartbeat

The existing WorkerProcess:

- renews leases while handlers run;
- rejects completion after lease loss;
- drains in-flight work for a bounded grace interval;
- leaves unfinished durable work recoverable after process exit.

These behaviors remain broker-neutral through `WorkQueuePort`.

## Failure matrix

| Failure point | Durable state | Redelivery behavior |
|---|---|---|
| before attempt/work transaction | nothing | caller retries scheduling transaction |
| attempt/work transaction committed, before claim | READY | normal claim/dispatch |
| worker crash before READY→DISPATCHING commit | READY | safe to dispatch |
| crash after DISPATCHING commit | DISPATCHING | mark AMBIGUOUS, no provider replay |
| provider result persisted, before work DONE | COMPLETED | complete redelivery, no replay |
| unsupported work operation | work DEAD | no repeated poison claim |
| infrastructure handler failure before external effect | lease expires/requeues boundedly | normal infra recovery |
| infrastructure attempts exhausted | work DEAD | no further claim |

## Tests

PostgreSQL integration tests prove:

- exclusive claims;
- heartbeat/lease renewal;
- expiry/reclaim;
- attempt exhaustion -> DEAD;
- explicit poison dead-letter;
- delayed `available_at`;
- atomic attempt/work/task-reference scheduling;
- completed-attempt duplicate delivery without provider replay;
- DISPATCHING redelivery -> AMBIGUOUS without provider replay;
- adapter exception -> AMBIGUOUS;
- AUTO target authorization;
- EXPLICIT_TARGET target immutability.

All provider calls use `ORQETIA_TEST_PROVIDER`; no paid credential or network is required.

## Non-goals

- policy-engine retry/fallback/cycles (#8);
- full observability stack (#18);
- real provider registry/adapters (#9/#10);
- provider pricing/accounting truth;
- public task/session command wiring;
- RASAi changes.
