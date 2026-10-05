# ADR-0007 — Tenant, client and resource ownership model

- Status: **Accepted**
- Issue: #21
- Depends on: #2, #33, #50

## Definitions

### Tenant
A customer organization/security boundary.

One tenant owns zero or more service clients and customer-facing human memberships.

### Service Client
An application/workload identity owned by exactly one tenant in the baseline architecture.

A service client:
- receives an assigned execution policy version;
- receives provider permissions;
- receives quota assignments;
- owns integration credentials;
- creates/owns sessions/tasks through authenticated requests.

### Human subject
A human identity authenticated through the future OIDC boundary.

Customer human subjects act through tenant membership and roles.

Backoffice human subjects are ORQETIA platform identities and are not made tenant members merely to gain administrative access.

### Client access credential
A credential owned by one service client.

The clear secret is shown only at creation/rotation when technically required and is otherwise stored hash-only where possible.

Credential metadata is not a substitute for provider credentials.

## Cardinality baseline

- tenant 1 → N service clients;
- tenant 1 → N customer memberships;
- service client 1 → N access credentials;
- service client 1 → N execution sessions;
- execution session 1 → N tasks;
- service client N → 1 effective policy assignment at an instant;
- service client N → N provider permissions;
- service client N → N quota assignments.

A service client cannot belong to multiple tenants in the baseline. Cross-tenant shared applications require a future ADR.

## Ownership columns

All client-owned authoritative resources persist tenant_id and client_id where required to prove ownership without an unsafe cross-context lookup.

At minimum:
- client credential metadata;
- session;
- task;
- attempt;
- usage/accounting facts;
- quota consumption/reservation;
- customer-facing audit/read-model facts.

Duplication of tenant_id on child rows is intentional for authorization, indexing, partitioning and integrity checks.

Within one owner context, composite/consistency constraints must ensure child tenant/client identity matches its parent.

## Authority

### Backoffice

Only explicitly authorized Backoffice roles may:
- create/suspend/offboard tenants;
- create/suspend service clients;
- administer Backoffice users/roles;
- assign execution policy;
- assign provider permissions;
- assign quotas;
- administer provider accounts/credentials;
- administer pricing/internal financial configuration.

Every sensitive Backoffice action is audited.

### Client Portal/customer human users

May, subject to their role/scope:
- inspect their tenant/client resources;
- create/rotate/revoke authorized access credentials for clients they administer;
- inspect sessions/tasks/results;
- inspect technical usage/reports;
- request token estimates.

They cannot:
- create another tenant;
- create arbitrary service clients unless a future product policy explicitly enables it;
- assign orchestration policy;
- assign quotas/provider permissions;
- create Backoffice identities;
- access provider credentials/cost/credits.

### Service client

May execute only the API actions granted by scopes and ownership.

Within an administratively enabled target-selection envelope, a service client may:
- omit execution target and use AUTO;
- request an explicitly allowed provider/model/reasoning profile.

It cannot widen its tenant/client scope, provider/model/profile entitlement or administrative limits through request fields.

## Authenticated scope derivation

Authorization context is derived from validated authentication state, not arbitrary body/query headers.

A resolved principal contains, as applicable:
- subject type;
- subject/client identifier;
- tenant_id;
- client_id for service-client credentials/tokens;
- customer membership roles;
- scopes;
- Backoffice role/privileges;
- authentication/session strength metadata for step-up decisions.

If a request includes tenant_id/client_id, it must equal or be subordinate to the authenticated authorized scope. It never expands that scope.

Trusted headers from an ordinary client are not an identity boundary.

## Object-level authorization rule

For every read/mutation:
1. authenticate principal;
2. resolve action/scope;
3. resolve authoritative resource ownership;
4. verify tenant/client relationship;
5. apply role/scope/resource/property policy;
6. only then perform/read sensitive data;
7. audit privileged/sensitive access when required.

“Resource UUID is hard to guess” is never authorization.

## Policy ownership

Execution policy is administrative data.

A client request does not submit free orchestration controls such as:
- max_cycles;
- retry/delay;
- timeout;
- arbitrary allowed/excluded provider set;
- pricing/quota configuration;
- terminal/quarantine policy.

### Client target-selection envelope

Issue #80 is the explicit allowlisted target-selection contract.

Backoffice/Control Plane defines, per client and effective policy:
- whether explicit targeting is enabled;
- allowed providers;
- allowed provider/model combinations;
- allowed provider/model/reasoning-profile combinations;
- server-side defaults used when an allowed target is partially specified;
- administrative cycles/retry/delay/timeout/quota ceilings.

The client's explicit request can only **narrow** execution to an allowed target. It cannot widen any permission or administrative limit.

AUTO remains the default when execution target is omitted.

### Task snapshot

A task persists:
- effective_policy_version_id;
- requested_execution_mode = AUTO | EXPLICIT_TARGET;
- requested provider/model/reasoning profile fields as supplied;
- effective provider/model/reasoning profile only when a hard explicit target is resolved;
- target resolution reason/status.

For AUTO there is no single frozen task-level provider/model target: actual provider/model/profile provenance belongs to each `attempt_id`. The task-level effective target fields remain null/not-applicable in AUTO.

For EXPLICIT_TARGET, the fully resolved target is frozen before the first provider dispatch. Later default/catalog changes do not rewrite it.

Exact scope authorization is revalidated by #12; routing/cost behavior is defined by #80.

## Tenant lifecycle

Suggested states:
- PROVISIONING
- ACTIVE
- SUSPENDED
- OFFBOARDING
- CLOSED

Suspension prevents new execution/credential issuance according to policy without silently deleting historical accounting/audit data.

Offboarding follows #55 retention/deletion rules and preserves legally/security-required records.

## Service-client lifecycle

Suggested states:
- ACTIVE
- SUSPENDED
- REVOKED

Revocation prevents new authentication/execution. Existing sessions/tokens follow #12 revocation policy.

## Defense-in-depth with PostgreSQL RLS

Adopt RLS on tenant-owned runtime tables where practical, especially client-facing identity/execution/read-model paths.

RLS is **defense in depth**, not the primary authorization engine.

Rules:
- normal client-facing runtime DB roles do not use BYPASSRLS;
- each transaction sets trusted tenant/client context from server-side authenticated state, never directly from untrusted request parameters;
- worker transactions process one claimed task/tenant context at a time and set the matching scope;
- migration/maintenance roles are separate from application roles;
- Backoffice cross-tenant access uses a separate tightly controlled data-access role/path and still requires application RBAC/ABAC plus audit;
- no shared superuser connection pool for ordinary application requests.

RLS policy implementation belongs to the scaffold/persistence implementation and must have negative tests.

## Query/repository API rule

Client-facing repositories must not expose unrestricted helpers such as:
- get_any_task(id);
- list_all_sessions();
- find_credential_without_owner_scope().

Prefer APIs requiring OwnershipScope/tenant/client and fail closed.

Backoffice repositories are separate interfaces with explicit privilege requirements.

## Authorization negative matrix

Mandatory tests include:
- tenant A cannot read/update/delete tenant B session/task;
- client A1 cannot access client A2 resource unless an explicitly authorized tenant-human role permits it;
- credential for client A1 cannot authenticate as A2 by changing client_id in request;
- suspended/revoked tenant/client/credential cannot create new execution;
- client cannot set/override policy/quota/provider permissions;
- client without an enabled target envelope cannot select provider/model/profile;
- provider/model/profile outside the client envelope is rejected before provider dispatch;
- explicit target does not change max cycles/retry/delay/timeout/quota;
- client response never exposes provider credential/cost/internal finance fields;
- Backoffice role lacking a specific privilege cannot gain it by knowing endpoint/resource ID;
- filters/export endpoints cannot bypass object/property authorization;
- worker cannot mutate a task under a mismatched tenant context.

## Future commercial billing

The identity/ownership model reserves future relationships for:
- commercial plan;
- client pricing policy;
- effective dates;
- client charge ledger.

These are separate from immutable historical provider-cost/accounting facts.

Adding commercial billing must not rewrite provider cost history.

## Revalidation result — 2026-10-05

Revalidated for #21 against #80:
- AUTO is default on omitted target;
- explicit target is an optional narrowing capability;
- provider/model/profile entitlements are server-side;
- task persistence distinguishes requested selection from resolved explicit target;
- AUTO provider provenance remains per attempt rather than being falsely frozen at task level;
- administrative orchestration limits remain owned by Backoffice/Control Plane.

## Consequences

Positive:
- authorization has a precise tenant/client object model;
- service credentials cannot widen ownership;
- RLS adds containment against missed application filters;
- future billing can attach without conflating provider cost.

Costs:
- ownership dimensions are intentionally repeated on hot facts;
- Backoffice and worker DB access require disciplined role separation;
- negative isolation tests become mandatory.
