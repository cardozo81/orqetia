# ADR — Hashed client integration credentials

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #24

## Decision

Client integration credentials are ORQETIA credentials, not provider credentials.

The stored representation contains only a random salt, a PBKDF2-HMAC-SHA256 verifier
and a short non-secret fingerprint. The bearer secret itself is never persisted and
cannot be recovered from PostgreSQL.

An issued/rotated secret is wrapped as a one-time application value. Once revealed,
the application object cannot reveal it again. HTTP/UI transport must use
`Cache-Control: no-store` and must not log response bodies.

## Identity and ownership

Every credential is owned by exactly one tenant/client and carries an explicit scope
set. Successful bearer authentication returns trusted:

- tenant_id;
- client_id;
- credential_id;
- safe fingerprint;
- scopes.

This makes client access credential identity available for usage/reporting without
using secret bytes as a reporting dimension.

Ownership is revalidated on list/rotate/revoke. Possession of another credential id
does not grant access.

## Rotation and revocation

Rotation keeps the same credential_id, increments key_version/state_version and
atomically replaces the stored verifier. The previous bearer secret stops
authenticating immediately after commit.

Revocation is an explicit terminal state and is checked on every authentication.

last_used_at is telemetry and is updated independently from lifecycle state version so
concurrent authentication does not create lifecycle version conflicts.

## Idempotency

ISSUE/ROTATE/REVOKE store only SHA-256 of the external Idempotency-Key plus a semantic
request fingerprint. Raw idempotency keys are not persisted.

A replay of the same operation does not repeat the mutation. Because the bearer secret
is intentionally non-recoverable, replay returns metadata without revealing a secret
again. A caller that lost the first secret must perform a new rotation.

Same key + different semantic request fails closed.

## Separation from provider secrets

Provider credentials (#25/#59) may require managed recoverable secret material because
ORQETIA must call upstream providers. Client integration credentials do not: only
verification is required, so irreversible storage is the safer baseline.

## Validation

The issue is validated with isolated identity credential tests and the identity
migration chain. No external identity provider, paid API or secret manager is needed.
