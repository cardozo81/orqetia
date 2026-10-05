# Runtime configuration boundary

Issue: #100  
Security: SEC-004 / SEC-007 / SEC-012

ORQETIA composition roots load typed settings through `orqetia.settings.RuntimeSettings`.
Domain/bounded-context code does not read environment variables directly.

## Environment variables

All runtime variables use the `ORQETIA_` prefix. Complex values such as CORS
origins and trusted proxy CIDRs are JSON arrays when supplied through environment
variables.

`.env.example` is synthetic documentation only. The application does not
automatically load a repository `.env` file.

## Secret boundary

General settings may contain:

- a database DSN, represented as `SecretStr` and redacted from safe diagnostics;
- opaque secret-store references;
- opaque root-key references.

General settings never contain provider API credentials or recoverable provider
secret values. Those are resolved later through the #56 `SecretStorePort`
boundary by authorized infrastructure/provider execution code.

`secret_store_ref` and `root_kek_ref` are identifiers such as
`vault://orqetia/provider-secrets`; they are not secret values.

## Fail-closed production rules

Production rejects:

- debug mode;
- HTTP public API base URL;
- missing public base URL for the API process;
- wildcard CORS;
- non-HTTPS CORS origins;
- globally trusted proxy CIDRs;
- local secret-store mode;
- malformed/non-PostgreSQL database DSN;
- managed/envelope secret modes without their required references.

The settings object does not silently downgrade an invalid production
configuration to local/test behavior.

## Logging

Use `safe_summary()` for startup diagnostics. Do not serialize the raw settings
model into logs, traces, events, support bundles, or API responses.

## Process roles

The same typed contract supports `api`, `worker`, and `scheduler`.
Role-specific behavior is composed by the owning application entry point; this
module does not implement API/database/queue business logic.
