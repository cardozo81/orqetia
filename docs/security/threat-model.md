# ORQETIA Threat Model

- Issue: #13
- Status: baseline accepted for DEVELOPMENT
- Security baseline: #53 / SEC-001..SEC-013
- Architecture: ADR-0002..ADR-0008

## Security objectives

1. A principal can access only functions, resources and fields explicitly authorized.
2. Tenant/client isolation survives application mistakes through layered controls.
3. Provider/client secrets are never exposed to clients, logs, reports or unrelated runtime components.
4. A retry/crash/replay cannot silently duplicate paid provider side effects, usage, cost or quota.
5. External provider responses and endpoints are treated as untrusted.
6. Operational/admin capabilities have stronger authentication, audit and data-access boundaries.
7. Client-facing APIs never expose internal financial/provider-account information in the baseline.
8. Security failure defaults closed and produces safe diagnostics.

## Primary assets

### SECRET
- provider API credentials;
- client credential secret material;
- signing/encryption/private keys;
- bootstrap/recovery secrets;
- session/token secrets.

### Restricted/confidential
- provider contracts/credits/costs/pricing;
- provider account/credential safe identifiers;
- cross-client operational intelligence;
- security incident evidence.

### Client-private
- tenant/client metadata;
- prompts/input/context;
- outputs/results;
- sessions/tasks/attempts;
- technical usage;
- customer credential metadata.

### Integrity-critical
- execution policy version;
- provider permissions;
- quota policy and ledger;
- pricing catalog/version;
- task state;
- usage/accounting facts;
- audit events;
- outbox/inbox/idempotency records.

## Threat actors

- anonymous Internet attacker;
- authenticated but malicious tenant user;
- authenticated but malicious/compromised service client;
- attacker holding a stolen client credential/session;
- compromised Backoffice account;
- malicious or compromised provider/API endpoint;
- compromised dependency/build/action;
- malicious insider with legitimate operational access;
- opportunistic bot performing credential stuffing, scraping or resource exhaustion;
- accidental operator/developer error.

## Trust boundaries

### TB-01 Internet → public API/reverse proxy
Untrusted:
- headers;
- IP/forwarding metadata unless from trusted proxy;
- JSON/body/schema;
- identifiers;
- idempotency keys;
- auth material.

Controls:
SEC-001/002/003/006/007/010.

### TB-02 Browser → BFF/session boundary
Untrusted browser state must not become authority.

Controls:
OIDC Authorization Code + PKCE, state/nonce, HttpOnly/Secure/SameSite session, CSRF defense, step-up for sensitive actions.

### TB-03 API/worker → PostgreSQL
Application identity and tenant scope are trusted only after server-side resolution.

Controls:
parameterized access, least-privilege DB roles, RLS defense in depth, scoped repositories, short transactions.

### TB-04 API/worker → queue/scheduler
Messages are authenticated/internal but still validated and idempotent.

Controls:
event/command schema, tenant/client dimensions, event ID, inbox/outbox, bounded payload, no secret/raw large payload.

### TB-05 worker/provider adapter → external provider
Provider boundary is hostile/unreliable.

Controls:
approved endpoint, TLS, timeout, response size/schema validation, error normalization, no arbitrary redirects/URL, secret isolation.

### TB-06 Backoffice → administrative/control plane
Highest privilege application boundary.

Controls:
strong MFA, step-up, least privilege, property-level authorization, dedicated repositories/DB access path, audit, no clear secret re-display.

### TB-07 Secret storage → runtime adapter
Only the minimal runtime component obtains usable provider secret material.

Controls:
reference-based retrieval, short exposure lifetime, memory/log redaction, role separation, rotation/version/audit.

### TB-08 authoritative facts → read models/analytics
Projection data may be stale or denormalized.

Controls:
never authorize mutations from projection alone; classification preserved; cross-tenant aggregates cohort-safe; watermark/as_of.

### TB-09 source/dependencies → CI/build/deploy artifact
Third-party code can compromise runtime even without application bug.

Controls:
lockfile, pinned Actions, dependency scan, minimal permissions, reproducible build, no untrusted PR secrets/write context.

## Threats and required control families

### Identity / authorization

T-001 BOLA: user changes resource ID to another tenant.
- Controls: SEC-001, #21 ownership, RLS.
- Evidence: negative cross-tenant object tests.

T-002 BFLA: customer calls Backoffice/admin endpoint.
- Controls: server-side action/role policy; route separation; default deny.
- Evidence: function-level negative tests.

T-003 BOPLA/mass assignment: client submits protected policy/quota/provider/financial fields.
- Controls: explicit request schemas; separate admin/client DTOs; property authorization.
- Evidence: unexpected/protected fields rejected/ignored safely.

T-004 stale privilege/self-contained token continues after revocation.
- Controls: short lifetime; high-risk revocation/step-up strategy; authorization recheck where required.
- Evidence: revoke/suspend tests.

T-005 tenant context injection through header/body/query.
- Controls: scope derived from authenticated server state; mismatch rejected.
- Evidence: spoofed tenant/client input tests.

### Authentication/session

T-006 account takeover via stuffing/spraying/recovery.
- Controls: IdP protections, MFA, rate limiting, no account enumeration, safe recovery.
- Evidence: #54 tests.

T-007 session theft/fixation/replay.
- Controls: rotated server-side session, secure cookies, nonce/state, revocation/logout.
- Evidence: fixation/replay/logout tests.

T-008 OAuth/OIDC mix-up/token substitution.
- Controls: exact issuer/audience/client/redirect validation, PKCE, state/nonce, token type/claim verification.
- Evidence: negative federation tests.

### Input/API/web

T-009 SQL/command/template/header injection.
- Controls: typed validation, parameterized queries, context encoding, no shell interpolation.
- Evidence: malicious input tests/static review.

T-010 CSRF on browser mutation.
- Controls: SameSite + anti-CSRF/origin strategy, no bearer-in-localStorage baseline.
- Evidence: missing/invalid CSRF/origin tests.

T-011 XSS/open redirect/CORS abuse.
- Controls: safe UI rendering, allowlisted redirects/origins, CSP/headers.
- Evidence: client portal tests when UI exists.

T-012 resource exhaustion by payload, concurrency, pagination, retry or expensive reporting.
- Controls: size/depth limits, quotas/rate limits/backpressure/timeouts/pagination.
- Evidence: limit tests/load profile.

### Data / tenant isolation

T-013 cross-tenant DB/query/cache leak.
- Controls: owner-scoped repositories, RLS, scoped cache key, negative tests.
- Evidence: multi-tenant synthetic suite.

T-014 secret/PII leak in log/trace/error/export.
- Controls: classification/redaction/minimization; safe error envelope.
- Evidence: log/export redaction tests.

T-015 stale read model grants mutation or reveals forbidden field.
- Controls: mutation revalidates owner; property-filtered projections; read model classification.
- Evidence: stale projection authorization test.

T-016 benchmark inference/reidentification.
- Controls: #11 cohort minimum/aggregation; no raw cross-tenant content; filter restrictions.
- Evidence: privacy inference tests.

T-017 backup/export exposure.
- Controls: encryption, least privilege, audit, retention, secure deletion.
- Evidence: M9 restore/export review.

### Provider/outbound boundary

T-018 SSRF via provider endpoint/model/config.
- Controls: endpoint is administrative allowlisted config, never arbitrary client URL; TLS/hostname validation.
- Evidence: malformed/private/link-local URL rejection tests.

T-019 provider response injection/oversize/malformed payload.
- Controls: response size/time/schema validation; no blind log/render; fail closed.
- Evidence: fake provider adversarial contract tests.

T-020 credential exfiltration via redirect/error/telemetry.
- Controls: redirect policy, header scoping, redaction, secret boundary.
- Evidence: mock HTTP captures host/header behavior.

T-021 malicious tool/agent execution.
- Baseline: tools/agentic capabilities require explicit capability/policy boundary; no unrestricted local/network execution.
- Evidence: future tool capability threat model before enablement.

### Async/reliability/integrity

T-022 duplicate broker delivery duplicates provider call/cost/quota.
- Controls: #14/#51 idempotency, inbox/outbox, attempt identity/lease.
- Evidence: replay/crash concurrency tests.

T-023 worker crash loses task or leaves ambiguous paid side effect.
- Controls: durable task/attempt state, lease/recovery, reconciliation.
- Evidence: kill/restart tests with fake provider.

T-024 forged/stale internal event mutates wrong tenant.
- Controls: schema/version/ownership validation, internal auth/network boundary, event ID/inbox.
- Evidence: invalid tenant/version/replay tests.

T-025 race in cancel/state transition produces invalid state/accounting.
- Controls: state machine + version/row lock + terminal invariants.
- Evidence: concurrency transition tests.

### Supply chain/operations

T-026 malicious/vulnerable dependency/action.
- Controls: SEC-008/013; lockfile, pin, audit, least workflow permissions.
- Evidence: CI.

T-027 leaked repository/deployment secret.
- Controls: repository secret check, secret manager, rotation/incident workflow.
- Evidence: CI + #57 runbook.

T-028 insecure debug/admin exposure or proxy trust.
- Controls: deployment config tests, private data plane, trusted proxy.
- Evidence: environment/security configuration tests.

T-029 unauthorized admin/support action.
- Controls: strong MFA, fine-grained roles, step-up, audit, purpose-restricted access.
- Evidence: Backoffice authorization matrix tests.

T-030 integrity corruption in migration/backfill/reconciliation.
- Controls: controlled migrations, idempotent backfill, checksums/count reconciliation, backup/restore.
- Evidence: migration/rebuild tests.

## High-risk assumptions

- External IdP is trusted only after protocol validation; it does not authorize ORQETIA resources by itself.
- Provider APIs are not trusted data sources for authorization.
- PostgreSQL/queue private networking reduces exposure but does not remove need for credentials/TLS/least privilege.
- A Backoffice user is not inherently authorized to all secrets/financial/tenant-private content.
- Successful HTTP provider response is not sufficient proof of logical success; schema/outcome normalization remains required.

## Security test ownership

- #12/#54: T-001..T-008 and T-029 auth/session aspects.
- #56/#59: T-014/T-017/T-020/T-027.
- #55/#61: T-014/T-016/T-017.
- #14/#51/#15: T-022..T-025.
- #9/#10/#8: T-018..T-021.
- #22/#23/#4: T-001..T-012 client/admin surface.
- #57/M9: incident, restore, disclosure and operational response.

## Change rule

Any new trust boundary, external integration, credential type, tool execution mode, public endpoint class or cross-tenant analytical feature must update this threat model or document why existing threats fully cover it.
