# ADR — Backoffice identity bindings, roles and step-up authorization

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #138

## Decision

Human authentication remains external OIDC per #54. ORQETIA stores no human password.
It does, however, own local authorization for Backoffice identities.

The stable external identity key is:

`issuer + subject`

A local binding maps that identity to ACTIVE/DISABLED state and a versioned baseline
role matrix. No IdP claim named "admin" or browser request field creates Backoffice
authority by itself.

## Roles and permissions

Baseline roles are ADMIN, PROVIDER_OPERATOR, FINANCE_ANALYST, SECURITY_ADMIN and
SUPPORT_READONLY. Roles expand to an explicit server-side permission matrix.

Unknown roles are rejected. Missing bindings are default-deny. A locally disabled
binding remains denied even if the external IdP authenticates successfully.

Role changes use optimistic binding versions and do not mutate issuer/subject.

## MFA and step-up

The application consumes a trusted authentication context produced by the OIDC/BFF
boundary: authentication time, MFA satisfaction, AMR and ACR when available.

Sensitive permissions require both:
- the permission granted by the local role matrix; and
- recent MFA/step-up, baseline maximum age 5 minutes.

The browser cannot forge this by sending an MFA header/body field because the evaluator
accepts only the server-created BackofficePrincipal.

## Audit

Binding creation, role/status changes and denied disabled-binding authorization emit
safe audit events. Passwords, OAuth tokens, session cookies and factor secrets do not
exist in this boundary.

## Bootstrap

#58 remains the authority for first-admin/bootstrap and break-glass. It may create the
first binding through this service only after its one-time, MFA-bound recovery
preconditions are satisfied.

## Backoffice integration

#22 uses this authorization service for every server-side session/request and performs
permission + step-up checks before sensitive mutations.

## Validation

Validation is isolated to migration plus role/default-deny/disable/step-up/version
tests. No real IdP is required.
