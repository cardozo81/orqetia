# Local Docker operational environment

Issue #165 extends #102; ADR 0004. Product state: **DEVELOPMENT**.
Only synthetic data and ORQETIA_TEST_PROVIDER are used. No paid provider,
Redis, Celery or alternative execution engine is required.

## Windows quick start

Prerequisites: Git and Docker Desktop with Linux containers running.

```powershell
Set-Location C:\IA-PROJETOS\github\orqetia
git pull --ff-only
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 120
docker compose ps -a
```

Compose orders PostgreSQL health, Alembic migration, idempotent bootstrap and
applications. `migrate` and `bootstrap` must exit 0; they are one-shot processes.
Application containers run as uid 10001 with read-only root filesystems and
explicit writable volumes. Canonical messaging, orchestration, authorization,
accounting and estimate engines are reused.

| Service | Image | Access / function |
| --- | --- | --- |
| postgres | postgres:18 | 127.0.0.1:5432; persistent database |
| migrate | orqetia-local-runtime:dev | Alembic to head; one shot |
| bootstrap | orqetia-local-runtime:dev | LOCAL/TEST seed and credentials; one shot |
| api | orqetia-local-runtime:dev | 127.0.0.1:8000; canonical Client API |
| worker | orqetia-local-runtime:dev | private; PostgreSQL queue and engine |
| scheduler | orqetia-local-runtime:dev | private; scheduler process |
| backoffice | orqetia-local-runtime:dev | private port 8100 behind gateway |
| portal | orqetia-local-runtime:dev | private port 8200 behind gateway |
| local-idp | orqetia-local-runtime:dev | private port 9000; synthetic identity broker |
| gateway | caddy:2-alpine | 127.0.0.1:8443; local HTTPS |

The runtime network is internal. API, PostgreSQL and gateway also join an edge
bridge for loopback publication. Worker has no outbound network. Gateway health
uses container loopback HTTP port 8080, never published; acceptance separately
verifies HTTPS/upstream routing. Worker/scheduler probes verify process presence
and DB readiness; recovery acceptance verifies actual worker progress.

## URLs and initial access

| URL | Authentication |
| --- | --- |
| https://localhost:8443/backoffice/login | local admin profile and installation token |
| https://localhost:8443/portal/login | local Viewer, Operator or Admin profile and token |
| https://localhost:8443/dev-idp/authorize | reached through signed BFF login transaction |
| https://localhost:8443/v1 | API prefix; choose an operation, not an index page |
| https://localhost:8443/openapi.json | public canonical contract |
| https://localhost:8443/health/live | public liveness |
| https://localhost:8443/health/ready | public API readiness |
| http://127.0.0.1:8000/openapi.json | direct loopback troubleshooting |

Use HTTPS for human sessions: cookies are Secure/HttpOnly. See
[all endpoints](local-endpoints.md).

| Access | Identity/key | Role / owner | Credential source |
| --- | --- | --- | --- |
| Backoffice | backoffice-admin | canonical local admin binding | local-login-tokens.json |
| Portal Viewer | client-viewer | VIEWER; Tenant A / Client A1 | local-login-tokens.json |
| Portal Operator | client-operator | DEVELOPER; Tenant A / Client A1 | local-login-tokens.json |
| Portal Admin | client-admin | OWNER; Tenant A / Client A1 | local-login-tokens.json |
| API full | client_a1_full | Tenant A / Client A1; full scopes | client-api-tokens.json |
| API readonly | client_a1_readonly | Tenant A / Client A1; read scopes | client-api-tokens.json |
| Isolation/quota control | client_b1_full | Tenant B / Client B1; HARD zero tasks | client-api-tokens.json |
| PostgreSQL | orqetia / database orqetia | development account | synthetic Compose value |
| Docker/Caddy/worker | no application login | local Docker authority | Docker Desktop user |

Client A2 is also seeded. Actual UUIDs, credential IDs, fingerprints and scopes
are in `manifest.json`. Private files live in `local_data` under
`/var/lib/orqetia/bootstrap/`: `manifest.json`, `client-api-tokens.json`,
`local-login-tokens.json`, `local-oidc-signing.key`. Provider simulator material
is in the local secret store in that volume. Private files use mode 0600.
Never put their contents in Git, logs, issues or screenshots.

Read initial access locally, without pasting output into public reports:

```powershell
docker compose exec -T backoffice cat /var/lib/orqetia/bootstrap/local-login-tokens.json
docker compose exec -T backoffice cat /var/lib/orqetia/bootstrap/client-api-tokens.json
docker compose exec -T backoffice cat /var/lib/orqetia/bootstrap/manifest.json
```

API tokens are hashed in PostgreSQL. Bootstrap retains installation-local raw
copies for initial access recovery. Credentials issued later through UI/API have
one-time secret responses; idempotent replay never reveals the secret again.

The identity adapter is synthetic, not a general standards-complete OIDC server.
MFA assertions are simulated, not an enrolled independent second factor. It uses
generated profile tokens, signed state/nonce/callback/audience, private PKCE
verifiers, expiry and durable one-use callback markers. It refuses
STAGING/PRODUCTION. Real deployments require an approved real IdP.

## Local HTTPS trust: explicit human security gate

Caddy creates a local CA in `caddy_data`. Browser trust installation changes the
current Windows user's certificate trust and requires an explicit human action.
Do not bypass certificate errors. Export and inspect:

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
docker compose cp gateway:/data/caddy/pki/authorities/local/root.crt .local/caddy-root.crt
$orqetiaCa = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new((Resolve-Path .local/caddy-root.crt).Path)
$orqetiaCa | Format-List Subject,Issuer,Thumbprint,NotAfter
```

After deciding to trust this installation's CA:

```powershell
Import-Certificate -FilePath (Resolve-Path .local/caddy-root.crt).Path -CertStoreLocation Cert:\CurrentUser\Root
Get-Item -LiteralPath "Cert:\CurrentUser\Root\$($orqetiaCa.Thumbprint)"
```

This trusts certificates signed by this CA for the current user, not all Windows
users. Its private key remains in the Docker volume. Restart the browser if
needed and verify HTTPS opens without an interstitial. Undo this exact trust:

```powershell
Remove-Item -LiteralPath "Cert:\CurrentUser\Root\$($orqetiaCa.Thumbprint)"
```

Record the fingerprint before deleting `caddy_data`: a replacement volume creates
a different CA. Automated HTTP acceptance trusts the exported CA only in its own
Python TLS context; it neither changes Windows trust nor disables verification.

## Incremental GitHub-to-Docker synchronization

The Windows quick start above is for first installation. For updates, verify
the exact approved remote commit and the local working tree **before** modifying
a running stack. Never discard an unknown local change or reset a working copy.

```powershell
Set-Location C:\\IA-PROJETOS\\github\\orqetia
git status --short
git fetch origin main
git rev-parse origin/main
git merge --ff-only origin/main
docker compose config --quiet
```

Stop if `git status --short` reports unexpected changes or if the fetch,
fast-forward or Compose validation fails. Verify that `origin/main` matches
the reviewed commit. For **documentation-only** revisions, do not rebuild,
recreate, migrate or bootstrap containers; use read-only health checks.

For code/image changes, first review the Git diff, `compose.yaml`, affected
services, migration files and environment/secret changes. Record a safe backup
point before any schema migration. The runtime applications share the
`orqetia-local-runtime:dev` image: one targeted image build can affect several
services, so Docker Compose may need to recreate every consumer of that image.

```powershell
# Only when application/runtime content changed:
docker compose build api
# Only if reviewed schema changes require it and compatibility is verified:
# docker compose run --rm migrate
docker compose up --no-build -d --wait --wait-timeout 120
docker compose ps -a
```

Confirm `migrate` and `bootstrap` one-shot statuses when those services were
recreated; do not blindly delete/reseed their state. Validate only changed
surfaces plus basic readiness/HTTPS/login. Preserve named volumes, local CA,
identities and credential copies. Never run `down -v`, `git reset --hard`,
a force-push or automatic credential regeneration as an update shortcut.

## Daily operation and recovery

```powershell
docker compose ps -a
docker compose logs --tail 100 api worker scheduler backoffice portal
docker compose restart api worker scheduler
docker compose up -d --wait --wait-timeout 120
docker compose stop
# Remove containers/network while retaining all named volumes:
docker compose down
docker compose up -d --wait --wait-timeout 120
# Explicit idempotent seed:
docker compose run --rm bootstrap
```

Normal rebootstrap preserves identities, memberships and initial credential
material. Rotate API credentials through Backoffice or canonical
`POST /v1/credentials/{credential_id}/rotate`, capturing its one-time response.
Use the new value instead of an old bootstrap copy. Revocation is explicit.
Do not delete individual private files as a generic recovery procedure.

For local login/signing-key rotation, stop the stack and take a protected backup
of `local_data` first. Replacing these files invalidates login transactions.
There is no general automatic rotation command in this delivery. A disposable
development reset regenerates all installation credentials:

```powershell
# DESTRUCTIVE: deletes local DB, credentials, sessions and CA.
# Export needed data and record/remove old CA trust BEFORE running this.
docker compose down -v
docker compose up --build -d --wait --wait-timeout 120
```

## Reproducible acceptance

With the locked development Python environment:

```powershell
uv sync --locked --python 3.14
uv run --no-sync --python 3.14 python scripts/accept_local.py --with-recovery
```

This creates synthetic tasks/credentials and tests real HTTPS, AUTO escalation,
fixed EXPLICIT_TARGET, idempotency, quota, owner isolation, observed estimates,
login/RBAC/CSRF, worker restart and persistent down/up. It temporarily stops the
stack: run when interactive work can pause. Redacted evidence is written to
ignored `.local/acceptance.json`. Raw tokens are never printed. The simulator
has no upstream HTTP exchanges, so its authorized exchanges collection is empty.
Internal costs are visible only to Backoffice. GLOBAL_PUBLIC estimates remain
unavailable without an actual eligible cohort.

## Troubleshooting

- TLS error: complete the explicit CA trust gate; never bypass verification.
- Unhealthy service: inspect `docker compose ps -a` and bounded service logs.
  Migration/bootstrap must be exited 0, not healthy long-running processes.
- Missing credentials: check retained `local_data` and bootstrap exit status.
  Never inject real provider credentials into this environment.
- Port conflict: stop the conflicting program or update loopback bindings and
  canonical local HTTPS origin together.
- No task progress: inspect worker, quota and attempts; do not bypass canonical
  PostgreSQL messaging or start another engine.
- Exit 137: inspect State.OOMKilled before requesting more Docker memory.
- Run PostgreSQL integration tests in Linux containers against a separate test
  DB; native Windows psycopg requires a compatible asynchronous event loop.

Browser visual acceptance was completed on 2026-10-08 after the local CA was
explicitly trusted on that Windows installation. On a new host/user profile,
CA trust and browser acceptance must be verified again; passing HTTP acceptance
alone never establishes browser trust.

## Browser form authentication

The IdP, Backoffice and Portal send `Referrer-Policy: strict-origin`. This
preserves the browser Origin on same-origin form POSTs while excluding paths
and query strings from Referer, including OIDC callback codes. No referrer is
sent on HTTPS-to-HTTP downgrade. The gateway preserves each application's
policy; the machine API retains `no-referrer`.

Do not use `no-referrer` on these form pages: Chromium submits `Origin: null`
for non-CORS form POSTs, which the strict origin guard correctly rejects.
Do not fix this by allowing a null Origin or disabling CSRF/browser protection.

Browser qualification on 2026-10-08 confirmed Backoffice ADMIN and Portal
VIEWER, DEVELOPER (Operator), and OWNER (Admin). The Operator created a session
and completed a synthetic task; Admin inspected credentials, usage and activity,
submitted a provider-free estimate, and received Not found for a Tenant B session.
The prior HTTP recovery/persistence acceptance remains complementary evidence.
Local MFA is simulated; this is not production identity-provider qualification.
