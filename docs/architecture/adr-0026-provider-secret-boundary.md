# ADR — Provider credential secret boundary

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #25

## Decision

Provider credentials are owned by the ORQETIA Control Plane. Clients never provide,
retrieve or receive provider API secrets in the baseline product.

The Control Plane persists only safe credential metadata. Secret material lives behind
a `ProviderSecretStore` port implemented by a managed secret manager or envelope
encryption adapter in production composition.

## Metadata

`control.provider_credentials` stores:

- safe credential UUID;
- provider and provider-account logical reference;
- opaque secret reference;
- non-reversible SHA-256 fingerprint prefix;
- key version and independent state version;
- status;
- creation/rotation/revocation/expiration timestamps;
- last successful/failed preflight timestamps.

There is no plaintext or encrypted secret-material column in the ORQETIA relational
metadata table.

## Secret handling

Secret values use an explicit redacting wrapper. String/repr output never reveals the
material. Backoffice-safe views omit the secret reference as well as the material.

Create writes the secret first and removes it if metadata persistence fails.

Rotation writes a new secret, atomically advances metadata through optimistic state
versioning, then retires the old reference. A persistence failure deletes the new
candidate and preserves the old active secret.

Revocation commits REVOKED metadata first, making runtime use fail closed, then retires
the secret. Failure to retire an already-inactive historical reference is recorded as
an audit cleanup failure and does not reactivate the credential.

## Preflight

Provider-specific preflight is an optional port. It receives the secret only in memory.
The result updates safe success/failure timestamps and audit metadata. No secret,
request header, provider response body or token is added to the audit contract.

No live provider preflight is required by ordinary CI.

## Runtime and deployment

Production may use only the managed/envelope secret modes already governed by #56.
The in-memory secret store exists solely as a deterministic test adapter.

Provider account modeling, external balances and multi-credential selection are
downstream work in #59.

## Validation

Tests prove redaction, safe views, rotation rollback, revocation effectiveness,
preflight handling and absence of secret material from audit records. CI remains
provider-free and adds no variable financial cost.
