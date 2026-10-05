# Phase 1 health, security and quality gate

Issue: #106  
Security controls: SEC-007 / SEC-008 / SEC-009 / SEC-013

## API health

Operational health endpoints are outside the public product contract:

- `GET /health/live` — process/event-loop liveness only;
- `GET /health/ready` — typed configuration is already valid and PostgreSQL
  responds to a bounded local probe.

They are excluded from the canonical `/v1` OpenAPI document.

Health/readiness never calls an AI provider, provider account, provider balance,
pricing endpoint, or any paid external service.

## Worker/scheduler health

Container/process supervision remains the primary liveness signal.

Worker and scheduler also emit periodic structured `process.heartbeat` records
containing only safe operational metadata. Work payloads and secrets are not
included.

## HTTP hardening

The API shell applies:

- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`;
- `Referrer-Policy: no-referrer`;
- `Cache-Control: no-store`;
- restrictive API CSP;
- HSTS only when composed as production HTTPS;
- CORS only from the typed allowlist, without credentialed cross-origin cookies.

Uvicorn remains configured with `--no-proxy-headers` in the baseline. Merely
setting a trusted-proxy CIDR does not make arbitrary client forwarding headers
authoritative. A future deployment adapter must explicitly enable/validate proxy
handling.

## Quality toolchain

The `Phase 1 quality gate` workflow uses the committed lock and Python 3.14 to
run:

1. `uv lock --check`;
2. locked development environment sync;
3. PostgreSQL 18 + `alembic upgrade head`;
4. Ruff;
5. mypy strict package checking;
6. full pytest suite against the migrated PostgreSQL integration database;
7. pip-audit.

The gate uses only synthetic/local test configuration and no provider
credentials.

## PostgreSQL integration harness

The quality job is the aggregate integration harness:

- PostgreSQL major 18;
- one ephemeral database;
- repository migration graph applied once before the full suite;
- the same `ORQETIA_DATABASE_DSN` consumed by persistence/messaging tests;
- no SQLite parity claim.

Specialized persistence/messaging workflows remain useful targeted diagnostics,
while the aggregate quality gate detects cross-suite regressions.

## Merge rule

Phase 1 is not complete while any of the above checks are red on the candidate
head. Relevant dependency vulnerabilities block merge according to #53 unless
there is an explicit time-bounded risk acceptance.
