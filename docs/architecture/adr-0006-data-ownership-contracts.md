# ADR-0006 — Data ownership and bounded-context contracts

- Status: **Accepted**
- Issue: #50
- Depends on: #49, #33

## Core rule

Every authoritative datum has one owner. A different context may hold a versioned reference, immutable snapshot or rebuildable projection, but does not become co-owner.

A shared PostgreSQL cluster is deployment convenience, not a permission to query another context's internal tables.

## Ownership matrix

| Context | Authoritative data |
|---|---|
| Identity & Tenancy | tenants, service clients, human subjects, memberships/roles, client access credential metadata |
| Control Plane | providers/models/capabilities, execution policy versions, client policy assignment, provider permission, quota definitions/assignments, pricing catalogs, provider account/credential metadata references |
| Execution | sessions, tasks, attempts, state transitions, cancellation, session health/quarantine, work leases, execution idempotency |
| Usage & Accounting | technical usage facts, pricing applications, provider estimated/observed cost facts, native usage facts, quota consumption/reservations/reconciliation |
| Statistical Estimation | benchmark snapshots, statistical rollups, calibration/quality metadata |
| Audit & Operations | administrative/security audit events and evidence references |
| Web Read Models | rebuildable client/backoffice/report projections only |

## Quota split

Control Plane owns **what limit applies**:
- policy;
- assignment;
- effective dates;
- dimensions;
- administrative status.

Usage & Accounting owns **what has been consumed/reserved**:
- reservation;
- usage debit;
- reconciliation;
- period consumption;
- overage/rejection fact.

Execution asks the quota contract for an authorization/reservation; it never edits quota ledgers directly.

## Identity contract

Identity & Tenancy exposes typed application contracts for:
- resolve active tenant/client;
- resolve subject membership/roles;
- validate client ownership;
- resolve safe credential metadata/status;
- publish tenant/client/credential lifecycle facts.

Other contexts store tenant_id/client_id/subject_id references required for authorization/audit but do not mutate identity records.

## Control Plane contract

Provides immutable/effective snapshots for:
- ExecutionPolicySnapshot;
- ProviderCapabilitySnapshot;
- ClientProviderPermissionSnapshot;
- PricingCatalogVersion;
- QuotaPolicySnapshot;
- ProviderCredentialReference.

Execution records the effective version/reference used for each session/task/attempt.

A later policy/catalog update never silently rewrites historical execution/accounting semantics.

## Execution contract

Execution is the only owner permitted to transition:
- session state;
- task state;
- attempt state;
- cancellation state;
- session quarantine/health.

Execution publishes facts such as:
- session.created/expired;
- task.created/queued/started/completed/partial/unavailable/failed/cancelled;
- provider.attempt.completed/failed;
- provider.quarantined.

Usage/Accounting and read models consume those facts; they do not modify execution rows.

## Usage & Accounting contract

Consumes attempt/usage facts and owns:
- normalized technical usage;
- pricing application;
- provider monetary facts;
- currency handling;
- quota consumption.

It publishes:
- usage.recorded;
- provider_cost.estimated;
- provider_cost.observed;
- quota.reserved/reconciled/rejected.

Execution may display/use accounting outcomes but must not recalculate monetary truth with private formulas.

## Statistical Estimation contract

Consumes only data explicitly permitted by #11/#55/#61.

Owns:
- versioned CLIENT_ONLY rollups;
- versioned GLOBAL_PUBLIC cohort-safe aggregates;
- benchmark/calibration snapshots.

It does not own raw task content or raw cross-tenant usage facts.

## Audit contract

Audit receives immutable audit facts from authorized actions and security-sensitive reads.

Audit records:
- actor/subject;
- tenant/client when relevant;
- action/resource;
- result;
- correlation/trace;
- classification;
- timestamp;
- safe metadata.

Audit does not store SECRET values or full payloads merely to increase observability.

## Read-model contract

Read-model projections are query products only.

Rules:
- projection consumers may not infer write authorization solely from projection state;
- mutation paths revalidate authoritative ownership/policy;
- each eventual projection carries watermark/as_of semantics when stale data matters;
- projections are rebuildable;
- projection schema may denormalize across contexts without changing ownership.

## Communication selection

### In-process typed call
Use when:
- producer and consumer are in the same deployable;
- immediate result is part of the use case;
- no independent durability boundary is needed.

### HTTP/internal service call
Use only after a context becomes independently deployed and immediate response is required.

### Queue command
Use for asynchronous work that must survive caller/process restart.

### Domain/integration event
Use for propagation of a fact already committed by the owner.

### Read model
Use for repeated cross-context queries where fan-out/joins would be expensive or violate ownership.

## Contract versioning

Public/internal event payloads have:
- explicit type;
- version;
- stable identifiers;
- occurred_at;
- correlation/causation where applicable.

Breaking event/contract changes use a new version or compatibility transition. Consumers never rely on undocumented table shape.

## Snapshot rule

Historical operations preserve the effective configuration necessary to explain their result.

Prefer immutable snapshot/version references over copying secrets or volatile whole records.

Must be reconstructible for an attempt:
- tenant/client;
- execution policy version;
- provider/model;
- provider credential safe identifier/reference;
- pricing rule/version/context;
- relevant quota policy version;
- timestamps/status/error/provenance.

## Cross-context database enforcement

Application repositories are scoped by context/schema.

Architecture tests will reject:
- importing another context's persistence repository from domain/application code;
- raw SQL that names another owned schema outside explicitly approved projection/migration tooling;
- cross-context ORM relationships.

Migration/administrative tooling may inspect multiple schemas only for controlled operations and may not become application business logic.

## Consistency matrix

Strong local consistency is required for:
- task/session transition;
- credential lifecycle mutation within owner;
- idempotency creation/result;
- quota reservation/debit/reconciliation within accounting owner;
- outbox state committed with owner mutation.

Eventual consistency is acceptable for:
- dashboards;
- read-model projections;
- statistical rollups;
- GLOBAL_PUBLIC benchmark;
- provider health analytics outside active session;
- aggregate reporting.

## Failure rule

A downstream projection/accounting consumer failure does not roll back an already committed execution fact across contexts.

Recovery uses outbox/inbox replay and idempotency, not distributed transaction.

Where an operation must not execute without a prerequisite result (for example quota reservation), obtain that result before provider side effect and persist the decision/reference in the execution flow.

## Security boundary

Data ownership does not override authorization:
- every contract validates caller/context;
- tenant/client IDs are never trusted only because they appear in a request;
- SECRET material is retrieved through the dedicated secret boundary, not general read-model/event contracts;
- events/logs carry safe identifiers, not secrets.

## Consequences

The design accepts some duplication in projections/snapshots in exchange for explicit source-of-truth boundaries and an extraction path that does not require shared-table coupling.
