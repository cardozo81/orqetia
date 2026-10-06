# ADR — Customer Portal BFF and authority boundary

**Status:** Accepted  
**Date:** 2026-10-06  
**Issue:** #23  
**Depends on:** #54, #142, #143, #144, #145

## Decision

The official Client Portal is a server-side BFF. Human authentication is supplied
by a trusted OIDC/OAuth2 broker and converted to a server-side ORQETIA web
session. The browser never receives provider credentials, integration secrets
after their one-time reveal, or a bearer token used as long-lived local
authority.

ORQETIA does not implement a local password protocol for the Portal.

## Browser authority

The browser may select only a `membership_id` returned by the server. The
server resolves and freezes `tenant_id/client_id` from that membership and
revalidates identity, membership and active owner state on subsequent requests.

Supplying `tenant_id/client_id` in browser form/query data never changes the
ownership scope.

A web session cannot switch to a different membership. Selecting another client
requires a new session.

## Web security

The Portal uses opaque server-side sessions and:

- `__Host-` secure cookies;
- HttpOnly session cookie;
- strict CSRF token binding;
- same-origin and Fetch Metadata checks for mutations;
- idle and absolute expiry;
- server-side revocation/logout;
- CSP, frame denial, no-store, no-referrer and restricted permissions policy;
- optional HSTS for HTTPS deployment.

Sensitive integration-credential mutations reuse #142 step-up and require recent
MFA.

## Application boundaries

The Portal does not query Execution or Control Plane tables as a shortcut.

Sessions, tasks, results, attempts, sanitized exchange evidence and authorized
provider/model catalog go through `ClientExecutionRuntime`.

Integration credentials go through `ClientAccessCredentialService`.
Usage goes through `ClientUsageReportService`. Estimates go through the
canonical `EstimateService`.

The PostgreSQL composition factory wires the concrete storage/application
adapters that already exist. OIDC and EstimateService remain explicit injected
ports because their provider/environment composition is outside the browser
boundary.

## Execution behavior

Target omitted means `AUTO`.

If the user supplies provider/model/reasoning, the Portal passes a typed explicit
target to `ClientExecutionRuntime`. Runtime authorization remains authoritative;
the UI is not an authorization boundary.

The Portal never exposes orchestration controls such as max cycles, retries,
delays, timeout caps or administrative quota configuration.

## Credential delegation

A human user can issue only integration scopes derived from the customer's
permission matrix. Portal/administrative permissions are not delegable.

Secrets are returned only from the mutation response that created or rotated
them. Metadata pages never persist or render the secret, hash or salt.

## Client-safe rendering

Portal DTO/rendering is allowlist-based. It excludes provider account,
provider credential, provider secret, provider credits, provider pricing,
provider cost, currency, internal commercial metadata and future
`client_charge`.

Activity is a metadata-only, ownership-scoped audit surface. Raw payloads,
responses and secrets are not copied into activity records.

## API documentation

The Portal serves the canonical Client API OpenAPI document by injection at
`/openapi.json`; it does not generate a divergent Portal-owned API contract.
The complete human manual remains owned by #27.

## External deployment gate

A real OIDC deployment requires IdP-side registration/configuration such as
redirect URIs and client configuration. That is an environment/credential gate
and must not be simulated by adding local passwords or weakening the BFF model.

CI uses a synthetic OIDC broker and no paid provider.

## Validation

The isolated `Customer portal authorization` gate covers:

- customer membership/session selection;
- CSRF/same-origin/session revocation;
- permission-aware navigation;
- ownership derived from membership despite hostile tenant/client form fields;
- AUTO target omission and explicit target preservation;
- absence of administrative Portal routes;
- client-safe provider/model rendering;
- one-time credential secrets and bounded delegated scopes;
- recent-MFA requirement for credential mutations;
- owner-scoped technical usage and estimates;
- owner-scoped metadata-only activity;
- canonical OpenAPI exposure;
- PostgreSQL composition smoke without provider calls.
