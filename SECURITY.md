# Security

Status: **DEVELOPMENT**.

## Scope

Security requirements in this repository apply to ORQETIA only. No changes are authorized in the RASAi repository.

## Minimum baseline

ORQETIA must design for HTTPS/TLS, JWT validation, explicit scopes, tenant isolation, secure web sessions, CSRF/XSS/injection defenses, secret redaction, credential rotation/revocation, encrypted provider secrets, audit logging, replay/idempotency controls, task ownership, cancellation authorization, SSRF controls, outbound restrictions where appropriate and cross-client data-leak prevention.

## Credentials

Access credentials, provider credentials and LLM usage tokens are distinct.

Secrets must not be logged, traced, embedded in container images or re-displayed after initial creation when avoidable.

Backoffice may report by safe credential ID/fingerprint, never by secret.

## Financial data

Internal provider-cost data is restricted to authorized Backoffice users in the current phase.

Client-facing APIs and reports must not expose monetary provider cost, currency, unit price or future client charge.

## Reporting vulnerabilities

Until a public disclosure process is explicitly approved, do not open a public issue containing exploitable security details. Report such matters privately to the repository owner.
