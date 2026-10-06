# Security

Status: **DEVELOPMENT**.

## Scope

Security requirements in this repository apply to ORQETIA only. No changes are authorized in the RASAi repository.

## Minimum baseline

ORQETIA must design for HTTPS/TLS, JWT validation, explicit scopes, tenant isolation, secure web sessions, CSRF/XSS/injection defenses, secret redaction, credential rotation/revocation, encrypted provider secrets, audit logging, replay/idempotency controls, task ownership, cancellation authorization, SSRF controls, outbound restrictions where appropriate and cross-client data-leak prevention.

Engineering controls are documented in docs/security/security-baseline.md.
Incident response and vulnerability handling are documented in
docs/security/incident-response.md and docs/runbooks/security-incident.md.

## Credentials

Access credentials, provider credentials and LLM usage tokens are distinct.

Secrets must not be logged, traced, embedded in container images or re-displayed after initial creation when avoidable.

Backoffice may report by safe credential ID/fingerprint, never by secret.

## Financial data

Internal provider-cost data is restricted to authorized Backoffice users in the current phase.

Client-facing APIs and reports must not expose monetary provider cost, currency, unit price or future client charge.

## Reporting vulnerabilities

Do **not** open a public issue containing exploitable details, credentials, tokens,
personal data, proof-of-concept payloads or reproduction steps that would increase
risk before remediation.

Use a private vulnerability-reporting/security-advisory channel offered by the
repository when available. If the repository UI does not expose such a channel,
contact the repository owner/maintainers through a private channel and disclose
only the minimum information needed to establish contact before transferring
sensitive details.

A report should include, when safe:
- affected component/version or commit;
- impact and exploit preconditions;
- whether exploitation is known or theoretical;
- minimal non-secret reproduction information;
- suggested mitigation if known;
- reporter contact for coordinated follow-up.

Maintainers follow docs/security/incident-response.md. Security fixes may use a
private patch/advisory path before public disclosure. Public disclosure must not
precede containment/remediation when doing so would materially increase risk.

## Security contact readiness

Before any RC, maintainers must verify that at least one private intake path is
actually usable by an external reporter and record the responsible security
owner/on-call role. A personal email address must not be invented or committed
solely to satisfy this requirement.
