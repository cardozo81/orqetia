# ADR-0004 — Deployment topology

- Status: **Accepted**
- Issue: #3
- Depends on: #2, #49

## Decision

ORQETIA uses containerized, process-separated deployment with the same application package running distinct entry points.

No environment is considered production merely because this topology exists. Product state remains DEVELOPMENT until #46 authorizes otherwise.

## Local topology

Docker Compose is the canonical reproducible local integration environment.

Services:

- api — FastAPI/Uvicorn;
- worker — durable work consumer;
- scheduler — enabled when #51/#52 require independent delayed work/event publication;
- postgres — PostgreSQL 18;
- queue — implementation selected by #52;
- optional reverse proxy only when local TLS/proxy behavior must be tested.

Local unit tests may run without containers when their boundary does not require infrastructure.

## Production-small topology

Minimum acceptable public deployment:

- one or more API containers behind TLS termination;
- at least one independently restartable worker;
- scheduler/event publisher if enabled by runtime contracts;
- private PostgreSQL;
- private queue/broker;
- externalized secrets;
- persistent encrypted database storage/backups;
- health/readiness endpoints and process supervision.

The API and worker may run on the same host for a small deployment, but they are separate processes/containers and must not rely on shared process memory.

## Production-scalable topology

Scale independently:

- stateless API replicas;
- worker replicas by queue/priority/capability;
- scheduler/event publisher with singleton/lease-safe behavior where needed;
- PostgreSQL with managed HA/backup strategy appropriate to measured SLOs;
- queue/broker with durability/ack/dead-letter behavior from #52;
- external object/blob storage only if #48 payload-size decision requires it.

No distributed transaction is introduced for scaling.

## Network boundaries

Public:
- HTTPS reverse proxy/load balancer only;
- API and future web UI.

Private:
- PostgreSQL;
- queue/broker;
- internal observability endpoints;
- administration infrastructure not intended for browser access.

Rules:
- PostgreSQL and queue are never exposed to the public Internet as an application requirement;
- provider outbound traffic uses TLS validation and outbound restrictions defined in #56;
- admin/API debug ports are not public.

## TLS and proxy

All public traffic uses HTTPS.

Production requirements:
- modern TLS configuration;
- HSTS after domain/TLS behavior is validated;
- exact trusted proxy configuration;
- forwarded headers accepted only from trusted proxy ranges;
- no application trust in arbitrary client-supplied forwarding headers.

Certificate provisioning may be managed by the hosting platform or reverse proxy; certificates/private keys are never stored in the repository.

## Secrets

Secrets are runtime-injected, never image-baked.

Provider credentials, signing/encryption keys and bootstrap/recovery secrets must use the #56 secret-management boundary.

Local development uses non-production placeholder/test secrets from ignored local configuration. No real provider credential is required for normal development or CI.

## Health and readiness

API:
- liveness: process/event loop alive;
- readiness: required local configuration valid and critical dependencies reachable according to endpoint semantics.

Worker:
- process liveness;
- queue/broker connectivity;
- database connectivity when required;
- heartbeat/lease observability.

Readiness must not call paid providers.

## Database migrations

Migrations are explicit deployment operations.

Rules:
- never let every API/worker replica race to auto-migrate at startup;
- migration runs once per deployment under a controlled job/process;
- application rollout supports compatible transition windows when schema changes require multiple steps;
- rollback procedure distinguishes code rollback from irreversible data migration.

Detailed migration policy belongs to #33.

## Durability and restart behavior

- HTTP process memory is disposable;
- session/task/attempt state is durable;
- worker crash/restart cannot silently lose an accepted durable task;
- retries/cycles/timers derive from durable state/queue/scheduler contracts;
- exactly-once delivery is not promised; effects are made idempotent per #14/#51.

## Backup and recovery

Before public RC:
- PostgreSQL backups are encrypted;
- retention is explicit;
- restoration is tested;
- RPO/RTO are documented under M9;
- backup credentials are isolated from application runtime where practical.

A backup that has never been restored in a test is not considered a validated recovery control.

## Rollout

Default:
1. validate configuration;
2. run pre-deploy checks;
3. run controlled migration if required;
4. deploy compatible API/worker version;
5. check readiness and targeted smoke without paid provider calls;
6. observe error/latency/task health;
7. complete rollout.

Rollback:
- return application image to last known good version when schema is backward-compatible;
- use a documented forward-fix/data recovery path for non-reversible migrations.

## Horizontal scaling

API is stateless with respect to execution correctness.

Workers coordinate via durable queue/DB leases rather than process-local locks.

Scheduler singleton behavior, when needed, uses durable lease/election semantics rather than assuming one process exists.

## Shared hosting acceptance criteria

Traditional shared hosting is acceptable only if it provides all required capabilities:

- long-running container/process support for API and worker;
- independent worker lifecycle;
- PostgreSQL 18-compatible durable database or secure external DB access;
- supported durable queue/broker access;
- HTTPS/custom domain;
- runtime secret injection;
- outbound HTTPS to approved providers;
- health checks/process restart;
- persistent logs/metrics export as required;
- controlled migrations;
- backup/restore capability;
- no forced sleeping/termination model that breaks workers/timers;
- resource limits sufficient for measured workload.

If any mandatory capability is absent, use a VPS/container/PaaS environment instead. Low price alone is not a valid hosting criterion.

## Container requirements

- minimal non-root runtime image where practical;
- pinned Python base major/minor and immutable deployment artifact;
- no build toolchain in final runtime layer unless required;
- dependency install from lockfile;
- no secrets in layers;
- explicit health checks;
- graceful termination for API/worker;
- image scanning under #53.

## Environment separation

At minimum:
- local/test;
- future staging/RC environment when required;
- future production only after explicit release decision.

Credentials, databases and encryption material are never silently shared across environments.

## Rejected alternatives

### One web process running timers/work in memory
Rejected: restart loses ownership/state and prevents independent scaling.

### SQLite as production multi-client store
Rejected: not the chosen durability/concurrency boundary.

### Database/queue exposed publicly
Rejected: unnecessary attack surface.

### Shared hosting regardless of capabilities
Rejected: hosting model must satisfy process/durability/security requirements.

## Consequences

This ADR unblocks local container scaffold and the infrastructure assumptions needed by #33, #52 and #56 while keeping provider/broker/hosting vendor choices replaceable.
