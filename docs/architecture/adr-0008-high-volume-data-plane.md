# ADR-0008 — High-volume PostgreSQL data plane

- Status: **Accepted**
- Issue: #44
- Depends on: #2, #21, #33, #50

## Decision

PostgreSQL remains the initial transactional and operational analytics store.

High-volume behavior is handled by:
- append-oriented factual tables;
- time-local UUIDv7 identifiers;
- narrow hot-path indexes;
- asynchronous batch rollups;
- rebuildable read models;
- explicit projection watermarks/reconciliation;
- temporal partitioning only when measured thresholds justify it.

No mandatory analytical database or PostgreSQL extension is introduced in the initial architecture.

## Write classes

### Transactional state
Mutable owner state:
- tenants/clients/configuration;
- sessions/tasks;
- quota reservation state;
- credential metadata.

Strong local constraints and short transactions.

### Append factual ledger
Immutable/append-mostly:
- provider attempts;
- usage facts;
- provider cost facts;
- native usage facts;
- audit events;
- projection source events where retained.

Corrections are new facts/reconciliation records, not silent historical overwrites.

### Immutable snapshots
- execution policy versions;
- pricing catalog/rule versions;
- benchmark snapshots;
- effective configuration references.

### Projections/read models
- dashboard/portal reports;
- statistical rollups;
- operational aggregates.

Rebuildable and eventually consistent.

## Hot-write rule

No heavy dashboard aggregation, percentile calculation or cross-tenant analytical query runs in the transaction that records a provider attempt/usage fact.

The hot write transaction performs only:
- required owner state change;
- append facts;
- local constraints;
- outbox record when needed.

## Identifier locality

UUIDv7 is preferred for append-heavy facts and execution entities.

Primary-key index remains B-tree.

Do not derive authorization/time solely from UUID contents.

## Physical high-volume tables

### execution.provider_attempts

Core columns:
- id uuid PK;
- tenant_id uuid NOT NULL;
- client_id uuid NOT NULL;
- session_id uuid NOT NULL;
- task_id uuid NOT NULL;
- provider_id/model_id;
- provider_account_id/provider_credential_id safe references;
- cycle integer;
- attempt_index integer;
- status/error_class;
- policy_version_id;
- started_at/finished_at timestamptz;
- duration_ms bigint;
- provenance metadata jsonb limited to non-index-critical extension data.

Indexes:
- UNIQUE(task_id, attempt_index);
- (tenant_id, client_id, started_at DESC);
- (session_id, started_at DESC);
- (task_id, started_at);
- (provider_id, model_id, started_at DESC) when provider analytics begins;
- partial error index considered only after error query profile is measured.

### accounting.usage_facts

Core columns:
- id uuid PK;
- attempt_id uuid logical reference;
- tenant_id/client_id;
- provider_id/model_id;
- client_credential_id safe reference when relevant;
- provider_credential_id safe reference when relevant;
- input/cached/output/reasoning/total token bigint fields;
- occurred_at timestamptz;
- source_event_id uuid for idempotency where event-derived.

Indexes:
- UNIQUE(source_event_id) when populated;
- (tenant_id, client_id, occurred_at DESC);
- (attempt_id);
- (provider_id, model_id, occurred_at DESC);
- (client_credential_id, occurred_at DESC) when credential reporting is enabled;
- BRIN(occurred_at) when table size makes it beneficial.

### accounting.provider_cost_facts

Core columns:
- id uuid PK;
- attempt_id;
- tenant_id/client_id;
- provider/model/account/credential safe dimensions;
- basis;
- amount numeric;
- currency char/varchar ISO code;
- pricing_catalog_version/rule_id/context;
- occurred_at;
- source_event_id.

Indexes optimized for Backoffice only:
- (tenant_id, client_id, occurred_at DESC);
- (provider_id, model_id, occurred_at DESC);
- (provider_credential_id, occurred_at DESC);
- BRIN(occurred_at) at scale.

No client-facing repository exposes monetary columns.

### audit.audit_events

Append-only semantic event records with minimal safe metadata.

Index by:
- occurred_at;
- actor/subject;
- tenant/client;
- action/resource class;
- correlation ID.

Sensitive incident evidence may use a separate restricted store/reference under #57/#61.

## Index discipline

Every hot-table index must answer a documented query or constraint.

Rules:
- avoid indexing every dimension combination;
- composite indexes place equality ownership filters before time-range ordering;
- BRIN supplements, not replaces, B-tree ownership indexes;
- JSONB GIN indexes require measured query need;
- unused/duplicate indexes are removed through measured review;
- write amplification is part of index acceptance.

## Partition strategy

Initial deploy may remain unpartitioned while volume is small.

A table becomes a temporal partition candidate when one or more is observed:
- approximately 10M+ append rows;
- table/index size materially impacts vacuum/backup/maintenance;
- retention needs efficient time-window detach/drop;
- query plans repeatedly scan irrelevant historical ranges;
- write/read SLO degradation is attributable to table size.

Default first partition scheme:
- RANGE by occurred_at/started_at;
- monthly partitions;
- automated creation ahead of time;
- default/catch-all partition only if monitored and drained;
- no tenant subpartitioning until skew measurements justify it.

Partition migration uses backfill/reconciliation and does not change logical ownership.

## Connection management

Each process has an intentionally bounded pool.

Rules:
- size pool from PostgreSQL max_connections and replica/process count;
- short transactions;
- no provider/network call while holding DB transaction;
- connection timeout and statement timeout configured by workload class;
- PgBouncer or managed pooling may be introduced when connection scaling requires it, not as a correctness dependency.

## Autovacuum/analyze

Append/mutable tables receive explicit monitoring for:
- dead tuples;
- analyze freshness;
- index growth;
- transaction age;
- long-running transactions.

Per-table tuning is measurement-driven.

Workers performing bulk backfills use bounded batches and yield between transactions.

## Rollup architecture

Rollups are produced asynchronously from authoritative facts/events.

Every projection pipeline owns:
- projection_name/version;
- last checkpoint/source position;
- watermark/as_of;
- last successful run;
- error/retry metadata.

Updates are idempotent.

Late facts:
- normal rolling window may be recomputed;
- older late facts mark affected buckets dirty;
- reconciliation/backfill rebuilds those buckets from authoritative facts.

## Rollup grain

Initial grains:
- hourly for operational views;
- daily for long-range reporting.

Dimensions are introduced only if they have a consumer and meet privacy rules.

Candidate dimensions:
- tenant/client;
- provider/model;
- safe client/provider credential ID;
- execution/profile class;
- input-size bucket;
- schema/output class;
- reasoning profile;
- status/error class;
- quota category.

Metrics:
- request/task/attempt counts;
- success/partial/failure counts;
- input/output/cached/reasoning/total tokens;
- native units;
- latency sum/count/min/max and stored percentile results where built;
- provider cost by currency for Backoffice-only rollups;
- unpriced count;
- quota consumed/reserved.

## Statistical estimates

CLIENT_ONLY aggregates are physically scoped to tenant/client.

GLOBAL_PUBLIC uses only sanitized/cohort-safe aggregates approved by #11/#55/#61.

No raw prompt/output or individual tenant identity is copied into GLOBAL_PUBLIC snapshots.

Percentiles can be computed during batch rollup using PostgreSQL ordered-set aggregates and stored as scalar results. No extension is required initially.

## Read models

Minimum products:
- client usage over time;
- client task/status summary;
- client provider/model technical usage when policy permits;
- credential technical usage;
- quota utilization;
- Backoffice provider/model operations;
- Backoffice provider/account/credential error/latency/cost;
- token-estimate benchmark views.

Common UI/API queries hit read models, not full ledger scans.

Individual attempt drill-down may query authoritative attempt/fact tables under explicit authorization.

## Consistency and reconciliation

Ledger is factual source.

Projection correctness mechanisms:
- unique source event/fact identity;
- idempotent upsert/rebuild;
- watermark;
- replay;
- late-event handling;
- periodic reconciliation of fact counts/totals against projections.

Every eventually consistent response that may matter operationally exposes as_of/watermark semantics.

## Initial service objectives

These are architecture targets for application/storage design, not a production SLA declaration.

On a healthy small-production PostgreSQL deployment under target load:
- task/session point lookup DB p95 target <= 100 ms;
- common client/read-model query DB p95 target <= 250 ms;
- common Backoffice dashboard query DB p95 target <= 500 ms;
- projection freshness p95 target <= 60 seconds for operational dashboards;
- ledger write must not wait on dashboard rollup completion.

M9 converts measured system performance into release capacity/SLO commitments.

## Synthetic benchmark profile

A reproducible local benchmark, implemented in Phase 1/related implementation issue, will create synthetic multi-tenant data only.

Profiles:
- correctness-small: CI-safe small dataset;
- capacity-local: configurable 1M+ factual rows on local Docker PostgreSQL;
- skew: one noisy tenant plus many small tenants;
- late-events: out-of-order projection input;
- backfill: rebuild rollups from facts.

Assertions:
- no cross-tenant result leakage;
- idempotent replay;
- expected query plans/index use for hot queries;
- no dashboard full-ledger aggregation in ordinary path;
- currency separation/UNPRICED correctness;
- projection reconciliation equality.

Absolute throughput is reported, not hard-coded as a universal CI gate because free-runner hardware varies. Regression gates use relative/query-plan thresholds plus product SLO checks in controlled release benchmarking.

## Analytical-store extraction gate

A dedicated analytical store is considered only when measured evidence shows PostgreSQL cannot meet required analytical SLO/cost/retention characteristics after reasonable indexing, partitioning and projection design.

Extraction requires ADR covering:
- source/event replication;
- data classification;
- tenant isolation;
- consistency/watermark semantics;
- backup/retention;
- operational cost.

## Consequences

The design favors correctness and an incremental path to high volume without paying the operational cost of a streaming/warehouse stack before the workload exists.
