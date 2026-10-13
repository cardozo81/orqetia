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

## Offline archive integrity preflight — #175

An operator can run the [read-only preflight](../../scripts/dr_manifest_preflight.py)
locally **before** any authorized off-host transfer or host-loss rehearsal. The
script does not discover, create, restore, export, upload or modify backups; it
never calls providers or Docker and does not change Windows trust stores.

Only a private JSON manifest supplied by the operator and the three exact
archive basenames (`postgres.dump`, `local-data.tar`, `caddy-data.tar`) inside an
explicit `--backup-root` are inspected. Artifact SHA-256 hashes and byte counts
are compared against independently recorded expected values by streaming the
file contents. Missing, modified, non-regular or symlinked inputs are rejected.
Unexpected manifest fields and unknown filenames are rejected without printing
their values. The tool also validates that deployment SHA, Alembic revision,
runtime image digest and Compose/Caddyfile hashes are well-formed **metadata**;
it does **not** independently compare those deployment identities to running
images, installed migrations or source files. Those checks belong to the
controlled host-loss rehearsal.

Example manifest **structure only** (all hashes, sizes, dates and identities
below are fictitious and must not be accepted as actual backup evidence):

```json
{
  "schema_version": 1,
  "repository_sha": "0000000000000000000000000000000000000000",
  "alembic_revision": "synthetic_revision",
  "runtime_image_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "compose_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "caddyfile_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
  "artifacts": {
    "postgres.dump": {
      "sha256": "4444444444444444444444444444444444444444444444444444444444444444",
      "size_bytes": 1,
      "captured_at_utc": "2026-10-13T10:00:00Z"
    },
    "local-data.tar": {
      "sha256": "4444444444444444444444444444444444444444444444444444444444444444",
      "size_bytes": 1,
      "captured_at_utc": "2026-10-13T10:00:00Z"
    },
    "caddy-data.tar": {
      "sha256": "4444444444444444444444444444444444444444444444444444444444444444",
      "size_bytes": 1,
      "captured_at_utc": "2026-10-13T10:00:00Z"
    }
  }
}
```

Create a separate private manifest using actual independently recorded capture
timestamps in UTC, file lengths and SHA-256 digests of the existing backup files.
Keep it **outside the repository** with restricted access; do not publish paths,
backups, secrets, certificate material or manifest contents in issues/CI logs.

Run from the ORQETIA repository root, specifying the existing location of
original backups; no staging copies are required:

```console
python scripts/dr_manifest_preflight.py --manifest "<operator-private-manifest.json>" --backup-root "<existing-backup-directory>"
```

Optional `--max-age-minutes 15` and `--max-skew-minutes 5` reject artifacts whose
**declared** capture times exceed those configured limits. These are operator-
chosen **staleness/skew proxies**, not a claimed 15-minute RPO or a test of
cross-artifact transactional consistency. Future timestamps beyond five minutes
are rejected. The manifest is capped at 16 KiB; inputs are never edited.

Exit 0 means only that local artifact bytes match the supplied manifest and its
declared metadata is syntactically consistent; exits 1/2 indicate validation or
input errors. **Even on PASS the command prints `HOST-LOSS RECOVERY NOT PROVEN`**.
This tool cannot establish independent failure-domain copies, encryption,
separate key custody, survivability of the original host, a working restored
application, or measured RPO/RTO. None of the decision/acceptance requirements
below are waived. #171/#173/#174 remain closed on their original evidence.


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
