# Durable provider dispatch and worker recovery — #15

## Scope

This issue composes the Phase-1 PostgreSQL work queue and worker shells with the Phase-2
Session/Task runtime. It deliberately does **not** implement orchestration policy.

The worker receives a pre-resolved provider attempt. Selection, retry, fallback, cycle creation,
cost escalation and Retry-After policy remain #8 responsibilities.

## Reused infrastructure

The implementation reuses the existing messaging/runtime primitives:

- PostgreSQL `messaging.work_items`;
- `FOR UPDATE SKIP LOCKED` claims;
- leases and heartbeat renewal;
- stale lease rejection;
- `available_at` delayed work;
- infrastructure retry/dead-state;
- `WorkerProcess` and `HandlerRegistry`;
- scheduler polling/wakeup fallback.

No second queue or broker is introduced.

## Infrastructure retry budget and poison work

`max_infrastructure_attempts` is authoritative for both explicit infrastructure
requeues and crash/lease-expiry recovery.

When an expired `LEASED` work item has already consumed its configured
infrastructure-attempt budget, the next queue scan moves it to `DEAD` instead of
claiming it again. This prevents a repeatedly crashing handler from bypassing the
retry budget merely by failing before it can explicitly requeue.

The queue also exposes an ownership-checked `dead_letter` operation. A claimed
operation for which the worker has no registered handler is deterministic poison and
is dead-lettered as `UNSUPPORTED_OPERATION`; it is not left to expire and cycle
indefinitely.

Dead-letter, completion and requeue all require the current non-expired lease owner,
so a stale worker cannot terminalize work after another worker has reclaimed it.

## Provider attempt journal

`execution.provider_attempts` is the durable dispatch journal. One normal provider dispatch has
one stable `attempt_id` from ADR-0018.

States:

| State | Meaning |
|---|---|
| `PREPARED` | Attempt and request identity are durable; no provider dispatch has started. |
| `DISPATCHING` | One durable work item won the dispatch CAS and may call the provider. |
| `COMPLETED` | A normalized provider result is durable. |
| `AMBIGUOUS` | Dispatch may have occurred but no trustworthy result is durable; automatic redispatch is prohibited. |
| `CANCELLED` | Task became non-runnable before dispatch began. |

The journal stores provider/model/reasoning identity, cycle/attempt index, request reference and
fingerprint, normalized outcome/error/retry-after/latency and accepted/missing snapshots.

It does not store provider credentials, account identity, monetary provider cost, pricing terms or
raw prompt/output bodies.

## Work identity vs attempt identity

`work_id` and `attempt_id` are separate identities.

The first work item that claims a `PREPARED` attempt records its `work_id` while moving the
attempt to `DISPATCHING`.

If another **different** work item references the same attempt while it is dispatching, it is a
duplicate delivery. It does not call the provider and does not invalidate the active dispatch.

If the **same** work item is reclaimed after its lease expires while the attempt is still
`DISPATCHING`, the previous dispatch result is unknowable. Recovery marks the attempt
`AMBIGUOUS` and does not call the provider again.

This distinction prevents an at-least-once queue from becoming an at-least-once provider call.

## Crash boundaries

### Crash before provider dispatch

The attempt remains `PREPARED`. Infrastructure failures such as adapter-resolution failure may
requeue the same work item through the existing infrastructure retry budget.

### Crash after result persistence but before work ACK

The attempt is already `COMPLETED`. Reclaiming the work item observes the terminal journal and
skips the provider call. The new lease may safely converge the work item to `DONE`.

### Crash/uncertainty after dispatch but before result persistence

The attempt remains `DISPATCHING`. Reclaim of the same work item terminalizes it as
`AMBIGUOUS`.

Without verified upstream idempotency, ORQETIA does not guess whether the provider processed the
request and does not silently dispatch a second time.

A future adapter that supports verified upstream idempotency may define replay of the same
logical attempt using the same `attempt_id`, but that capability is not inferred by #15.

## Provider outcomes are not infrastructure retries

A normalized provider result such as:

- transient error;
- rate limit;
- Retry-After;
- timeout;
- quota/credit exhaustion;
- auth failure;
- requirement not satisfied;

still means the **work item executed successfully as infrastructure**. The handler persists the
provider result and completes the work item.

#8 later decides whether that logical outcome creates a new attempt/cycle and, if delayed, a new
work item with the appropriate `available_at`.

## Cancellation

Before changing `PREPARED -> DISPATCHING`, the attempt store re-checks the owned Task.

If the Task is no longer `RUNNING`, the attempt becomes `CANCELLED` and the provider is not
called.

A cancellation that arrives after dispatch has already begun is handled by the Task CAS contract
from #7; #15 does not pretend it can always revoke an external call already in flight.

## Delayed execution

`build_provider_attempt_work_item()` materializes provider work using the existing
`available_at` contract.

The queue will not claim future work early. Polling discovers due work even if a wakeup/NOTIFY is
lost. No request thread or worker uses `sleep` for Retry-After scheduling.

## Horizontal workers

Exactly one work lease is active for a work item. In addition, the attempt journal provides a
second semantic CAS boundary so duplicate work items cannot cause duplicate provider dispatch.

Heartbeat, reclaim and stale work completion behavior remain owned by the reusable worker/queue
foundation.

## Current composition boundary

The production worker composition root still has no provider registry or orchestration engine.
Therefore #15 exposes the provider-attempt handler but does not auto-register it in
`apps/worker/main.py`.

#8/#9 will compose the handler with the actual target resolver/adapter registry. Tests inject
`ORQETIA_TEST_PROVIDER` directly.

## Non-goals

- AUTO ranking/cost escalation;
- logical retry/fallback/cycle policy;
- provider registry/capability discovery;
- financial accounting;
- distributed tracing/dashboard implementation;
- real provider calls or credentials;
- RASAi modification.
