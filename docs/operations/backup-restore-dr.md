# ORQETIA Backup, Restore and Disaster Recovery

Issue: #148
State: DEVELOPMENT
Depends on: #33, #56, #144

These objectives are engineering recovery targets for the reference deployment, not a public SLA.

## Initial objectives

- RPO target: <= 15 minutes for mutable PostgreSQL-owned state when scheduled backups are enabled.
- RTO target: <= 60 minutes for a documented manual restore of the reference PostgreSQL deployment.
- Client-private artifacts stored in PostgreSQL are part of the same backup consistency boundary.
- Queue/outbox/inbox and immutable policy/pricing/accounting facts must be restored consistently with the database snapshot.
- Read models/rollups remain rebuildable; they are not allowed to become the sole factual recovery source.

A deployment that cannot meet these targets must record its own tighter or looser targets before RC; it must not silently claim the reference targets.

## Backup boundary

PostgreSQL backup includes database schemas/tables, durable queue state, client-private artifacts, accounting facts, policy/catalog snapshots, identity metadata and audit metadata.

Database backup must not contain a root KEK or managed secret-store master credential. Provider-secret recovery material is governed separately by #56. A database restore alone must not be sufficient to recover envelope-encrypted provider secrets without the authorized external key material.

Backups are encrypted at rest in a real deployment and access is least-privilege.

## Restore order

1. isolate the target environment and stop conflicting writers;
2. verify backup provenance/time and select a restore point;
3. restore PostgreSQL into a clean database;
4. verify Alembic revision and expected schemas;
5. restore/attach approved secret-store or KMS recovery material separately;
6. verify a synthetic secret can be resolved when the deployment uses a recoverable secret backend;
7. validate client-private artifact ownership/readability with synthetic data;
8. reconcile queue leases/outbox/inbox and allow expired leases to follow canonical recovery;
9. rebuild/reconcile read models when necessary;
10. run readiness/security checks before reopening traffic.

Never use a real provider credential as restore-test material.

## Corruption and partial recovery

- Do not restore one bounded-context table in place while unrelated writers remain active unless an explicit repair plan proves consistency.
- Prefer full database restore to a clean target for disaster recovery.
- For logical repair, preserve evidence and reconcile from authoritative ledger/immutable snapshots rather than editing derived rollups blindly.
- Queue work may be replayed only through existing idempotency/recovery contracts.
- Revoked credentials remain revoked after restore; restoration is not authorization to resurrect revoked secret material.

## Synthetic drill

The repository script scripts/postgres_restore_drill.sh performs a bounded drill:

- inserts one synthetic client-private artifact;
- captures source Alembic revision;
- creates a real custom-format pg_dump using postgres:18 tools;
- restores into a separate temporary database with pg_restore;
- verifies Alembic revision parity;
- verifies the synthetic artifact;
- verifies expected ORQETIA schemas;
- deletes the source marker and restored database on exit.

The drill uses only synthetic data and does not call any provider.

## Deployment-specific secret-store drill

Before RC on a concrete deployment using managed secret storage or envelope encryption, run a separate synthetic secret restore check that proves:

- recovery material is stored separately from the database backup;
- authorized runtime can resolve the restored synthetic secret;
- an invalid/revoked reference fails closed;
- clear secret material does not appear in logs or backup artifacts.

This step is environment-specific because #56 deliberately does not mandate one KMS/secret-manager vendor.

## Evidence

Every restore drill should record UTC start/end, backup identity, source/restored Alembic revision, restored environment identifier, result and operator/automation identity. Do not record secret values.
