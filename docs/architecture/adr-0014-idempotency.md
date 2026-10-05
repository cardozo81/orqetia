# ADR-0014 — End-to-end idempotency and duplicate-effect prevention

- Status: **Accepted**
- Issue: #14
- Depends on: #33, #50
- Related: #51, #52, #15
- Security controls: SEC-006, SEC-010, SEC-011
- Amended by: ADR-0018 (#86/#87) for stable attempt identity and normal retry/fallback identity semantics

## Decision

Idempotency is an end-to-end contract, not only an HTTP header.

ORQETIA uses distinct but correlated mechanisms for:
1. client API request replay;
2. durable work deduplication;
3. provider attempt dispatch/recovery;
4. accounting/quota uniqueness;
5. event consumer deduplication.

The goal is **exactly-once logical effect where required**, implemented on top of at-least-once transport and local transactions.

ORQETIA does not claim exactly-once network delivery.

## Public API contract

Mutating/repeatable operations that require idempotency accept:

    Idempotency-Key: <opaque client-generated value>

Initial coverage:
- session creation where the API supports creation;
- task creation;
- estimate requests when designated idempotent/cacheable;
- future credential actions where a duplicate would be harmful;
- future webhook registration/mutation as applicable.

Cancellation is naturally idempotent by resource/state and may additionally accept an idempotency key when the endpoint contract requires request replay.

## Key requirements

Recommended client key:
- opaque;
- high entropy;
- stable for retries of the same logical request;
- <= 200 UTF-8 bytes in baseline;
- not a password/token/secret.

ORQETIA stores a cryptographic hash/fingerprint of the key rather than relying on the raw value in logs/read models.

Scope:

    tenant_id + client_id + API version/operation + key_hash

The same literal key may therefore be used for unrelated operations without collision.

## Request fingerprint

The server computes a versioned request fingerprint from the validated semantic request, not from arbitrary raw JSON byte order.

Fingerprint includes values that influence the logical effect:
- API/operation version;
- route/path resource identity;
- validated request body fields;
- relevant semantic headers/options;
- authenticated tenant/client scope is represented by the key scope.

Fingerprint excludes:
- trace/correlation ID;
- transport timestamps;
- irrelevant header order;
- authentication token bytes.

Suggested form:

    SHA-256(canonical_versioned_request_representation)

Changing canonicalization version is explicit and compatible with stored records.

## Same key behavior

### Same key + same fingerprint

No new logical effect.

If the original operation is complete:
- return/reconstruct the same logical response/resource;
- include current resource representation only if endpoint semantics explicitly choose that model;
- never create another resource/provider effect.

If still in progress:
- return the same bound resource/status when available; or
- return a deterministic in-progress response such as 409/202 with safe retry guidance.

### Same key + different fingerprint

Return:

    409 Conflict
    code = IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST

Do not replace the existing record or run the new effect.

## Idempotency record

Logical fields:
- id;
- tenant_id;
- client_id;
- operation;
- key_hash;
- request_fingerprint;
- canonicalization_version;
- status;
- resource_type/resource_id;
- response_status;
- safe response snapshot/reference if needed;
- created_at;
- completed_at;
- expires_at;
- error/retry metadata that is safe to persist.

Unique constraint:

    (tenant_id, client_id, operation, key_hash)

States:
- PENDING;
- IN_PROGRESS;
- SUCCEEDED;
- FAILED_FINAL;
- EXPIRED.

A retryable infrastructure failure does not delete the original idempotency identity.

## Transactional creation

For resource creation:
1. validate auth/request;
2. begin local transaction;
3. insert idempotency record or load conflicting existing record;
4. verify fingerprint;
5. create/bind the logical resource;
6. persist resource reference on idempotency record;
7. commit;
8. enqueue async work through transactional outbox when required.

The resource and its API-idempotency binding become visible atomically inside the owning context.

A second concurrent request cannot create a second resource.

## API replay retention

Baseline API replay window:
- at least 24 hours for externally supplied idempotency keys;
- operation-specific policy may retain longer;
- task/session creation should retain mapping long enough for normal client/network retry behavior.

Expiration of the API replay record does **not** erase internal task/attempt/accounting dedupe identities.

Clients must not assume a key remains reserved forever after documented expiry.

## Response storage

Prefer storing:
- logical resource ID;
- stable response status;
- small safe response envelope.

Avoid duplicating:
- raw prompt/output;
- SECRET;
- large result body.

A replay can reconstruct an authorized representation from the resource where safe.

Authorization is rechecked on replay; possession of an idempotency key does not grant resource access.

## Durable work deduplication

Every queued logical work item has a stable work_id / operation_id.

Consumer claim uses:
- unique work identity;
- durable state;
- lease/attempt metadata.

Redelivery of the same work item:
- reclaims/resumes according to state;
- does not create a second logical task/action.

A lease expiring means another worker may resume the work; it does not mean already committed side effects are repeated.

## Provider attempts

A provider call has a durable `attempt_id` created **before** network dispatch.

ADR-0018 clarifies:
- a normal orchestration retry/fallback that causes a new provider dispatch creates a **new** attempt_id, even when the request payload is identical;
- only recovery of the same ambiguous dispatch through a verified provider-side idempotency mechanism may reuse the same attempt_id;
- request fingerprint/hash never substitutes for attempt identity.

Logical uniqueness includes enough context to distinguish intended calls, e.g.:
- task;
- cycle;
- provider;
- attempt ordinal/logical attempt ID.

State model includes:
- PLANNED;
- CLAIMED;
- DISPATCHING;
- SUCCEEDED;
- FAILED_KNOWN;
- AMBIGUOUS_EXTERNAL_OUTCOME;
- CANCELLED_BEFORE_DISPATCH.

## External provider idempotency capability

Adapter metadata declares whether the provider/API supports a safe request-idempotency token for the relevant operation.

If supported:
- derive/send the provider idempotency token from stable ORQETIA attempt identity;
- retries of the same attempt reuse the same provider token;
- provider-specific semantics are covered by adapter contract tests.

If unsupported:
- ORQETIA does not pretend the external call is exactly-once.

## Crash after provider dispatch

The hardest case:

    request may have reached provider
    ↓
    worker crashes before response/usage persistence

If there is no provider-side idempotency/reconciliation proof:
- mark/recover the attempt as AMBIGUOUS_EXTERNAL_OUTCOME;
- **do not automatically dispatch the same logical provider attempt again**;
- do not fabricate zero usage/cost;
- retain reconciliation/audit evidence;
- orchestration recovery must explicitly decide whether to continue with a different/new attempt under policy, counting the ambiguity as a real risk/budget event.

This prevents a crash retry from silently issuing the same provider call twice.

If provider-side idempotency is supported, the same attempt may be safely retried using the same provider idempotency token according to that provider's verified semantics.

## Accounting idempotency

Every usage/cost/native-usage fact is keyed to a stable source identity.

Examples:
- usage fact unique by attempt + usage component/source;
- provider cost fact unique by attempt + cost basis/version/source;
- quota reservation unique by reservation_id/logical operation;
- quota reconciliation unique by source event/reconciliation ID.

Replaying an attempt/event cannot create a second debit/cost row for the same source fact.

Observed and estimated cost remain distinct facts per canonical accounting semantics.

## Quota idempotency

Before a costly effect when quota requires reservation:
- create/resume a stable reservation ID;
- repeated reservation request returns the same reservation;
- commit/release/reconcile operations are idempotent;
- a worker retry cannot consume the same quota twice.

Quota owner remains Usage & Accounting per ADR-0006.

## Scheduler idempotency

Scheduled actions use a stable action identity derived from the logical task/state transition, not scheduler delivery count.

Duplicate wakeups:
- acquire the same state transition/lease;
- observe it already executed or execute it once;
- do not create an extra cycle/provider call.

## Cancellation idempotency

Cancellation is a state transition request.

Repeated cancel:
- returns the same terminal/cancelling state;
- does not generate duplicate provider cancellation/accounting events;
- cannot resurrect a task.

Race with completion is resolved through version/lock/state-machine rules.

## Event consumer idempotency

#51 defines transport/event contracts.

Baseline here:
- every integration event has event_id;
- consumer inbox has unique (consumer, event_id);
- side effect and inbox completion occur in the same local transaction where possible;
- duplicate event is acknowledged/ignored after verifying prior completion.

## Webhooks

Future outbound webhook delivery:
- stable event/delivery ID;
- signature/timestamp;
- retries reuse the same logical event ID;
- receiver is expected to dedupe;
- ORQETIA delivery ledger prevents creation of duplicate logical notification events even though network attempts may repeat.

## Locks and concurrency

Use database uniqueness/state transitions first.

Pessimistic lock/advisory lock is used narrowly when required to serialize:
- initial idempotency creation conflict;
- work lease claim;
- provider attempt dispatch transition;
- cancellation/state race.

Do not hold DB locks during provider HTTP calls.

## Security

Idempotency keys:
- are not authentication;
- do not bypass ownership checks;
- are not trusted tenant/client identifiers;
- are bounded in length/rate to avoid storage abuse;
- raw values are not broadly logged.

A replay response performs normal authorization for the current principal.

## Failure semantics

Never turn uncertainty into success.

Cases:
- DB commit unknown: reload by idempotency/work key before attempting effect;
- provider outcome unknown: AMBIGUOUS_EXTERNAL_OUTCOME unless provider idempotency proves safe replay;
- accounting event duplicate: dedupe by stable source;
- outbox delivery duplicate: consumer inbox/effect idempotency;
- stale worker: version/lease check rejects state mutation.

## Metrics

Track:
- idempotency hits;
- key conflicts;
- in-progress replays;
- work redeliveries;
- inbox duplicates;
- ambiguous provider outcomes;
- prevented duplicate accounting;
- stale lease/state conflicts.

Metrics use safe IDs/counts, not raw idempotency keys.

## Consequences

Positive:
- client/network retries are safe;
- worker/broker retries do not silently duplicate logical work;
- provider crash ambiguity is explicit instead of hidden;
- accounting/quota facts have stable uniqueness.

Costs:
- more durable state and cleanup;
- provider exactly-once cannot be guaranteed when upstream lacks idempotency;
- recovery logic must handle AMBIGUOUS_EXTERNAL_OUTCOME as a first-class state.
