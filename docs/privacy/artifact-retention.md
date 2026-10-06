# Client-private artifact retention and deletion

Issue: #149
Policy version: 1
State: DEVELOPMENT

This policy materializes the data-lifecycle boundary from #55 over the durable
client-private artifact store delivered by #144.

## Default DEVELOPMENT retention

| Data class | Default retention |
| --- | ---: |
| client request artifact | 30 days |
| client terminal result artifact | 30 days |
| raw provider response artifact | 14 days |
| sanitized exchange evidence | 14 days |

These are engineering defaults, not a statement of legal minimum/maximum. A real
deployment must reconcile contractual/legal obligations before RC.

Accounting/audit retention is outside this policy. Audit immutability/retention is
owned by #150.

## Enforcement

ClientArtifactRetentionCoordinator requires an explicit tenant/client OwnershipScope
and one versioned ClientArtifactRetentionPolicy. PostgreSQL deletes only rows owned by
that scope and whose artifact kind is older than its own cutoff.

Repeated purge is idempotent. Reads of deleted client results return unavailable/None
through the existing artifact boundary; no fallback to internal tables is permitted.

## Holds

Every purge call must make an explicit hold decision by passing RetentionHold or None.
An active hold skips deletion completely for that owner. Holds may represent a security
investigation, legal preservation requirement or other authorized retention exception.

A future administrative hold registry may provide these decisions, but purge code must
never infer that absence of an integration/configuration means a hold can be ignored.

## Offboarding

purge_offboarded_owner removes all client-private artifact/evidence content for one
owner after the controlling lifecycle confirms deletion is allowed. It remains
ownership-scoped and respects an active hold.

Tenant/client metadata, accounting facts and audit/security records have separate
lifecycle rules and are not silently removed by artifact offboarding.

## Safety rules

- no cross-owner purge;
- no purge based on browser-supplied tenant/client authority;
- no raw secret material in retention metadata;
- no delete of audit/accounting facts through this adapter;
- provider response retention remains shorter than client result retention by default;
- CI uses synthetic content only.
