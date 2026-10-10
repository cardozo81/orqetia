# Host-loss recovery plan

Status: **DEVELOPMENT**. Roadmap: #47. Gap register/decision owner: #48.
Volume classification: #173. Historical isolated backup acceptance: #171.
This is a plan, not evidence that host-loss recovery has succeeded.

## Current protection

The concrete backups tested in #171 remain on the original host. PostgreSQL
restore and archive integrity are evidenced; integrated services on restored
state and total host/disc loss have not been homologated. RPO <= 15 minutes and
RTO <= 60 minutes in [backup-restore-dr.md](backup-restore-dr.md) are engineering
targets, not measurements or a public SLA. File ACLs alone prove no encryption.

## Decision required before execution

Record in #48 an approved independent destination/failure domain, real custodian,
least-privilege access, encryption/authentication mechanism, separate recovery-key
custody, retention/deletion policy, accepted costs (including zero-cost limits)
and an isolated rehearsal window. Do not upload backups, provision storage,
create accounts, buy services or copy recovery keys until explicitly authorized.

## Ordered isolated rehearsal, after authorization

1. Freeze a reviewed recovery manifest: repository SHA, Compose/Caddyfile,
   compatible image identities, database/schema revision, backup capture times,
   integrity hashes and separate secret-store dependencies. Keep private paths
   and material private; publish only aggregates and safe references.
2. Verify availability/decryption/integrity from the approved independent domain
   on a clean, isolated target. Preserve originals and avoid public ports/traffic.
3. Restore a consistent PostgreSQL snapshot with canonical ownership,
   queue/outbox/inbox, artifacts and immutable accounting/policy facts together.
   Validate schema, Alembic, integrity and revoked-credential state.
4. Restore installation-specific local_data and caddy_data from matching approved
   material, separately from database key custody. Do not silently regenerate
   credentials, users or CA. Reconstruct caddy_config only after #173 proves its
   derived nature. Follow the [five-volume matrix](volume-recovery-inventory.md)
   for nested oidc_state coverage and temporal invalidation of missing markers.
5. Start isolated services using canonical lease recovery/idempotency. Use only
   synthetic credentials/provider fixtures for test interactions. Validate
   readiness, login/session ownership, tenant/client authorization, tasks/results,
   queue convergence and TLS without altering the original host trust store.
6. Measure recovered data cutoff versus failure time (observed RPO), and elapsed
   time from declared recovery start to accepted service readiness (observed RTO).
   Record UTC milestones, failures, missing dependencies and operator acceptance.
7. Clean up only identified rehearsal resources after evidence review. Keep
   originals, operational volumes and the source installation intact.

## Acceptance and boundaries

Require independent-source recovery, verified identity/secret dependencies,
integrated functional acceptance, measured RPO/RTO and an approved custody plan
before claiming host-loss coverage. An isolated fresh Caddy startup or a database
restore alone cannot satisfy these criteria.

No `docker compose down -v`, operational restore, credential/certificate changes,
trust-store changes, paid provider, RASAi mutation or release is part of M11
documentary integration. #171 stays closed; new material changes use #84 delta
issues. #162/#164 and lifecycle #46 remain independent pre-RC gates.
