# ORQETIA Security Baseline

- Issue: #53
- Product state: DEVELOPMENT
- Verification baseline: OWASP ASVS 5.0.0 Level 2, plus selected stronger controls for Backoffice, secrets, provider boundaries and other high-risk operations.
- Awareness/risk references: OWASP Top 10:2025; OWASP API Security Top 10:2023.
- Identity references: RFC 9700 / OAuth 2.0 Security BCP; NIST SP 800-63-4 family.
- This baseline is an engineering gate, not a claim of external ASVS certification.

## Policy

A change that introduces a known, relevant exploitable vulnerability is blocked until:
- it is corrected; or
- a time-bounded explicit risk acceptance is recorded with owner, impact, mitigation and expiry.

Security controls are applied server-side. Client UI behavior, obscure URLs or unguessable identifiers are never security boundaries.

## Verification controls

### SEC-001 — Authorization / tenant isolation

References:
- ASVS V8 Authorization;
- OWASP Top 10:2025 A01 Broken Access Control;
- API1 BOLA, API3 BOPLA, API5 BFLA.

Requirements:
- default deny;
- server-side function/object/property authorization;
- tenant/client ownership verification on every protected resource;
- no unrestricted repository path in client-facing code;
- RLS defense in depth per #21 where applicable;
- negative cross-tenant tests;
- exports/filters cannot bypass authorization;
- privileged Backoffice reads/actions audited.

### SEC-002 — Authentication, federation and session integrity

References:
- ASVS V6 Authentication and V7 Session Management;
- OWASP A07 Authentication Failures;
- API2 Broken Authentication;
- RFC 9700;
- NIST SP 800-63-4/63B-4.

Requirements:
- standardized OIDC/OAuth flows, not home-grown bearer/session protocol;
- issuer/audience/signature/time/nonce/state validation as applicable;
- short-lived tokens/sessions with revocation strategy;
- secure server-side browser session preferred;
- MFA/step-up per #54;
- no token/session secret in localStorage baseline;
- session fixation/replay/logout/recovery tests.

### SEC-003 — Injection, encoding and input validation

References:
- ASVS V1 Encoding and Sanitization and applicable validation controls;
- OWASP A05 Injection.

Requirements:
- schema/type validation at trust boundary;
- parameterized SQL/ORM binding;
- context-appropriate output escaping;
- no shell construction with untrusted input;
- explicit allowed fields to prevent mass assignment;
- bounded payload/schema complexity.

### SEC-004 — Cryptography and secret handling

References:
- OWASP A04 Cryptographic Failures;
- #56.

Requirements:
- modern TLS for public/provider traffic;
- secrets never committed/logged/traced;
- provider/client secrets stored using #56 boundary;
- no custom cryptographic primitive;
- key/version/rotation/revocation/audit;
- encrypted backups where applicable;
- safe fingerprints/IDs in reports instead of secret material.

### SEC-005 — SSRF and unsafe external API consumption

References:
- API7 SSRF;
- API10 Unsafe Consumption of APIs.

Requirements:
- clients cannot set arbitrary provider base URL;
- approved endpoint/host boundary;
- strict TLS certificate/hostname verification;
- redirect behavior reviewed;
- response schema/size/timeouts bounded;
- provider response treated as untrusted input;
- no provider error/body blindly rendered to users/logs.

### SEC-006 — Resource consumption and abuse

References:
- API4 Unrestricted Resource Consumption;
- API6 Unrestricted Access to Sensitive Business Flows.

Requirements:
- request/body limits;
- concurrency/rate/backpressure controls;
- bounded retries/cycles/timeouts;
- quota enforcement before costly effects where required;
- pagination/export limits;
- protection against expensive estimate/report queries.

### SEC-007 — Secure configuration and exposed surface

References:
- OWASP A02 Security Misconfiguration;
- API8 Security Misconfiguration.

Requirements:
- production debug disabled;
- no public DB/queue;
- strict CORS allowlist when cross-origin access exists;
- secure headers/CSP appropriate to UI;
- trusted proxy configuration;
- least-privilege service/database roles;
- no default admin credential;
- environment-specific secure defaults.

### SEC-008 — Software supply chain and integrity

References:
- OWASP A03 Software Supply Chain Failures;
- A08 Software/Data Integrity Failures.

Requirements:
- lockfile/reproducible install;
- dependency vulnerability review;
- minimal dependencies;
- GitHub Actions pinned to immutable full commit SHA;
- workflow permissions explicit and minimal;
- no untrusted pull-request code executed with write/secrets context;
- release/SBOM/provenance hardening before RC under M9.

### SEC-009 — Security logging and alerting

References:
- OWASP A09 Security Logging and Alerting Failures.

Requirements:
- auth failures, privileged changes, secret lifecycle and high-value security events audit safely;
- correlation IDs;
- no SECRET/raw token in logs;
- log integrity/retention proportional to classification;
- actionable alerting finalized before RC.

### SEC-010 — Exceptional-condition handling

References:
- OWASP A10 Mishandling of Exceptional Conditions.

Requirements:
- fail closed on auth/authorization/config ambiguity;
- client errors use safe envelopes;
- no stack trace/internal secret to client;
- transaction rollback/recovery explicit;
- unknown provider response does not become success;
- crash/retry does not duplicate provider call/accounting silently.

### SEC-011 — Multi-tenant confidentiality

Requirements:
- ownership dimensions typed/indexed;
- authorization and RLS negative tests;
- cache key includes ownership/security scope;
- no raw cross-tenant benchmark material;
- background worker restores correct tenant context;
- read-model staleness cannot grant mutation authorization.

### SEC-012 — Sensitive-data minimization and redaction

Requirements:
- prompts/results not logged by default;
- privacy/classification rules from #55/#61;
- no credential values in issues/PRs/tests/analytics;
- sanitized support/diagnostic outputs;
- test fixtures synthetic.

### SEC-013 — Repository/CI baseline

Automated zero-dependency checks:
- reject likely committed secret material/private keys;
- reject committed .env files other than safe templates;
- reject unpinned external GitHub Actions;
- reject pull_request_target baseline usage;
- require explicit workflow permissions;
- run canonical characterization tests.

Dependency/static/runtime security checks are added to the scaffold once pyproject/lockfile exist.

## PR security review

Each relevant PR declares:
- affected SEC controls;
- AuthN/AuthZ/tenant impact;
- data classification/privacy impact;
- outbound/provider impact;
- migration/configuration impact;
- targeted negative tests;
- residual risk.

"N/A" must be reasoned for changes touching a trust boundary.

## Secure SDLC gate by change type

### API endpoint
Must review SEC-001/002/003/006/007/010/011/012.

### Database/repository
Must review SEC-001/003/004/010/011/012.

### Provider adapter/outbound HTTP
Must review SEC-003/004/005/006/010/012.

### Auth/session/credential
Must review SEC-001/002/004/007/009/010/011/012.

### CI/dependency/deployment
Must review SEC-004/007/008/009/012/013.

## Risk acceptance

A security risk acceptance is not an ordinary TODO.

It records:
- vulnerability/control;
- exploit preconditions;
- affected data/tenant surface;
- severity/rationale;
- compensating controls;
- owner;
- expiry/review date;
- remediation issue.

No permanent blanket acceptance for exposed known vulnerabilities.

## Evidence

Verification evidence may include:
- unit/contract/security negative tests;
- integration tests;
- static/dependency/secret scans;
- configuration tests;
- threat-model abuse cases;
- restore/runbook tests;
- targeted manual security review for high-risk boundary changes.

Before first RC, M9 must demonstrate the applicable baseline end-to-end.
