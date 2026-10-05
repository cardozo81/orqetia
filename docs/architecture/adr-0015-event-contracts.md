# ADR-0015 — Event contracts, transactional outbox/inbox and delivery semantics

- Status: **Accepted**
- Issue: #51
- Depends on: #14, #33, #49, #50
- Related: #52, #15, #18, #44
- Security controls: SEC-006, SEC-010, SEC-011, SEC-012

## Decision

ORQETIA uses three distinct interaction types:

1. synchronous request/response — direct typed call or HTTP;
2. asynchronous command/work item — asks a worker to do something;
3. integration/domain event — states a fact already committed by its authoritative owner.

Events are not used as a universal replacement for function calls.

Delivery model:

    at-least-once delivery
    +
    idempotent consumer
    =
    exactly-once logical effect where the local effect can be transactionally deduplicated

ORQETIA does not promise exactly-once message delivery.

## Event vs command

### Event

Past-tense immutable fact:
- task.completed;
- provider.attempt.failed;
- usage.recorded;
- credential.revoked.

An event consumer does not reinterpret the fact into a different historical truth.

### Command/work item

Imperative asynchronous request:
- execute_task;
- resume_task;
- publish_outbox;
- rebuild_projection;
- process_privacy_deletion.

Commands may be retried/leased/cancelled and have an execution state.

A command is **not** renamed to an event merely to put it on a broker.

## Provider-side effects

An event consumer does not directly issue a provider call.

If a consumed event implies new asynchronous/provider work:
1. consumer records its inbox/effect;
2. creates a stable #14 work/command identity in the same local transaction where possible;
3. worker later executes the command under the normal task/attempt idempotency model.

This prevents event redelivery from silently duplicating provider cost.

## Canonical event envelope

Conceptual JSON shape:

    {
      "event_id": "uuid-v7",
      "event_type": "task.completed",
      "event_version": 1,
      "producer": "execution",
      "occurred_at": "2026-10-05T00:00:00Z",
      "tenant_id": "uuid-or-null",
      "client_id": "uuid-or-null",
      "aggregate_type": "task",
      "aggregate_id": "uuid",
      "aggregate_version": 7,
      "correlation_id": "uuid-or-null",
      "causation_id": "uuid-or-null",
      "trace_id": "string-or-null",
      "data_classification": "CLIENT_PRIVATE",
      "payload": {}
    }

Rules:
- event_id is globally unique and immutable;
- occurred_at is the owner's commit/business-fact time, not consumer receipt time;
- tenant_id/client_id are required for tenant/client-owned facts;
- aggregate_version is included where ordering/version matters;
- correlation/causation are propagated from trusted runtime context;
- trace_id is observability metadata, not event identity;
- SECRET classification is prohibited.

## Event naming

Use lower-case dotted past-tense names:

    <subject>.<past-tense-fact>

Examples:
- task.created;
- task.completed;
- provider.attempt.failed;
- pricing.catalog.published.

Avoid:
- vague event;
- command-like task.execute;
- implementation names tied to one broker.

## Event versions

event_version is an integer contract version scoped to event_type.

Same version may add an optional field only when:
- old consumers safely ignore it;
- semantics of existing fields do not change;
- required behavior does not change.

Breaking changes require a new event_version and a compatibility transition.

Producer and consumer contract tests validate supported versions.

An old persisted event payload is never silently rewritten to match a new schema.

## Contract source

Phase 1 stores machine-readable JSON Schema contracts under a versioned repository path such as:

    contracts/events/<event_type>/v<event_version>.json

Generated language models may exist, but JSON Schema/contract tests remain the compatibility boundary.

AsyncAPI documentation may be generated later if it adds value; it is not required to choose a broker.

## Transactional outbox

An authoritative context that publishes an event writes:
- owner state/fact;
- outbox event

in the **same local PostgreSQL transaction**.

There is no:
1. commit owner state;
2. best-effort publish without durable outbox.

## Physical outbox ownership

Each bounded context owns its outbox records in its own persistence boundary/schema, using the shared envelope contract.

Examples:
- execution.outbox_events;
- accounting.outbox_events;
- control.outbox_events.

This avoids a cross-context write into a single shared business table.

A common infrastructure publisher may read approved outbox interfaces/tables without becoming business-data owner.

## Outbox fields

Baseline:
- event_id PK;
- event_type/version;
- producer;
- tenant/client/aggregate dimensions;
- aggregate_version;
- correlation/causation/trace;
- data_classification;
- payload jsonb;
- occurred_at;
- available_at;
- state;
- publish_attempts;
- next_attempt_at;
- last_error_class;
- claimed_by/claim_until;
- published_at;
- created_at.

States:
- PENDING;
- CLAIMED;
- PUBLISHED;
- DEAD.

## Publisher algorithm

Publisher:
1. claims a bounded batch with lease/row-lock semantics;
2. publishes event to broker/topic;
3. only after broker acknowledgement marks the outbox row PUBLISHED;
4. retries transient failures with bounded backoff/jitter;
5. moves deterministic/exhausted failures to DEAD and alerts.

Important crash window:

    broker accepted event
    ↓
    publisher crashes before marking PUBLISHED

The event will be published again. This is expected and why consumers must dedupe.

Marking PUBLISHED before broker acknowledgement is prohibited.

## Outbox cleanup

Outbox is a delivery/replay buffer, not the authoritative business fact store and not automatically an event-sourcing ledger.

Published rows may be compacted/archived after:
- configured replay/support window;
- downstream reconciliation policy;
- audit/incident requirements.

Cleanup never deletes authoritative task/attempt/accounting data.

## Consumer inbox

Each consumer has a stable consumer_name/version identity.

Inbox uniqueness:

    UNIQUE(consumer_name, event_id)

Baseline fields:
- consumer_name;
- event_id;
- event_type/version;
- received_at;
- state;
- processed_at;
- attempts;
- last_error_class.

States:
- RECEIVED;
- PROCESSING;
- PROCESSED;
- DEAD.

## Consumer algorithm

For local DB effects:
1. begin transaction;
2. insert/lock inbox identity;
3. if already PROCESSED, acknowledge duplicate;
4. validate event schema/version/classification;
5. apply idempotent local effect;
6. mark inbox PROCESSED;
7. create any resulting outbox/work item in the same transaction;
8. commit;
9. acknowledge broker delivery.

Crash before commit:
- broker redelivers;
- transaction did not persist effect.

Crash after commit before broker ack:
- broker redelivers;
- inbox detects PROCESSED and no duplicate effect occurs.

## External effects from consumers

If the effect is not transactionally local:
- do not call it directly inside the event handler;
- materialize a stable #14 command/work record and process through the appropriate worker.

This rule covers provider calls, webhooks and other external side effects.

## Ordering

No global event ordering is promised.

Where a consumer needs per-aggregate order:
- producer includes aggregate_version;
- broker partition/routing key should use aggregate identity when supported;
- consumer detects duplicate, gap and stale version.

Out-of-order handling:
- stale already-applied version -> ignore/dedupe;
- future version with gap -> bounded retry/buffer or mark reconciliation needed;
- never invent missing state.

Projection/reconciliation logic may rebuild from authoritative state/facts when ordering cannot be recovered cheaply.

## Late events

Late events are valid.

Rollup/read-model consumers use:
- occurred_at;
- watermark;
- dirty-bucket/rebuild semantics from #44.

Consumer receipt time is not substituted for occurrence time in business/statistical aggregation unless the metric explicitly measures processing latency.

## Payload size

Broker messages are metadata/small-contract payloads.

Prohibited:
- large raw prompt;
- large result/blob;
- provider secret/token;
- binary artifact;
- arbitrary full database row dump.

For large CLIENT_PRIVATE payloads:
- persist in the authoritative/protected payload store;
- event carries opaque resource/blob reference plus integrity/version metadata as needed;
- consumer re-authorizes/uses service identity to retrieve.

Object storage decision remains a separate payload-storage implementation decision.

## Data classification

Every event declares data_classification from #61.

Rules:
- SECRET: prohibited;
- RESTRICTED/CONFIDENTIAL: only on explicitly restricted internal channels/consumers when necessary;
- CLIENT_PRIVATE: tenant/client scope required;
- PUBLIC: only explicitly approved data.

Event classification cannot be lower than its payload.

Broker ACL/retention/encryption must support the highest allowed class on the channel.

## Trust boundary

Broker-delivered event is not trusted merely because it is JSON.

Consumer validates:
- expected producer/channel identity through infrastructure trust;
- event type/version;
- required IDs;
- tenant/client scope;
- schema;
- size;
- classification;
- aggregate/version invariants.

Client-supplied event envelope fields are never accepted as internal events.

## Correlation and causation

- correlation_id groups a logical cross-context flow;
- causation_id points to the event/command/request that directly caused this fact;
- event_id is the event's own identity.

Do not overload trace_id as a durable idempotency/causation identifier.

## Retry policy

Publisher/consumer retry distinguishes:
- transient infrastructure failure;
- deterministic schema/unsupported-version failure;
- application conflict requiring reconciliation.

Retry count/backoff is bounded/configurable.

Repeated deterministic failure enters DEAD/DLQ state; infinite hot-loop retry is prohibited.

## Dead-letter / poison handling

A DEAD event/work item retains:
- event ID;
- safe envelope metadata;
- failure class;
- attempts;
- timestamps;
- reference to payload under its classification.

Recovery tooling:
- inspect with authorized role;
- fix consumer/config/code;
- replay the **same event_id** when the original fact remains valid;
- or emit a new corrective event when business truth changes.

Do not mutate historical event payload and pretend it was the original fact.

## Replay

Replay is explicit and audited.

Consumer replay:
- may clear/retry a failed inbox state for the same event;
- never bypasses #14 idempotency;
- may rebuild a projection into a new projection version.

Bulk replay:
- bounded;
- rate-limited;
- has checkpoint;
- can resume after interruption.

## Event catalog

Initial candidate events are in docs/architecture/event-catalog-v1.md.

Publishing an event type that has no owner/schema/version is prohibited.

## Observability

Track:
- outbox pending count/age;
- publish latency;
- publish retries/dead count;
- broker delivery/consumer lag;
- inbox duplicates;
- processing latency;
- unsupported schema/version;
- aggregate sequence gaps;
- replay volume.

No SECRET/raw content in metrics labels.

## Broker independence

Application code depends on:
- EventPublisherPort;
- EventEnvelope;
- Inbox/Deduplication abstraction where appropriate.

Concrete broker implementation is selected in #52.

Domain models do not import a broker SDK.

## Consequences

Positive:
- atomic owner-state/event intent;
- safe redelivery;
- explicit duplicate semantics;
- broker remains replaceable;
- event history does not become accidental second source of truth.

Costs:
- outbox/inbox storage and cleanup;
- duplicate delivery is normal and must be tested;
- consumers that need order must handle gaps/late events explicitly.
