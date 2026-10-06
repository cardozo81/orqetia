# Immutable audit retention and integrity

Issue: #150  
Policy: `ORQETIA_AUDIT_DEVELOPMENT` v1  
State: DEVELOPMENT

## Classes and retention

| Audit class | Development retention |
| --- | ---: |
| SECURITY_ADMIN | 730 days |
| CLIENT_ACTIVITY | 365 days |

These values are engineering defaults, not legal maxima/minima. Deployment policy
must be reviewed before RC against contractual, regulatory and incident-specific
preservation requirements.

Every new canonical audit event stores the policy id/version and its computed
`retain_until`. `NULL means forever` is not used.

## Append-only boundary

`audit.audit_events` is append-only at the PostgreSQL boundary. Ordinary
`UPDATE` and `DELETE` are rejected by a trigger. The legacy
`audit.customer_activity_events` table receives the same mutation protection.

Application code has no ordinary purge method for audit events. Expiration marks
eligibility for a separately authorized archive/disposal procedure; it does not
silently weaken immutability. Legal/security holds must be evaluated before any
future privileged disposal mechanism is added.

## Integrity

New canonical rows contain a deterministic SHA-256 hash over their immutable
metadata, including class, actor/resource references, owner, correlation id,
timestamps and retention policy.

The audit adapter verifies the hash before insertion and exposes bounded synthetic
reconciliation that reports event ids whose persisted data no longer matches the
stored hash.

This detects accidental/unauthorized mutation inside the application boundary.
It is not a substitute for external WORM storage or SIEM; those remain optional
hardening choices.

## Data minimization

The canonical ledger deliberately has no raw payload/body field. Audit producers
may record action/result, actor/resource identifiers, owner, correlation and
timestamps only.

Never copy:
- provider/client secrets;
- passwords, tokens or recovery codes;
- raw prompt/input/output;
- raw provider response;
- financial payloads not required for audit provenance.

## Correlation and ordering

Every canonical event requires a correlation id and both `occurred_at` and
`recorded_at`. Ordering is deterministic by occurred_at + UUIDv7 event id.
