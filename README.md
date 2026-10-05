# ORQETIA

**Orchestration, Routing, Quotas, Execution, Telemetry, Integration & Accounting**

> Status: **DEVELOPMENT**
>
> ORQETIA is not a prerelease, Release Candidate (RC), production release, or GA product at this time.

ORQETIA is an independent, generic, multi-client AI orchestration service being extracted conceptually from behavior already proven in the RASAi orchestration engine.

## Repository boundary

This repository is the only writable project boundary for ORQETIA development.

`cardozo81/RASAI-Readiness-Auditor` is **read-only** from this project. No ORQETIA work may create or modify RASAi code, branches, commits, issues, comments, labels, milestones, pull requests, workflows, releases, tags, settings, documentation, or configuration.

RASAi may be consulted only as a technical reference and source of characterization tests.

## Current architecture direction

```text
Backoffice Web ─┐
Client Portal ──┼──> API / Auth
External Clients┘        │
                         ▼
                    Persistence
                         │
                    Queue/Scheduler
                         │
                       Workers
                         │
                  Policy / Providers
                         │
                 Usage / Accounting
```

Development starts locally with containers and must remain deployable later as a permanent public web service.

## Product boundaries

### Backoffice

Backoffice users are the only human users allowed to administer:
- tenants and clients;
- Backoffice users;
- orchestration policies and runtime parameters;
- providers, models, endpoints and provider credentials;
- pricing, quotas, rate limits and scopes;
- technical and financial provider-cost reporting.

Backoffice may inspect requests and attempts by tenant/client and by safe access-credential identifier/fingerprint. It never displays the access-token secret.

### Client Portal / client-facing API

Clients may:
- manage their own integration credentials when authorized;
- inspect their own sessions/tasks;
- export execution reports;
- inspect observed token usage;
- request token/usage estimates;
- access integration documentation.

Clients do **not** administer orchestration parameters and do **not** receive monetary cost data in this phase.

## Execution selection

ORQETIA supports two execution modes:

- **AUTO (default):** when the service client does not specify a target, ORQETIA orders eligible candidates by the lowest comparable estimated provider cost first and escalates to higher-cost candidates only when the cheaper candidate does not satisfy the remaining requirement.
- **EXPLICIT_TARGET (optional):** the service client may request an authorized provider, model and reasoning/depth profile. Once resolved, that target remains fixed for retries/cycles; ORQETIA does not switch to another provider/model because of cost.

Backoffice defines the allowed provider/model/profile envelope and runtime limits. The service client chooses only within that envelope.

## Economic model

ORQETIA separates:
- `technical_usage`;
- internal `provider_cost` (estimated and observed when available);
- future `client_charge` based on commercial plans;
- client-visible technical usage.

`provider_cost` is not the future customer price.

## Canonical-preservation rule

Reuse is preferred over rewriting. Behavior already homologated in the RASAi baseline should be extracted or adapted when technically sound.

A near-total rewrite requires an explicit architecture decision before implementation.

See:
- #1 — master architecture epic;
- #31 — characterization matrix;
- #34 — canonical characterization suite;
- #43 — reuse-first strategy.

## Data architecture direction

The initial data-plane hypothesis is PostgreSQL with:
- durable transactional state;
- append-heavy execution/usage ledger;
- time-aware partitioning where justified;
- incremental statistical rollups;
- read models for Backoffice and Client Portal;
- tenant-safe aggregates for token estimates.

See #33, #44 and #45.

## Development workflow

Issues are organized under functional epics #35–#40. Executable issues must state Parent, Blocked by, Blocks, risk, targeted tests, canonical references, and any human gate.

Automation and GitHub Agent use are allowed only when they do not create additional financial cost for the repository owner. Tests should be isolated to the changed boundary by default.

Human smoke testing is reserved primarily for complete functional packages, high-risk changes, release candidates, or external behavior that cannot be simulated safely.

## Release lifecycle

The project remains in **DEVELOPMENT** until an explicit public decision declares an RC.

See #46 for lifecycle governance.
