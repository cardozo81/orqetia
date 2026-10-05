# Worker and scheduler process shells

Issue: #105  
Depends on: #52, #102, #104

## Process separation

ORQETIA runs separate composition roots:

- `python -m apps.worker.main`;
- `python -m apps.scheduler.main`.

Both use the durable PostgreSQL messaging ports from #104. They are independently
restartable and do not rely on shared process memory.

## Worker shell

The worker:

- claims only durable work from its configured queue;
- executes a versioned handler from `HandlerRegistry`;
- renews active leases while a handler runs;
- refuses stale completion after lease loss;
- supports explicit infrastructure-only requeue;
- drains in-flight handlers during graceful shutdown.

An arbitrary handler exception is **not** interpreted as provider retry,
Retry-After, fallback, or another canonical cycle. The durable lease expires and
the owning handler/idempotency logic decides recovery.

Phase 1 registers no production domain/provider handlers. With an empty registry
the process remains alive but deliberately does not claim unknown work.

## Scheduler/event-publisher shell

The scheduler is a separate process using the scheduler queue. It only processes
durable work already materialized by an owner. It does not calculate or invent
canonical provider retry timers.

`wake_due_scan()` emits only the generic scheduler wakeup hint. Repeating the
hint does not create durable work.

Future scheduler/outbox-publisher handlers are registered through the same
versioned handler registry.

## Lease heartbeat

Heartbeat renews lease ownership periodically while the handler is active. If
renewal fails, the process marks the lease as lost and does not commit
completion/requeue through the stale lease.

The PostgreSQL adapter validates current state/owner/non-expired lease on every
renewal/completion/requeue.

## Logging

Process logs contain safe operational metadata only:

- process ID;
- queue;
- work ID;
- operation type/version;
- correlation ID;
- error class.

Payloads and secrets are not logged by the process shell.

## Local Compose

The #102 Compose stack now runs the real worker/scheduler entry points. The
temporary development lifecycle harness has been removed.
