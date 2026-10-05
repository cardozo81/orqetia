# ADR-0010 — Human authentication, MFA, web sessions and recovery

- Status: **Accepted**
- Issue: #54
- Depends on: #12, #13, #53
- Security controls: SEC-001, SEC-002, SEC-004, SEC-007, SEC-009, SEC-010, SEC-011, SEC-012

## Decision

ORQETIA does not create a custom human identity/password protocol in the baseline.

Human authentication uses a standards-compliant external OpenID Connect Identity Provider.

Browser applications use:
- Authorization Code flow;
- PKCE with S256;
- exact registered redirect URIs;
- state and nonce validation;
- server-side/BFF session;
- secure host-only session cookie.

Browser code does not persist OAuth/OIDC bearer access or refresh tokens in localStorage/sessionStorage.

The BFF owns browser-session state and exchanges/uses IdP tokens server-side as required.

## Human principal types

- BACKOFFICE_HUMAN;
- CUSTOMER_HUMAN.

Backoffice and customer sessions are distinct authorization surfaces. A customer session never becomes a Backoffice session through scopes or request fields.

## OIDC login requirements

The BFF validates:
- exact configured issuer;
- authorization response state;
- nonce binding where applicable;
- code exchange through the configured token endpoint;
- PKCE verifier/challenge;
- ID token signature and algorithm allowlist;
- audience/client ID;
- exp/iat/nbf/profile claims as applicable;
- authentication-time/ACR/AMR claims when used for step-up;
- redirect target against an explicit allowlist.

Open redirect through return_url/next parameters is prohibited.

IdP discovery/JWKS follows the trust rules of ADR-0009.

## Session architecture

Session identifier:
- opaque, cryptographically random, high entropy;
- contains no tenant/client/role/secret data;
- mapped to server-side session state.

Cookie baseline:
- Secure;
- HttpOnly;
- host-only when deployment permits;
- Path=/;
- SameSite=Lax baseline;
- no persistent bearer token in JavaScript storage;
- __Host- cookie prefix preferred when deployment constraints allow it.

Session state stores only what is needed to operate and revalidate:
- session ID/internal key;
- subject/principal type;
- authoritative membership/role references;
- tenant/client context when applicable;
- authentication time/strength;
- last activity;
- absolute expiry;
- privilege/security version used for revocation;
- CSRF/session anti-replay state as required.

Authorization-critical mutable facts remain server-side authoritative per ADR-0009/#21.

## Session rotation

Rotate the ORQETIA browser session identifier:
- after successful authentication;
- after privilege/tenant-context elevation or material role change;
- after sensitive recovery where the prior session should not survive;
- when session fixation risk is detected.

The pre-authentication/session-bootstrap identifier cannot be promoted unchanged into an authenticated session.

## Session timeouts

Defaults are security configuration, not client-controlled request parameters.

Baseline targets:
- Backoffice idle timeout: 15 minutes;
- Backoffice absolute lifetime: 8 hours;
- Customer Portal idle timeout: 30 minutes;
- Customer Portal absolute lifetime: 12 hours;
- sensitive-action authentication freshness: 5 minutes unless a stricter action policy applies.

A deployment may shorten these values. Materially increasing them requires security review.

Session expiry is enforced server-side, not only by cookie expiration.

## Logout and revocation

Logout:
- invalidates the ORQETIA server-side session immediately;
- clears the browser cookie;
- invokes IdP logout/end-session behavior when supported and appropriate;
- does not depend solely on deleting a browser cookie.

Revocation triggers include:
- tenant/user/client suspension;
- Backoffice privilege removal;
- credential/recovery compromise;
- administrative security action;
- high-risk identity change.

Revocation-sensitive requests revalidate server-side security/membership version as required.

## MFA and phishing-resistant authentication

### Backoffice

MFA is mandatory.

Preferred order:
1. WebAuthn/FIDO2 security key or passkey;
2. TOTP as fallback;
3. one-time recovery codes for recovery only.

SMS/email OTP is not accepted as the only MFA method for privileged Backoffice access.

Where the IdP supports authentication context, ORQETIA verifies the required ACR/AMR/authentication-age semantics for privileged actions.

### Customer Portal

MFA/step-up is mandatory for security-sensitive actions including:
- create integration credential;
- rotate credential;
- revoke credential;
- change privileged membership/role;
- security/recovery actions.

Passkey/WebAuthn is preferred. TOTP may be an approved fallback.

Ordinary read-only portal access may use the tenant's configured baseline authentication policy, subject to future product policy and security minimums.

## Step-up / reauthentication

Sensitive actions require recent, sufficient authentication.

The BFF/API evaluates:
- principal type;
- authentication time;
- achieved factor/authentication strength;
- target action.

If insufficient:
- do not execute the operation;
- initiate a step-up flow through the IdP;
- bind the return to the original server-side operation context;
- rotate/update session security state after successful step-up.

A client cannot claim MFA by supplying a request field/header.

## CSRF protection

All state-changing browser requests require CSRF protection.

Baseline:
- same-site secure session cookie;
- server-generated synchronizer CSRF token bound to the session;
- token required in a non-cookie request field/header;
- Origin validation on state-changing requests where browser semantics permit;
- Fetch Metadata checks may add defense in depth;
- JSON/content-type requirements do not replace CSRF controls.

CORS is not CSRF protection.

## Account/session enumeration and error handling

Login/recovery responses avoid disclosing whether a human account exists unless the product flow explicitly requires and authorizes that disclosure.

Browser/API errors:
- do not expose IdP tokens;
- do not expose stack traces;
- do not echo raw provider/IdP error payloads;
- preserve correlation ID for support/audit.

## Recovery

Recovery is a distinct high-risk flow.

Requirements:
- short-lived, one-time recovery transaction/token;
- purpose-bound and subject-bound;
- invalid after use;
- audit event for initiation/completion/failure;
- rate limits/backoff;
- no security questions;
- no support-agent bypass that directly sets authentication state;
- no downgrade from phishing-resistant MFA to weaker factor without policy;
- recovery may revoke existing sessions and authentication methods according to risk.

Recovery codes:
- high entropy;
- displayed only at issuance/regeneration;
- stored non-recoverably (hash/verifier);
- each code one-time;
- regeneration invalidates prior unused codes.

A lost MFA device is not sufficient proof by itself.

## Password policy

Baseline: ORQETIA does not store a local human password database.

If a future IdP choice or product requirement introduces local passwords, that is a separate explicit implementation decision and must at minimum address:
- Argon2id;
- unique salts;
- breached-password screening;
- no security questions;
- password-manager/paste support;
- anti-enumeration;
- stuffing/spraying controls;
- reset/recovery threat model.

## Remembered devices

No custom long-lived "trusted device" bypass is in the baseline.

If the selected IdP provides device/passkey trust, ORQETIA consumes only standards-level authentication-strength/freshness signals and does not create a parallel bypass token.

## Session store

The concrete session store is an infrastructure choice.

It must support:
- server-side revocation;
- expiry;
- atomic rotation;
- concurrent-session policy;
- security-version invalidation;
- tenant/subject lookup for administrative revocation;
- no plaintext OAuth refresh/access token exposure to ordinary application logs/read models.

Redis may be used later if #52/performance justifies it, but session correctness cannot depend on an unsafe process-local dictionary.

## Concurrent sessions

Baseline permits multiple sessions unless a security event/policy revokes them.

Backoffice UI must eventually expose enough administrative evidence to revoke sessions or subject-wide access without revealing token material.

## Audit events

Audit at minimum:
- successful/failed login outcome category;
- logout/revocation;
- MFA enrollment/removal when visible to ORQETIA;
- step-up success/failure category;
- recovery initiation/completion;
- session/security-version administrative revocation;
- privileged credential-management operation.

Do not log:
- authorization code;
- PKCE verifier;
- access/refresh/ID token;
- session cookie;
- TOTP seed;
- recovery code;
- WebAuthn private material.

## IdP portability

Application configuration uses standards-level OIDC/WebAuthn authentication context where feasible.

Vendor-specific IdP provisioning/admin operations remain behind an IdP adapter and do not change ORQETIA authorization/session semantics.

## Consequences

Positive:
- avoids storing human password/verifier data in baseline;
- removes browser bearer-token persistence;
- supports strong MFA/step-up;
- server-side revocation and fixation protection are explicit;
- IdP remains replaceable.

Costs:
- requires an external IdP for real human login;
- BFF/session persistence becomes part of availability;
- step-up behavior must be integrated with the selected IdP's standards support.
