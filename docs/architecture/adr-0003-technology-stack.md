# ADR-0003 — Technology stack and runtime baseline

- Status: **Accepted**
- Issue: #2
- Depends on: #43, #49
- Validated against upstream release state: 2026-10-04

## Decision summary

ORQETIA will use **Python 3.14** as its initial production language/runtime, with FastAPI/Pydantic for HTTP contracts, PostgreSQL for OLTP, SQLAlchemy/Alembic/Psycopg for persistence, and separate API/worker processes.

The concrete broker/queue implementation is intentionally deferred to #52. Domain/application code depends on internal queue/scheduler ports, not a Celery/Taskiq/Kafka-specific API.

## Runtime

### Python

Baseline: Python >=3.14,<3.15.

Initial development images use the latest Python 3.14 patch available at build time and build metadata records the exact runtime.

Reason:
- the canonical RASAi core is Python;
- #43 estimates high semantic reuse for the orchestration/economic core;
- Python 3.14 is stable;
- Python 3.15 is still prerelease at this decision date and is excluded until final release plus dependency compatibility validation.

Moving to Python 3.15 later is a routine compatibility change if:
- final release is available;
- all runtime dependencies support it;
- characterization/security/database suites pass.

## Project and dependency management

Use:
- pyproject.toml as project metadata;
- **uv** for dependency resolution/environment/sync;
- committed uv.lock for exact reproducibility;
- dependency groups for development/test/security tools.

CI/deploy use locked/frozen installs. Dependency upgrades are explicit PRs.

## HTTP/API

Use:
- **FastAPI 0.141.x**;
- **Pydantic 2.13.x**;
- pydantic-settings for typed environment configuration;
- **Uvicorn 0.46.x** as ASGI server;
- **httpx** for application/provider HTTP client boundaries unless a provider requires a justified official SDK.

Rules:
- OpenAPI is generated from typed contracts but versioned/checked as a product contract;
- client/admin surfaces remain explicitly separated;
- long-running provider work never blocks an HTTP request for the full orchestration lifecycle;
- no provider SDK object leaks into domain/application contracts.

## Persistence

Use:
- **PostgreSQL 18** as the initial production/local OLTP baseline;
- **SQLAlchemy 2.1.x**;
- **Alembic 1.20.x**;
- **Psycopg 3** with native asyncio support;
- SQLAlchemy async session/engine for application I/O paths.

The exact PostgreSQL compatibility window and physical schemas are finalized in #33, but SQLite is not the production multi-client source of truth.

## Worker and scheduler runtime

Use a separate Python worker process sharing versioned ORQETIA packages.

Application-level abstractions:
- WorkQueue;
- WorkLease;
- Scheduler;
- EventPublisher.

Until #52 selects infrastructure:
- no business module may import a broker-specific API;
- no logical retry/cycle timer belongs to FastAPI request memory;
- no durability guarantee depends on a sleeping web process.

## Concurrency model

Primary application model:
- asyncio for network/database I/O;
- bounded concurrency at provider/client/worker boundaries;
- CPU-heavy work, if introduced, uses an explicit executor/process boundary and must not block the event loop.

Do not add gevent/eventlet.

## Transactions and locking

Detailed strategy belongs to #33/#14/#51.

Baseline:
- database transaction is the local atomicity boundary;
- database constraints enforce invariants where possible;
- PostgreSQL row locks/advisory locks may be used where explicitly modeled;
- no distributed 2PC;
- outbox/inbox will implement cross-boundary delivery semantics.

## Cache

No mandatory distributed cache in the base stack.

Rules:
- cache is never authoritative execution/accounting/security state;
- add Redis or another cache only when #52/performance requirements justify it;
- process-local cache may hold immutable/read-through non-sensitive data only when correctness does not depend on it.

## Testing

Use:
- **pytest 9.1.x** for production tests;
- pytest async support as needed;
- HTTP contract tests through ASGI/httpx;
- PostgreSQL integration tests for persistence semantics;
- container-based integration only when cost-free and deterministic;
- #34 stdlib characterization oracle remains an independent semantic gate.

No paid provider call in ordinary tests or CI.

## Static quality and security tooling

Initial free/open-source toolchain:
- Ruff for lint/format;
- mypy for static type checking;
- pip-audit for Python dependency vulnerability checking;
- secret scanning via repository/CI tools selected under #53;
- dependency lock verification.

Security tooling failures of relevant severity block merge according to #53.

## Observability

Application code uses structured logging and stable correlation identifiers from the start.

OpenTelemetry instrumentation/collector/export policy is finalized in #18. No vendor-specific telemetry SDK may become a domain dependency.

## Package layout

Initial source layout:

    src/orqetia/
      identity/
      tenancy/
      control_plane/
      execution/
      providers/
      usage_accounting/
      estimation/
      audit/
      read_models/
      shared/
    apps/
      api/
      worker/
      scheduler/

The shared package is limited to deliberately generic primitives and must not become a cross-context business-logic package.

## Dependency direction

Application/domain code depends on ports. Infrastructure depends inward on application/domain ports. Apps compose those dependencies.

Domain/application packages do not depend on FastAPI, SQLAlchemy, broker SDKs or concrete provider SDKs except inside explicit infrastructure/adapter modules.

## Alternatives considered

### TypeScript/Node.js

Technically viable for HTTP/async I/O, but rejected for the initial implementation.

Primary reason:
- it would translate rather than reuse the highest-risk canonical Python behavior;
- #43 estimates 70–90% semantic/algorithmic reuse potential for the pure orchestration/economic core in Python;
- no material operational benefit has been demonstrated that compensates the additional parity and security-review burden.

Choosing TypeScript later for a separate UI does not change this backend decision.

### Django

Rejected for the initial runtime because ORQETIA requires an API/worker modular core, explicit contracts and controlled infrastructure boundaries; Django's integrated application model would add framework surface without solving the core orchestration problem.

### Flask

Viable but rejected because FastAPI/Pydantic provide stronger typed request/response/OpenAPI integration for the intended public API with less bespoke contract plumbing.

### Microservice-per-context stack

Rejected by ADR-0002.

## Upgrade policy

- exact dependency versions come from the committed lockfile;
- security/bugfix upgrades occur in dedicated PRs with targeted tests;
- minor/major upgrades that change behavior require relevant contract tests;
- Python runtime upgrades run #34 plus the full affected package suite.

## Consequences

Positive:
- maximizes reuse of characterized behavior;
- strong typed API/OpenAPI path;
- mature PostgreSQL async ecosystem;
- API and worker can scale independently;
- queue remains replaceable until requirements are finalized.

Costs:
- Python remains the backend platform commitment for the first architecture;
- careful async/session management is mandatory;
- package-boundary discipline must be enforced because multiple contexts share a repository.

## Gate resolved

This ADR clears the technology-stack gate for #3, #33, #52 and the technical scaffold, subject to the remaining M0 architecture/security gates in #47.
