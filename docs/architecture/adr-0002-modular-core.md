# ADR-0002 — Modular core with selective operational decomposition

- Status: **Accepted**
- Issue: #49
- Depends on: #31, #43

## Decision

ORQETIA starts as a **modular core** with explicit bounded contexts and a small number of deployables.

A bounded context is a code/data ownership boundary. It is **not automatically a microservice**.

Initial deployables:

1. **Web/API**
2. **Worker**
3. **Scheduler/event publisher** only when durable timing/publication requires a distinct process
4. PostgreSQL
5. Queue/topic implementation selected by #52
6. Reverse proxy/TLS in public deployment

The first three deployables may share the same versioned Python package while remaining separate processes.

## Bounded contexts

### Identity & Tenancy
Owns:
- tenant/organization;
- human subject metadata;
- service client/application;
- client credential metadata;
- membership/role/scope assignments.

### Control Plane
Owns:
- providers/models/capabilities;
- execution policy definitions/versions;
- provider permissions;
- pricing catalog configuration;
- quota configuration;
- provider account/credential metadata references.

### Execution
Owns:
- execution sessions;
- tasks;
- attempts;
- state transitions;
- health/quarantine state;
- cancellation;
- runtime idempotency/recovery state.

### Usage & Accounting
Owns:
- technical usage facts;
- provider cost estimation/application;
- observed provider monetary facts;
- native usage;
- accounting ledger/reconciliation.

### Statistical Estimation
Owns:
- versioned rollups;
- benchmark snapshots;
- CLIENT_ONLY/GLOBAL_PUBLIC aggregate products.

### Audit & Operations
Owns:
- administrative audit trail;
- operational/security audit facts;
- incident-relevant immutable references.

### Web Read Models
Owns only reconstructible projections optimized for portal/backoffice/report queries.

## Data ownership rules

- every authoritative table has exactly one owning context;
- another context consumes an explicit internal contract, event or projection;
- arbitrary cross-context table access is prohibited;
- a shared PostgreSQL cluster/schema layout does not imply shared ownership;
- projections never become the authoritative source by convenience;
- no distributed 2PC.

Detailed physical ownership is finalized in #33/#50.

## Internal contracts

Use:
- in-process typed calls for synchronous operations inside one deployable;
- HTTP only when a context is independently deployed and immediate response is required;
- queue for asynchronous commands/work;
- events for facts already committed;
- read models for cross-context query optimization.

Do not use events as a substitute for every function call.

## Dependency direction

The domain/application layers of a context must not import infrastructure details from another context.

Shared packages are restricted to deliberately generic primitives such as:
- identifiers/time;
- error envelope primitives;
- tracing/correlation primitives;
- small security-neutral utilities.

A generic `shared` package must not become a dumping ground for cross-context business logic.

## Provider boundary

Concrete SDK/auth/wire logic is owned by provider adapters.

Execution and client-facing code depend on a neutral adapter protocol/capability model, never concrete provider classes or secrets.

## Deployable extraction criteria

A bounded context becomes an independently versioned/deployed service only with measured or explicit evidence of at least one material difference:

- throughput/scaling pattern;
- latency/SLO;
- blast radius;
- security boundary;
- failure isolation;
- consistency model;
- lifecycle/deploy cadence;
- resource profile;
- regulatory/operational isolation.

Extraction requires an ADR that records:
- evidence;
- new data ownership boundary;
- communication contract;
- failure/retry model;
- migration plan;
- rollback plan.

## Explicit non-decisions

This ADR does **not**:
- choose the final queue/broker (#52);
- choose AuthN/AuthZ provider (#12/#54);
- define physical schemas/indexes (#33/#44);
- create a dedicated analytics database;
- create service-per-entity boundaries.

## Rejected alternatives

### Dozens of microservices now
Rejected: distributed failure/consistency/operational overhead without measured need.

### Conventional monolith with unrestricted shared tables
Rejected: hides ownership and makes later extraction risky.

### Service per entity
Rejected: entity boundaries do not represent operational consistency or capability boundaries.

### Polyglot persistence now
Rejected: no measured requirement; increases backup, security and consistency burden.

## Testing consequences

Architecture tests should eventually enforce:
- prohibited imports between bounded contexts;
- ownership/migration boundaries;
- no production dependency on characterization test oracle;
- provider SDK isolation.

## Consequences

Positive:
- minimizes initial operational complexity;
- keeps durability/tenancy/security explicit;
- allows worker scaling independently of HTTP;
- permits later service extraction without redesigning ownership.

Costs:
- requires discipline around package boundaries even inside one repository/process;
- some queries must use projections/contracts instead of convenient cross-table access.

## Relationship to reuse

#43 remains authoritative: modularization cannot be used as justification for reimplementing canonical behavior unnecessarily.
