# ADR-0005 — Relational persistence and consistency

- Status: **Accepted**
- Issue: #33
- Depends on: #2, #49

## Decision

PostgreSQL 18 is the initial authoritative relational store. SQLAlchemy 2.1 async is the data-access layer, Psycopg 3 is the driver, and Alembic owns versioned migrations.

A single PostgreSQL database may host multiple logical schemas initially, but schema/table ownership follows bounded contexts and is treated as if independent extraction were possible later.

## Logical PostgreSQL schemas

- identity
- control
- execution
- accounting
- estimation
- audit
- readmodel

Each authoritative table has exactly one owner.

## Cross-context rule

Default: **no foreign key across bounded-context schemas**.

Cross-context references use opaque IDs plus explicit application contracts/events/snapshots. This avoids making data ownership depend on a shared physical database.

Within one bounded context, foreign keys and database constraints are encouraged.

Direct arbitrary SELECT/JOIN against another context's internal tables is prohibited in production application code. Cross-context read needs use:
- owner application service;
- published event/projected data;
- explicit read model.

## Identifier strategy

Use UUID identifiers, with UUIDv7 preferred for newly created high-volume/public entities because they preserve global uniqueness while improving temporal locality.

Generation is application-side to avoid coupling identity creation to a database extension/function.

Rules:
- UUID is identity, not authorization;
- tenant/client ownership is always checked separately;
- external secrets are never encoded in IDs;
- human-facing aliases may exist but are not primary keys.

## Time

All persisted timestamps use PostgreSQL timestamptz and UTC-aware application datetimes.

Rules:
- store instants, not ambiguous local wall time;
- UI/report timezone conversion occurs at presentation/query boundary;
- database/server timezone is configured to UTC;
- created_at/updated_at/occurred_at semantic meaning is explicit per table.

## Transaction boundaries

One application use case owns one local transaction when atomicity is required.

Examples:
- task state transition + outbox event;
- idempotency record + logical resource creation;
- attempt fact + execution transition when same context owns both facts;
- credential lifecycle mutation + local audit reference;
- quota reservation within its owner.

No transaction spans external provider calls.

Provider calls happen outside a database transaction holding row locks.

## Isolation

PostgreSQL READ COMMITTED is the default isolation level.

Use explicit concurrency controls rather than globally increasing isolation:
- unique constraints for uniqueness/idempotency;
- SELECT ... FOR UPDATE for state transitions/leases where necessary;
- optimistic version column for aggregate updates susceptible to stale writers;
- advisory locks only for well-defined singleton/coordination cases;
- SERIALIZABLE only for narrow operations where invariants cannot be expressed safely otherwise.

Every use of a pessimistic/advisory/serializable strategy must have a targeted concurrency test.

## Aggregate versioning

Mutable aggregate roots that can receive concurrent commands include a monotonically increasing version where useful.

Expected-version mismatch produces a concurrency conflict rather than silent last-write-wins.

Terminal execution results are immutable or explicitly versioned by a separate revision record.

## Idempotency

#14 defines public/runtime semantics.

Persistence baseline reserves an execution-owned idempotency record containing:
- tenant_id;
- client_id;
- operation;
- idempotency_key hash/fingerprint;
- request payload fingerprint;
- resource/result reference;
- status;
- created/expires timestamps;
- uniqueness over tenant/client/operation/key.

Raw credentials/secrets are not stored as idempotency keys.

## Outbox/inbox

#51 defines event contracts.

Persistence baseline:
- outbox row is committed in the same local transaction as owner state;
- publisher marks/delivers asynchronously;
- consumers maintain inbox/deduplication by event_id;
- at-least-once delivery with idempotent effect;
- no claim of exactly-once delivery.

## Migration strategy

Use one repository-controlled Alembic migration graph initially.

Migration files identify the owning bounded context in name/header.

Rules:
- no application auto-migrate race on API/worker startup;
- controlled migration job/process;
- expand → migrate/backfill → contract for incompatible shape changes;
- destructive change requires data-retention/rollback analysis;
- schema migration and application version compatibility are documented in the PR;
- every migration has deterministic upgrade path;
- downgrade is provided when technically safe, otherwise explicitly documented as forward-only with recovery procedure.

## Logical ERD baseline

### identity
- tenants
- service_clients
- external_human_subjects
- memberships/role_assignments
- client_credentials_metadata

### control
- providers
- provider_models
- execution_policy_versions
- client_policy_assignments
- client_provider_permissions
- quota_definitions/assignments
- pricing_catalog_versions
- provider_accounts
- provider_credential_metadata

### execution
- execution_sessions
- tasks
- provider_attempts
- task_result_revisions when required
- idempotency_records
- outbox_events
- inbox_events/work_dedup as needed by execution consumers

### accounting
- usage_facts
- provider_cost_facts
- native_usage_facts
- pricing_application_facts
- quota_ledger/reservations if quota ownership remains here after #50

### estimation
- benchmark_snapshots
- benchmark_rollups
- calibration/quality metadata

### audit
- audit_events
- privileged_access_events
- security-relevant immutable references

### readmodel
- backoffice/client projections and rollups only; always rebuildable

The detailed ownership decision is finalized in #50 and physical high-volume design in #44.

## Data types

Prefer:
- bigint for token/native integer counts;
- numeric/decimal for money and rates, never binary float;
- ISO currency code stored with every monetary fact;
- text/enums/check constraints for stable state tokens;
- jsonb only for genuinely flexible/provider-specific metadata;
- typed/indexed columns for every dimension used in authorization, filtering, joins or accounting.

Do not put tenant_id/client_id/status/provider/model/timestamps exclusively inside JSONB.

## Tenant isolation by design

Every tenant/client-owned authoritative resource stores the ownership identifier required for its authorization model.

Indexes/unique constraints include tenant/client scope where needed.

Repository/query APIs require explicit ownership scope rather than offering unrestricted “get by ID” helpers to client-facing paths.

PostgreSQL Row Level Security is evaluated in #21 as defense-in-depth; application authorization remains mandatory even if RLS is adopted.

## Secret data

Provider/client secret material is not persisted in plaintext application columns.

#56 decides KMS/secret-manager/envelope-encryption implementation.

Relational tables store only:
- secret reference;
- safe fingerprint;
- key/version metadata;
- status/rotation timestamps;
- encrypted material only if #56 explicitly selects that model.

No migration fixture contains real credentials.

## Prompt/input/output storage

No raw prompt/output persistence is required merely for observability.

When a product use case requires payload persistence:
- purpose/classification/retention must be defined under #55/#61;
- size boundary follows #48;
- large blobs must not be placed in queue messages;
- sensitive content must not be copied into read models/logs.

## Retention and partitioning

#44/#55 finalize physical partition/retention.

Baseline:
- execution/accounting/audit append-heavy facts are designed to permit temporal partitioning;
- no partitioning is added without query/write benchmark or operational reason;
- deletion/retention jobs are idempotent and auditable;
- legal/security retention may override ordinary user-content retention where applicable.

## Projections

Read models and rollups:
- are eventually consistent unless explicitly stated;
- carry as_of/watermark where relevant;
- are rebuildable from authoritative facts/events;
- never become the hidden source of truth.

## Local/test database

Persistence integration tests use PostgreSQL, matching the selected production major where feasible.

SQLite may be used only for isolated pure-unit tooling that does not claim PostgreSQL transaction/constraint parity.

Tests that assert locking, JSONB, indexes, isolation or migrations must run against PostgreSQL.

## Backup/recovery

#3 defines deployment responsibility and M9 finalizes RPO/RTO.

Database model assumes:
- encrypted backups;
- point-in-time/recovery capability when hosting supports it;
- restore drills before RC;
- migration and recovery runbooks.

## Consequences

Positive:
- durable execution state;
- clear context ownership despite one initial cluster;
- extraction path remains viable;
- explicit concurrency and idempotency strategy;
- accounting uses correct numeric/currency semantics.

Costs:
- some cross-context joins require projections/contracts;
- PostgreSQL is required for meaningful integration tests;
- explicit migration discipline is mandatory.
