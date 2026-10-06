# ADR — Secure Backoffice administrative web composition

**Status:** Accepted  
**Date:** 2026-10-06  
**Issue:** #22

## Decision

The official Backoffice is a server-rendered FastAPI BFF. Browser sessions remain
server-side per ADR-0034 and no bearer/provider secret is persisted in browser storage.

The web layer is composition only. It does not own administrative domain facts and
does not write context tables directly. Writes delegate to the application services
from #136–#141, #25/#59 and #24. Reads use typed repositories from the owning context
only where the application service has no listing projection.

## Information architecture

The baseline routes are:

- `/backoffice/tenants` — tenant/client lifecycle;
- `/backoffice/users` — external identity bindings and Backoffice roles;
- `/backoffice/policies` — immutable execution policy/target envelope;
- `/backoffice/quotas` — quota policy publication;
- `/backoffice/providers` — provider catalog, provider accounts/credentials, provider capacity and internal pricing;
- `/backoffice/client-credentials` — client integration credential lifecycle;
- `/backoffice/intelligence` — operational/financial intelligence and audited export.

Complex provider-catalog/pricing structures use bounded technical JSON forms. They are
decoded by the same canonical codecs used by persistence, so browser transport does not
define a second provider/pricing model.

## Authorization matrix

Read access is role/permission checked server-side on every route.

State-changing operations additionally require:
1. authenticated server-side session;
2. exact configured Origin;
3. same-site Fetch Metadata when present;
4. CSRF synchronizer token bound to the session;
5. the corresponding server-side permission;
6. recent MFA/step-up according to #138/#54.

Key permissions:

- `tenancy:admin` — tenant/client lifecycle;
- `users:admin` — Backoffice bindings/roles/status;
- `policy:admin` — execution policy publication;
- `quotas:admin` — quota publication;
- `providers:admin` — provider catalog/accounts/credentials/capacity/pricing;
- `client-credentials:admin` — client integration credentials;
- `reports:read` — operational/financial intelligence;
- `reports:export` — audited report export.

Navigation is never authorization. Direct route invocation without permission fails.

## Secret handling

Provider credential secrets are write-only form inputs. After create/rotate the
Backoffice redirects and never renders secret material or secret references.

Provider credential lists display safe fingerprint/key/status metadata only.

Client integration credentials follow #24 and may display the newly issued/rotated
client secret exactly once. Replays never re-display it. Responses remain `no-store`.

No provider secret appears in dashboard, export, error or HTML response.

## Cross-boundary validation

The web composition applies integration invariants that individual owner services
cannot assume:

- execution-policy targets must exist in the current approved provider catalog;
- provider credential creation must reference an existing account whose provider_id matches the credential provider;
- Backoffice client-credential operations require an ACTIVE tenant/client owner;
- provider endpoint/pricing JSON uses canonical decoders and bounded request sizes.

Provider catalog approval still does not grant tenant/client authorization; #137 is
required independently.

## Security headers

All Backoffice responses are no-store and include CSP, frame denial, nosniff,
referrer policy and restrictive permissions policy. HSTS is enabled by deployment
configuration for HTTPS environments.

## E2E validation

The directed Backoffice tests use only in-memory domain adapters plus local PostgreSQL
migration execution. They prove:

- OIDC/MFA session establishment;
- CSRF/same-origin enforcement;
- local role disable/default-deny;
- tenant/client, policy and quota administration;
- provider catalog/pricing/account/credential administration;
- provider secret non-disclosure;
- one-time client credential secret;
- Backoffice intelligence and audited export;
- role denial for a read-only principal.

No paid provider or real IdP is contacted.
