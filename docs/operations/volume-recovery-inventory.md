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
| `caddy_config` | `/config`; gateway RW | Candidate derived configuration; must verify actual inventory and startup source | Versioned Caddyfile plus compatible Caddy image; classification must distinguish autosave from indispensable state and must not conflate it with caddy_data |

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

Host audit is pending at initial documentary integration. Inspect named volumes
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
in #173 and append the observed results here before final closure.

## Residual risks

Same-host backups do not survive total host/disc loss. ACLs are not encryption
proof. Host-loss, RPO/RTO and RC are not demonstrated. Follow the separate
[host-loss plan](host-loss-recovery-plan.md) and [backup contract](backup-restore-dr.md).
