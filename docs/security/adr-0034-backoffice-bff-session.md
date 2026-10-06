# ADR — Backoffice BFF and server-side browser sessions

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #22  
**Depends on:** #54, #138

## Decision

Backoffice is a separate server-side BFF/web application. It does not reuse the
service-client bearer API as a browser session mechanism.

Human login is delegated to a trusted OIDC broker port. That adapter is responsible
for Authorization Code + PKCE, state, nonce, issuer/audience/signature/time validation
and returns only a trusted `HumanAuthenticationContext`. ORQETIA does not parse an
unverified browser-supplied token to establish authority.

## Session

After OIDC and local #138 authorization succeed, ORQETIA creates a new random browser
session. PostgreSQL stores only SHA-256 of the session token and CSRF token.

Baseline:
- host-only `__Host-` session cookie;
- Secure + HttpOnly;
- SameSite=Lax;
- 15 minute server-side idle timeout;
- 8 hour absolute lifetime;
- immediate server-side revocation;
- local binding/status/roles revalidated on every request.

The pre-login OIDC transaction cookie is never promoted into the authenticated session.

## MFA and step-up

Backoffice login itself requires recent MFA according to #54/#138. Sensitive
administrative actions additionally re-evaluate the permission and authentication
freshness when the action is executed.

## CSRF

State-changing browser requests require:
- exact allowed Origin;
- same-origin/same-site Fetch Metadata when present;
- synchronizer token bound by a server-side hash to the session;
- CSRF cookie/form equality.

CORS is not treated as CSRF protection.

## Browser security

Responses use no-store caching, restrictive CSP, frame denial, no-referrer and
permissions policy. No OAuth access/refresh token is stored in localStorage,
sessionStorage or HTML.

## Composition

A deployment without a correctly configured trusted OIDC adapter fails closed at
login. Local/security tests use a synthetic broker; there is no development password or
hidden bypass.

## Validation

The isolated Backoffice web gate covers migration, cookie/session properties, MFA,
binding disable, CSRF/Origin rejection and logout revocation.
