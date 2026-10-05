# PostgreSQL messaging foundation

Issue: #104  
Contracts: ADR-0015 (#51), ADR-0016 (#52), idempotency ADR-0014 (#14)

## Ownership

The messaging schema is infrastructure transport, not a business bounded context.

It owns only:
- messaging.work_items;
- messaging.event_deliveries.

Authoritative outbox/inbox remain inside the bounded context that owns the
business fact/effect. build_outbox_table() / build_inbox_table() provide a
shared physical shape while requiring owner-schema metadata.

## Work queue

PostgresWorkQueue implements:
- durable enqueue;
- due-work claim with FOR UPDATE SKIP LOCKED;
- bounded priority ordering within one queue;
- leases;
- expired-lease reclaim;
- stale-worker completion rejection;
- bounded infrastructure requeue.

A work item's max_infrastructure_attempts is not provider retry policy.
Provider Retry-After/cycles/fallback remain Execution-owned.

Processing occurs after the short claim transaction commits.

## Event transport

PostgresEventTransport stores one delivery per (event_id, consumer_name).
Repeated transport registration is idempotent through the database unique
constraint.

The consumer inbox remains the final exactly-once-effect boundary. Transport
delivery itself remains at-least-once.

## Payload/security

Inline JSON is limited to 64 KiB.

SECRET is rejected in application contracts and database constraints.

CLIENT_PRIVATE requires both tenant_id and client_id.

PostgresWakeup sends only a generic queue name through pg_notify.
Notification is an optional optimization: workers must continue polling durable
rows, so missed LISTEN/NOTIFY cannot lose work.

## Queue classes

- execution
- scheduler
- accounting
- projection
- maintenance

Priority is local to a queue. Clients do not select internal queue or priority
through the public API.

## Deferred to #105 / later runtime

- long-running worker loop;
- LISTEN listener lifecycle;
- lease heartbeat loop;
- handler registry;
- scheduler due-work activation;
- DEAD/replay operator tooling;
- synthetic throughput benchmark/reporting.

The persistence/claim semantics required by those processes are established in
this issue.
