# ADR-0009 — Authentication and authorization architecture

- Status: **Accepted**
- Issue: #12
- Depends on: #21, #53, #13
- Security controls: SEC-001, SEC-002, SEC-007, SEC-009, SEC-010, SEC-011

## Decision

ORQETIA does not implement a custom OAuth/OIDC authorization server or password identity system in the baseline.

A standards-compliant external Identity Provider / Authorization Server authenticates human and service principals.

ORQETIA public/admin APIs act as OAuth 2.0 resource servers and enforce authorization server-side using:
- validated principal identity;
- tenant/client ownership;
- Backoffice/customer role;
- OAuth scope;
- resource/action;
- property-level policy;
- resource state and security context where relevant.

Authentication proves a principal. It does **not** by itself authorize an ORQETIA resource.

## Principal types

- SERVICE_CLIENT — machine/application identity owned by one ORQETIA client.
- CUSTOMER_HUMAN — human subject with tenant membership.
- BACKOFFICE_HUMAN — ORQETIA administrative subject.
- INTERNAL_SERVICE — tightly scoped internal process identity where network/process separation eventually requires it.

Provider credentials are **not principals** and never authenticate to ORQETIA client APIs.

## Service-to-service flow

Baseline:
- OAuth 2.0 Client Credentials grant;
- short-lived access token;
- no refresh token for client-credentials grant;
- API accepts access token, not the long-lived client credential itself.

Client authentication preference:
1. private_key_jwt when supported and operationally appropriate;
2. high-entropy client secret as interoperable baseline;
3. mTLS for deployments/clients that justify certificate lifecycle complexity.

A permanent global shared API key is prohibited.

## Access token profile

Preferred baseline: signed JWT access tokens from the configured Authorization Server, because ORQETIA can validate them locally without making every API request depend on IdP availability.

Requirements:
- exact configured HTTPS issuer;
- exact audience including orqetia-api;
- signature validated against trusted issuer keys;
- explicit asymmetric algorithm allowlist;
- exp required and validated;
- nbf/iat validated when present according to profile;
- subject/client identifier required;
- token type/profile validated where supported;
- scope parsed from an explicit claim profile;
- bounded clock skew;
- no acceptance of alg=none;
- no HS algorithm when the trust model is asymmetric;
- unknown kid may cause one bounded JWKS refresh, then fail closed;
- JWKS cache has bounded lifetime and safe refresh behavior.

Opaque access tokens remain a future-compatible option if the selected IdP/deployment requires introspection, but that is a different runtime dependency profile and must be configured explicitly.

## Token lifetime

Default target for service-client access tokens: no more than 10 minutes.

High-risk administrative/browser behavior is governed by #54 session and step-up controls.

Exact IdP capability may tighten this value; increasing materially requires risk review.

## Revocation model

Short token lifetime is the first containment layer.

Additionally:
- revoked/suspended tenant/client/credential is checked against authoritative ORQETIA status before high-value execution;
- Backoffice/customer membership and resource authorization are not trusted solely from long-lived token claims;
- high-risk revocation can deny a still-cryptographically-valid token based on server-side status/version;
- human session revocation is defined in #54.

## Client credential lifecycle

#24 defines client integration credentials in detail.

Baseline security semantics:
- credentials belong to one service client;
- clear secret shown once when secret-based authentication is used;
- server/IdP retains only the form required to authenticate, preferably non-recoverable/hash-only where supported;
- rotate by overlap window or explicit replacement policy;
- revoke immediately;
- never log/trace/report the secret;
- client_id is not secret;
- credential fingerprint/id may be used for audit.

## Human authentication

Human login is detailed in #54.

Baseline here:
- OIDC Authorization Code flow;
- PKCE;
- exact redirect URI;
- state and nonce validation;
- server/BFF-managed browser session preferred;
- S2S token is not reused as a human browser session.

## Authorization data sources

### Token/identity source
May provide:
- subject;
- authentication issuer;
- client/application identifier;
- coarse scopes;
- authentication strength/freshness metadata.

### ORQETIA authoritative identity/control data
Provides:
- tenant/client ownership;
- active/suspended/revoked state;
- customer membership;
- Backoffice role/privileges;
- effective provider permissions;
- execution policy/quota assignment;
- resource ownership.

Authorization-critical mutable facts are revalidated server-side where stale claims could grant access.

## Scope taxonomy

Client/service scopes:
- sessions:read
- sessions:write
- tasks:read
- tasks:write
- tasks:cancel
- usage:read
- estimates:write
- providers:read
- models:read

Customer-human scopes/roles may add:
- credentials:read
- credentials:write
- credentials:rotate
- credentials:revoke

Backoffice privileges are a separate namespace/policy set and are never inferred from client scopes.

Administrative privileges include fine-grained capabilities such as:
- tenants:admin
- clients:admin
- policies:admin
- quotas:admin
- providers:admin
- provider_credentials:admin
- pricing:admin
- finance:read
- operational_intelligence:read
- security_audit:read

Exact route-to-privilege mapping is versioned with the API/backoffice implementation.

## Authorization decision algorithm

For protected action:
1. validate authentication/token/session;
2. resolve principal type and active status;
3. resolve OAuth scope / Backoffice/customer role;
4. load authoritative target ownership/state;
5. verify tenant/client relationship;
6. evaluate action/function authorization;
7. evaluate property/field authorization;
8. apply step-up/security conditions where required;
9. execute;
10. audit privileged or sensitive action/read where policy requires.

Failure is deny-by-default.

## Object lookup behavior

Client-facing resource access should avoid confirming existence of unauthorized cross-tenant objects.

Use 404 vs 403 consistently according to the endpoint's information-leak policy.

Do not expose authorization failure details that let an attacker enumerate tenants/users/credentials.

## Property-level authorization

Separate request/response schemas are preferred for:
- client vs admin writes;
- client vs internal financial/provider data;
- customer-human vs Backoffice credential operations.

Mass assignment into ORM/domain entities is prohibited.

Financial/provider-secret fields are never removed only by UI hiding; they are absent from client-facing serialization contracts.

## Internal service authentication

While API/worker are initially deployables of the same product, any future networked internal service boundary requires:
- independent service identity;
- least privilege;
- authenticated transport;
- explicit audience;
- no trusted source-IP/header-only authentication.

## CORS / trusted headers

Authentication is not delegated to arbitrary reverse-proxy headers.

Forwarded headers are accepted only from configured trusted proxies.

CORS is an origin policy, not an authentication/authorization control.

## Negative contract tests

Mandatory:
- invalid signature;
- wrong issuer;
- wrong audience;
- expired/not-yet-valid token;
- unsupported/none/symmetric algorithm confusion;
- unknown key after bounded refresh;
- missing required scope;
- service client attempts Backoffice privilege;
- valid tenant A token requests tenant B object;
- token client identity conflicts with request client_id;
- suspended/revoked tenant/client/credential attempts new execution;
- lower role requests protected response field;
- filter/export cannot bypass resource/property policy;
- stale privilege state is rechecked where immediate revocation is required.

These map to threat model AB-001..AB-008 and related cases.

## Local/test strategy

CI uses synthetic keys/tokens and fake issuer/JWKS fixtures only.

No real IdP credential or paid identity service is required for ordinary tests.

A deterministic test issuer component may sign test JWTs but is never enabled as a production authorization server.

## IdP portability

ORQETIA configuration uses standards-level values:
- issuer;
- authorization/token/end-session metadata for browser flow as applicable;
- JWKS/discovery;
- audience/client registrations;
- claim mapping.

Vendor-specific IdP admin/provisioning adapters, if needed for Client Portal credential lifecycle, remain behind an IdentityProviderAdminPort.

Changing IdP should not alter authorization semantics.

## Consequences

Positive:
- avoids custom identity protocol/password storage;
- keeps authorization anchored to ORQETIA ownership;
- short tokens reduce credential compromise window;
- IdP remains replaceable.

Costs:
- deployment depends on a trusted IdP;
- service-client provisioning must integrate with IdP client registration/management;
- revocation-sensitive actions require server-side status checks rather than trusting token lifetime alone.
