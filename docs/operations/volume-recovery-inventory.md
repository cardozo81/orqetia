# Five-volume recovery inventory

Status: **DEVELOPMENT**. Issue: #173. Parent: #47 / #48; governance epic #40.
Dependencies: #170 and #171 completed; this classification does not reopen them.
Contract baseline: `cf8c00e6cafcce363dddac3b04fdecc845f9101c`.

## Recovery matrix

| Volume | Mount / consumers | Data class and integrity | Recovery dependency / loss behavior |
| --- | --- | --- | --- |
| `postgres_data` | `/var/lib/postgresql`; postgres | Authoritative database: identities, sessions, tasks, queue/outbox/inbox, client-private artifacts, accounting and audit facts | Consistent PostgreSQL 18 dump, schema/Alembic compatibility and separate secret recovery material; empty storage loses durable facts, bootstrap is not restore |
| `local_data` | `/var/lib/orqetia`; bootstrap/backoffice RW; worker/portal/local-idp RO | Installation-specific credentials, signing and secret-store material; sensitive, not safely replaceable by regenerating bootstrap | Protected matching backup plus database point and permissions; loss can invalidate access/signatures or secret resolution; do not rotate/reseed as a recovery shortcut |
| `oidc_state` | `/var/lib/orqetia/oidc-used`; backoffice/portal RW, nested over local_data | Ephemeral anti-replay ledger, security-relevant while signed transactions remain valid; zero-content markers created atomically | Consistent short-lived ledger or controlled temporal invalidation before reopening login; missing/old ledger can permit replay of still-valid consumed transactions |
| `caddy_data` | `/data`; gateway RW | Installation-specific PKI/private keys and TLS storage; preserve identity and trust | Protected verified backup; fresh generation is not equivalent to recovering the existing CA and may break trusted TLS |
| `caddy_config` | `/config`; gateway RW | Derived configuration, verified against versioned Caddyfile and isolated startup below | Recreate from versioned Caddyfile and compatible Caddy image; existing startup uses that source, not resume; caddy_data is a separate PKI dependency |

The mount topology comes from [compose.yaml](../../compose.yaml). The declarative
gateway source is [Caddyfile](../../deploy/local/Caddyfile); it disables the admin
API and uses internal TLS. Never print raw Docker environment, marker names,
session/token filenames, private certificates or secret contents.

## Existing backup evidence and nested mounts

#171 proved `postgres.dump`, `local-data.tar` and `caddy-data.tar` in an isolated
restore/extraction, not five independent volume images. Its accepted report
includes 20 local-data files, of which 14 were OIDC markers, and nine Caddy files.
Therefore absence of a separate oidc_state TAR is not proof of absent marker
coverage. A TAR captured through a BFF parent mount can include the nested
oidc_state mount; copying the local_data volume directly need not include it.
Verify aggregate archive coverage and capture topology before claiming parity.
There was no independent caddy_config backup/reconstruction proof in #171.

## OIDC expiry and safe recovery boundary

[local_oidc.py](../../src/orqetia/infrastructure/local_oidc.py) uses a 300-second
TTL for signed transactions and authorization codes, validates signature,
issuer/audience/callback, state/nonce/PKCE, transaction binding and authentication
age before creating a marker with O_CREAT | O_EXCL and mode 0600. The requested
directory creation mode is 0700; existing Docker mount permissions must be
measured rather than inferred from that mkdir call.

Expiry rejects when `exp < int(time.time())`: equality is still accepted.
The transaction deadline also constrains a code issued near the end of login.
Markers contain no expiry payload. No automatic marker cleanup is present in
this module; check other tracked processes and external maintenance evidence
before describing deployment-wide retention.

Do not discard or restore an old ledger while login is serving traffic. For a
future approved recovery with missing/incomplete markers: stop issuance and
callbacks for all BFFs/IdP, establish the last possible issuance time, ensure
the verifier clock is trustworthy and strictly beyond the 300-second transaction
window plus a documented clock uncertainty margin, then reopen with new login
transactions. If clock history or issuance cutoff cannot be established, keep
login unavailable and escalate; this is not permission to rotate signing keys.
Existing application sessions are a separate database/session-store boundary.

Any future cleanup must preserve markers for potentially valid transactions,
handle clock rollback, restored timestamps, concurrent consumers and bounded
filesystem work. Age alone after an untrusted restore is not sufficient.
Measure aggregate age/count/bytes first; open a separate bounded issue for an
evidenced retention gap. No operational purge is authorized by this inventory.

## Read-only audit protocol and current evidence

For repeat audits, inspect named volumes
and selected container metadata only: existence, destination, RW flag, IDs,
image/start/restart state and health. In existing containers aggregate file count,
logical bytes, allocated size where available, age range and permission modes;
exclude nested oidc_state when counting local_data itself. Do not attach
operational volumes to new containers or export their contents.

For Caddy compare derived configuration internally and emit equality/counts only.
If needed, prove cold startup with versioned Caddyfile in a disposable networkless
container with fresh temporary storage, no host ports, no operational mounts and
the existing image. Do not import its synthetic CA into any host trust store.
Record limitations: isolated fresh PKI does not prove recovery of the real PKI.

Capture before/after service identities, mounts, starts/restarts and health.
Operational services may write autonomously; do not claim a transactionally
frozen database from filesystem metadata. Record aggregates and explicit unknowns
in #173 with the observed results and limitations before final closure.

## Observed host evidence — 2026-10-10

Read-only snapshots at **12:27:29 UTC** and **12:31:02 UTC** confirmed all five
`orqetia-local_` named volumes exist (local driver) and all mounts match the matrix.
File bytes below are logical sizes; the allocated column sums file blocks only,
excluding directory/inode overhead. Zero-byte markers still consume filesystem
metadata. The local_data row explicitly excludes the nested oidc_state mount.

| Volume | Regular files | Logical bytes | Allocated file bytes | File mode / UID:GID |
| --- | ---: | ---: | ---: | --- |
| `postgres_data` | 2,578 | 91,517,947 | 91,594,752 | 0600 / 999:999 |
| `local_data` | 6 | 9,024 | 28,672 | 0600 / 10001:10001 |
| `oidc_state` | 14 | 0 | 0 | 0600 / 10001:10001 |
| `caddy_data` | 9 | 3,528 | 36,864 | 0600 / 0:0 |
| `caddy_config` | 1 | 2,111 | 4,096 | 0600 / 0:0 |

Observed mount-root modes: postgres_data 1777 (container data parent), local_data
0700, oidc_state 0755, caddy_data/config 0755. The OIDC mkdir(0700) request does
not tighten an existing mount root; the observed BFF parent is 0700 and markers
are 0600. No permission change was made; no exploitability claim follows from
the mount-root mode alone.

The three original backup sizes/hashes still match the accepted #171 manifest:
191,395 bytes for the dump, 30,720 for local-data TAR, 16,896 for Caddy TAR.
Archive metadata confirms **20 files = six local-data files + 14 empty OIDC
markers**, and nine Caddy files. Thus OIDC marker coverage is present inside the
existing parent-mount archive. This is historical snapshot coverage, not a claim
that a live ledger can safely be rolled back to that point. Archive contents were
not extracted or restored in this audit and no member names were published.

### OIDC classification and retention finding

Initial marker ages were **82,360..234,263 seconds**, all beyond 300 seconds.
Repository searches across src/apps/scripts/Compose found creation but no cleanup
of this ledger. The BFF image had no cron directories; Windows scheduled-task
actions had zero explicit references to oidc_state/oidc-used. Generic/external
maintenance cannot be excluded by those checks. There is no demonstrated resource
exhaustion or urgency to purge the 14 operational markers.

Two passing synthetic cases (Backoffice and Portal audiences) in
[test_oidc_state_recovery.py](../../tests/devenv/test_oidc_state_recovery.py)
prove: a new broker with the same ledger rejects consumed transactions; a missing
ledger can accept the still-valid signed tuple at the exact TTL boundary; one
second later it is rejected before marker creation; expiry does not remove the
old marker. All keys, tokens and directories were synthetic and isolated.

**Decision:** treat oidc_state as time-bounded anti-replay state, preserve it in
normal operation, and use controlled temporal invalidation for an approved
recovery with missing/incomplete coverage. A separate **#174** tracks bounded
retention/controlled cleanup, including clock and concurrency safety, before any
implementation or operational purge. This audit does not implement #174.

Subsequent #174 analysis is recorded in the
[marker retention safety specification](../security/local-oidc-marker-retention.md).
Synthetic counterexamples show that finite waiting and a same-store signed
rejection horizon do not establish safety when that horizon and the clock can
both be restored backwards. Destructive implementation remains blocked pending
an explicit non-rollback authority or restart/restore invalidation contract.
The runtime still retains all markers; there is no collector or operational purge.
The trusted-clock prerequisite of the recovery procedure above remains essential.

### Caddy reconstruction evidence

The running gateway uses `caddy run --config /etc/caddy/Caddyfile --adapter
caddyfile`, with no `--resume`. Its mounted Caddyfile matches the versioned source.
/config contains only two directories (including the root) and one regular JSON
configuration file, with no symlinks or other file types. Internal JSON comparison
of this saved configuration with `caddy adapt` from the mounted source was equal;
only the equality result was emitted. No indispensable additional state was found.

An isolated cold start used the **already installed image**
`sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f`:

- network none, zero published ports, no operational volume mounts;
- fresh 8 MiB tmpfs mounts for /config, /data and /tmp; read-only root filesystem;
- UID/GID 10001, no-new-privileges, 96 MiB memory and 0.5 CPU;
- all capabilities dropped except NET_BIND_SERVICE, needed to execute the image's
  capability-bearing Caddy binary (the first all-dropped attempt failed before
  Caddy started; the disposable container was removed);
- versioned Caddyfile copied into an isolated fixture with only the global
  `skip_install_trust` directive added, to prohibit even synthetic trust installation.

Cold startup returned **HTTP 200** on the container-local health endpoint and
recreated the saved configuration from empty /config. The regenerated JSON equals
the canonical adaptation except for the expected `install_trust=false` PKI overlay.
The temporary container and tmpfs state were removed; no named volume was created.
The local fixture contains only public configuration and no generated PKI.

To repeat this narrow proof, adapt the versioned source with the installed image,
apply only the trust-installation suppression in a synthetic fixture, start with
the isolation constraints above, compare regenerated JSON internally, check the
health endpoint, and remove only that disposable container. Do not use live
volumes, expose the admin API or publish raw configuration. This proves derived
configuration reconstruction; it does not test upstream apps, restore real PKI
or homologate the entire host. In an approved actual recovery, restore caddy_data
separately to preserve the existing CA and trust relationships.

### Preservation and validation limits

Before/after: identical ten container IDs/images/start times/restart counts/mounts
and five volume identities; eight persistent services healthy and migrate/bootstrap
exited 0. File count/size/mode aggregates were unchanged; original backup hashes
were unchanged. HTTPS live/ready returned 200 with Windows TLS verification and
no trust-store modification. No operational login or DB mutation was performed.
These checks do not claim byte-for-byte immutability of an independently running
database or automated Caddy renewal; no live database snapshot was taken.

Documentary integration `36f08e544a0d2f646ec787bd13a2516669f3d87e` passed 35/35
GitHub checks. Final audit integration SHA/check evidence belongs to #173, avoiding
a self-referential commit hash in this file. Local validation: three documentary
tests, two synthetic OIDC cases, targeted Ruff and the isolated Caddy proof.
Sanitized local artifacts: `.local/m11-before.json`, `.local/m11-after.json` and
`.local/m11-caddy-result.json`; persistent evidence is this document and #173.

## Residual risks

Same-host backups do not survive total host/disc loss. ACLs are not encryption
proof. Host-loss, RPO/RTO and RC are not demonstrated. Follow the separate
[host-loss plan](host-loss-recovery-plan.md) and [backup contract](backup-restore-dr.md).
