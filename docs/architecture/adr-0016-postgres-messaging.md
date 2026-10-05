# ADR-0016 — Queue/topic strategy: PostgreSQL-first durable messaging

- Status: **Accepted**
- Issue: #52
- Depends on: #2, #3, #51
- Related: #14, #15, #44
- Security controls: SEC-006, SEC-007, SEC-010, SEC-011, SEC-012

## Decision

ORQETIA starts with a **plain PostgreSQL-backed durable work/event-delivery transport**.

No Redis, RabbitMQ, NATS, Kafka, Redpanda or Celery broker/framework is required for the initial architecture.

PostgreSQL is already the selected durable OLTP dependency. Using it for the initial queue:
- minimizes operational components;
- preserves one local transaction boundary for enqueue/outbox transitions;
- supports multi-worker claiming with row locks/SKIP LOCKED;
- supports delayed work through available_at;
- supports leases, retry state and dead-letter state explicitly;
- keeps canonical orchestration retry semantics under ORQETIA control.

This is an infrastructure choice, not a claim that PostgreSQL is the final high-throughput streaming platform.

## Official primitive

PostgreSQL row locking with FOR UPDATE SKIP LOCKED is used only for queue claiming.

It is intentionally not used as a general consistency shortcut.

LISTEN/NOTIFY may be used as an optional low-latency wake-up hint. Durable queue state remains in tables because notifications are not the authoritative work record.

## Infrastructure schema

Add a non-business infrastructure schema:

    messaging

It is not a bounded-context source of truth.

Initial tables:
- messaging.work_items;
- messaging.event_deliveries.

Bounded-context outbox/inbox remain owned as defined by ADR-0015.

## Application ports

Application code depends on:
- WorkQueuePort;
- WorkLease;
- EventTransportPort;
- Scheduler/WakeupPort.

No domain/application package imports Psycopg-specific queue SQL or a future broker SDK.

## Work item contract

Baseline fields:

- work_id UUIDv7 PK;
- queue_name;
- operation_type;
- operation_version;
- tenant_id/client_id when scoped;
- resource_type/resource_id;
- data_classification;
- payload jsonb or protected payload reference;
- priority;
- state;
- available_at;
- attempt_count;
- max_infrastructure_attempts;
- lease_owner;
- lease_until;
- last_error_class;
- created_at;
- started_at;
- completed_at;
- dead_at;
- correlation_id/causation_id/trace_id;
- idempotency/logical_operation reference from #14.

States:
- READY;
- LEASED;
- DONE;
- DEAD;
- CANCELLED.

Work-item state is transport/execution infrastructure state, not the business task state.

## Queue names / criticality

Separate logical queues prevent low-priority work from starving execution.

Initial queue classes:

### execution
Critical:
- execute/resume task;
- orchestration/provider work;
- cancellation follow-up.

Dedicated worker concurrency and strict backpressure.

### scheduler
Critical timing:
- due task/cycle wake-up;
- delayed internal retry;
- lease/recovery scan trigger when modeled as work.

Scheduler wake-up never owns canonical provider retry policy; it only activates the durable state already decided by Execution.

### accounting
High integrity, less latency-sensitive:
- usage ingestion;
- pricing application/reconciliation;
- quota reconciliation.

### projection
Eventually consistent:
- read-model updates;
- operational rollups;
- CLIENT_ONLY/statistical rollups.

### maintenance
Low priority:
- privacy deletion/backfill;
- cleanup/retention;
- bulk rebuild;
- repair/reconciliation jobs.

Security/incident urgent maintenance may receive a dedicated queue/policy rather than sharing ordinary bulk maintenance.

## Priority

Priority exists **within** a logical queue.

Do not create a single global priority queue where high-volume low-value tasks can influence critical scheduling.

Baseline ordering for claim:
1. highest priority;
2. earliest available_at;
3. stable time-local ID/order.

Starvation prevention is tested; priority ranges are bounded and server-controlled.

Clients cannot choose arbitrary internal priority.

## Claim algorithm

Conceptually:

    BEGIN;

    SELECT id
    FROM messaging.work_items
    WHERE queue_name = :queue
      AND state = 'READY'
      AND available_at <= now()
    ORDER BY priority DESC, available_at, work_id
    FOR UPDATE SKIP LOCKED
    LIMIT :batch;

    UPDATE selected rows
    SET state = 'LEASED',
        lease_owner = :worker,
        lease_until = :deadline,
        attempt_count = attempt_count + 1;

    COMMIT;

Processing occurs **outside** the claim transaction.

Completion/failure uses expected lease/version checks so a stale worker cannot finalize work after losing its lease.

## Leases

Lease duration:
- exceeds the expected atomic handler interval;
- is renewable/heartbeat-capable for long work;
- is not used to infer provider-call success.

If a worker dies:
- lease expires;
- work can be reclaimed;
- #14 handler/idempotency state decides what may safely repeat.

Lease expiry never means "repeat provider call blindly".

## Delayed work

Delayed work uses:

    available_at

No broker-specific delayed-message plugin is required.

Workers:
- claim only due work;
- sleep/poll until next due time;
- may use LISTEN/NOTIFY wake-up when new earlier work arrives.

Business/canonical cycle timing is persisted in Execution state and materialized as due work; queue retry timers do not replace canonical orchestration timers.

## LISTEN/NOTIFY

Optional optimization:
- notify channel contains only a generic queue/wakeup signal;
- no SECRET/CLIENT_PRIVATE payload is placed in notification;
- worker always re-queries durable table;
- missed/reordered wake-up cannot lose work;
- periodic polling remains the correctness fallback.

Notification payload is never the work item.

## Infrastructure retry vs business retry

### Infrastructure retry

Examples:
- transient DB connectivity;
- process crash before any external effect;
- temporary internal dependency outage.

May requeue the same work item with bounded backoff.

### Business/orchestration retry

Examples:
- provider transient failure;
- Retry-After;
- canonical cycle/fallback.

Owned by Execution/#8 and characterized semantics, not automatic queue retries.

A worker framework/broker may not secretly decide provider retry count.

## Provider ambiguity

If a work item dispatched a provider call and the external outcome is ambiguous, the provider attempt state from #14 controls recovery.

The queue may redeliver/resume the command, but handler observes AMBIGUOUS_EXTERNAL_OUTCOME and must not dispatch the same unsupported-provider attempt again.

## Event delivery transport

ADR-0015 owner outbox remains canonical publication intent.

PostgreSQL EventTransport implementation:
1. outbox publisher loads PENDING owner event;
2. resolves statically/versioned registered consumers;
3. inserts one messaging.event_deliveries row per consumer with unique (event_id, consumer_name);
4. marks owner outbox published only when the transport insertion is durably committed;
5. delivery consumers claim rows with the same lease/SKIP LOCKED pattern;
6. consumer inbox/effect provides final duplicate protection.

Because initial transport is the same database, steps 3/4 can use a controlled local DB transaction where architecture boundaries permit. The contract still assumes at-least-once so later external broker migration does not weaken correctness.

## Event delivery fields

- event_id;
- consumer_name;
- event_type/version;
- envelope/payload;
- data_classification;
- state;
- available_at;
- attempts;
- lease_owner/until;
- last_error_class;
- delivered/acked/dead timestamps.

Unique:

    (event_id, consumer_name)

## Subscription registry

Initial event subscriptions are application configuration/code contracts tied to event catalog versions.

No dynamic tenant-created topic/subscription feature exists.

Each consumer declares:
- supported event types/versions;
- queue class;
- classification allowed;
- handler identity/version.

## Dead-letter

No separate broker DLQ is required initially.

Work/event delivery enters DEAD state after:
- max bounded infrastructure attempts;
- deterministic unsupported/invalid contract;
- explicit poison classification.

DEAD records remain inspectable/replayable through authorized administrative tooling.

Replay:
- audited;
- bounded;
- preserves logical work/event ID where semantics require;
- never bypasses #14/#51 dedupe.

## Payload size

Inline work/event JSON payload baseline maximum:

    64 KiB

Prefer substantially smaller messages.

Larger/raw payload:
- stored in authoritative/protected DB/blob storage;
- queue contains reference + version/integrity metadata.

SECRET is prohibited regardless of size.

Large prompt/result duplication in messaging tables is prohibited.

## Data classification

- SECRET: never in messaging payload;
- CLIENT_PRIVATE: allowed only when consumer truly needs it and tenant/client scope is present;
- CONFIDENTIAL/RESTRICTED: only on restricted internal queue/consumer contract;
- PUBLIC/INTERNAL: according to contract.

Database roles for workers/publishers have least privilege to the queues they process.

## Worker pools

API and worker have independent DB connection pools.

Worker pool may be separated by queue:
- execution workers;
- accounting workers;
- projection workers;
- maintenance workers.

A maintenance backfill cannot consume all execution workers/connections.

## Backpressure

Measure:
- READY backlog;
- oldest work age;
- claim latency;
- lease expirations;
- worker saturation;
- DB CPU/IO/locks;
- retry/dead rate.

Admission/concurrency controls use these signals plus quotas.

Unbounded queue growth is not considered acceptable durability.

## Cleanup/bloat

Queue tables are mutable/churn-heavy.

Requirements:
- targeted indexes only;
- autovacuum monitoring/tuning;
- DONE rows retained for a bounded diagnostic/dedupe interval then archived/deleted according to policy;
- long-term authoritative business history remains in owner tables, not the work queue;
- batch cleanup;
- benchmark bloat/vacuum behavior under target load.

## Local development

Docker Compose requires only PostgreSQL for initial messaging.

No paid broker/cloud service is needed.

Deterministic integration tests use PostgreSQL and multiple concurrent worker processes/tasks.

## Production-small

Use private PostgreSQL with:
- dedicated messaging roles/pools;
- queue indexes/autovacuum;
- worker process supervision;
- monitoring of backlog/claim latency;
- sufficient connection budget.

LISTEN/NOTIFY may reduce polling latency but is optional.

## Scale-out gate: external work broker

Evaluate RabbitMQ/NATS JetStream/managed queue or another fit when measurements show one or more material needs:
- queue workload causes OLTP DB latency/SLO regression;
- queue churn/vacuum/IO becomes operationally significant despite tuning;
- very high sustained dispatch throughput exceeds validated PostgreSQL capacity with headroom;
- independent queue availability/scaling becomes materially different from database needs;
- broker-native routing/delivery features materially simplify the product;
- multi-region topology requires an independent transport boundary.

Migration requires benchmark + ADR and preserves WorkQueuePort/idempotency semantics.

## Kafka/Redpanda gate

Do **not** adopt Kafka/Redpanda for ordinary task queuing.

Consider log/stream platforms only with measured requirements such as:
- high sustained event throughput with significant headroom need;
- many independent consumer groups;
- long retained replayable streams;
- stream processing/analytics as a first-class workload;
- partition-key ordering across a large distributed pipeline;
- PostgreSQL/external queue architecture fails measured SLO/cost/operability goals.

"Industry standard" or anticipated future scale is not evidence.

## Alternatives

### Redis lists/streams

Not selected initially:
- adds an infrastructure dependency;
- durability/eviction/persistence configuration becomes another correctness boundary;
- no current requirement justifies it.

### RabbitMQ

Strong work-queue option, but adds broker operations now. Retained as future candidate if PostgreSQL work queue becomes a measured bottleneck.

### NATS JetStream

Strong lightweight durable pub/sub candidate, but adds another stateful system before a measured need.

### Celery

Not selected as application execution abstraction because built-in task retry/result semantics could obscure ORQETIA's canonical cycle/idempotency/recovery ownership. A future adapter could use a task framework only if those semantics remain explicitly controlled and tested.

### Kafka / Redpanda

Rejected initially as disproportionate operational complexity for the current work/task/event profile.

## Benchmark acceptance before Phase 2

Phase 1 messaging implementation includes:
- multi-consumer SKIP LOCKED correctness test;
- lease-expiry/reclaim test;
- delayed available_at test;
- priority/no-starvation test;
- duplicate/redelivery test;
- dead/replay test;
- LISTEN missed-signal + polling recovery test;
- 64-KiB payload limit test;
- synthetic load reporting claim latency/backlog without paid infrastructure.

Absolute throughput from free/variable CI hardware is reported rather than treated as universal production capacity.

## Consequences

Positive:
- minimal initial infrastructure;
- local transactions and recovery are straightforward;
- no hidden framework retry ownership;
- local development remains simple and free;
- migration boundary to external broker is explicit.

Costs:
- messaging churn shares PostgreSQL resources initially;
- worker loop/lease code must be tested rigorously;
- event fan-out is suitable for a small internal consumer set, not a massive streaming ecosystem.
