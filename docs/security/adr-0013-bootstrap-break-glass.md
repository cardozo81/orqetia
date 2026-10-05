# ADR-0013 — First-admin bootstrap and break-glass recovery

- Status: **Accepted**
- Issue: #58
- Depends on: #54, #56
- Security controls: SEC-001, SEC-002, SEC-004, SEC-007, SEC-009, SEC-010, SEC-012

## Decision

ORQETIA has no permanent master password, universal API key or hidden super-admin credential.

Initial Backoffice bootstrap and emergency recovery are explicit, short-lived, one-time workflows anchored to:
- the external OIDC identity boundary;
- strong MFA;
- #56 secret handling;
- durable audit evidence.

## Bootstrap invariant

The normal public application starts with **zero Backoffice administrators**.

Administrative authority is created only through a one-time bootstrap grant.

Bootstrap is available only when:
1. no active Backoffice administrator exists; and
2. a valid, unconsumed bootstrap grant exists; and
3. the authenticating OIDC subject satisfies the grant binding; and
4. required MFA/step-up strength is present.

After the first administrator is successfully created, ordinary bootstrap redemption is permanently disabled unless an explicit emergency recovery workflow creates a new recovery grant.

## Bootstrap grant creation

A deployment operator with infrastructure-level authority creates the grant through a local/administrative CLI or equivalent controlled deployment operation, not through an unauthenticated public web endpoint.

Conceptual operation:

    orqetia-admin bootstrap create \
      --issuer <expected-issuer> \
      --subject <expected-oidc-subject> \
      --expires-in 15m

If exact OIDC subject cannot be known before first login, an explicitly approved alternative binding may use a verified IdP account identifier, but subject binding is preferred.

The command:
- generates a cryptographically random high-entropy token;
- stores only a verifier/hash plus binding/expiry/status;
- displays the clear token exactly once to the deployment operator;
- never logs it;
- records a bootstrap-grant-created audit event using safe metadata.

Baseline TTL target: 15 minutes. Deployment may shorten it.

A materially longer TTL requires explicit security review.

## Bootstrap token transport

The clear token:
- is delivered out-of-band to the intended administrator;
- is never placed in a URL/query string;
- is never emailed by ORQETIA in plaintext;
- is never persisted in browser storage beyond the active redemption interaction;
- is never placed in issues, PRs, deployment logs or analytics.

## Bootstrap redemption

Flow:
1. user authenticates through the configured OIDC IdP;
2. ORQETIA validates the human session per ADR-0010;
3. required MFA strength is verified;
4. user submits the one-time bootstrap token through the authenticated protected flow;
5. server verifies token using constant-time comparison/verifier;
6. issuer + subject binding is verified;
7. server acquires a transactional bootstrap lock/state;
8. server re-checks that no active Backoffice admin exists and grant is unused/unexpired;
9. first Backoffice administrator membership/role is created;
10. grant is atomically marked CONSUMED;
11. session/security version is rotated;
12. privileged audit event is committed.

Concurrent redemption attempts result in exactly one success.

## Bootstrap state

Suggested grant states:
- ACTIVE;
- CONSUMED;
- EXPIRED;
- REVOKED.

Store:
- grant ID;
- verifier;
- issuer;
- intended subject/binding;
- created/expires/consumed timestamps;
- created_by infrastructure operator identity/reference when available;
- safe creation/revocation metadata.

Do not store clear token.

## Public endpoint exposure

A bootstrap redemption route may exist only as an authenticated flow and must fail closed when the bootstrap preconditions are not satisfied.

It must not disclose:
- whether a particular OIDC subject is expected;
- token verifier;
- administrator details.

Once bootstrap is complete, repeated requests receive a generic unavailable/forbidden response.

No endpoint such as /admin/default-login or hidden query parameter bypass exists.

## Recovery scenarios

### ORQETIA role/session lockout

If all Backoffice administrators are suspended/locked out but the IdP is healthy:

A deployment operator uses a privileged local recovery operation to create a short-lived, one-time **recovery elevation grant** bound to a specific existing OIDC subject.

The recipient:
- authenticates via OIDC;
- satisfies strong MFA;
- redeems the grant;
- receives narrowly scoped temporary recovery authority or a controlled restoration action.

Prefer repairing normal administrative membership over creating a standing emergency account.

### Lost/revoked MFA

Recovery occurs through the IdP's secure recovery process plus ORQETIA's step-up/session rules.

ORQETIA does not bypass the IdP by accepting security questions or a local fallback password.

### IdP outage

ORQETIA does not silently become a second identity provider.

If the configured IdP is unavailable:
- ordinary Backoffice web administration remains unavailable;
- infrastructure operators may execute only documented offline/local recovery needed to restore identity configuration/service;
- no general-purpose unauthenticated application admin shell is exposed.

A later deployment may maintain a separately controlled IdP emergency account with phishing-resistant MFA, but it is an IdP/operations control, not an ORQETIA master password.

### IdP configuration corruption

A local infrastructure recovery command may repair trusted issuer/client configuration under strict operational authentication and audit.

This path is limited to recovery configuration and cannot become an unrestricted client/provider/data administration API.

## Break-glass model

Baseline uses **just-in-time break-glass elevation**, not a permanent enabled account.

Characteristics:
- disabled/nonexistent until an operator invokes recovery;
- bound to one identity;
- short TTL;
- strong MFA required;
- narrow purpose/scope;
- automatic expiry;
- immediate alert/audit;
- existing privileged sessions may be revoked depending on incident;
- grant/token revoked after use.

Suggested recovery-elevation TTL target: <= 30 minutes.

The elevated session must not survive beyond the grant/policy's absolute recovery window without returning to normal role state.

## Separation of duties

Infrastructure recovery authority is separate from ordinary Backoffice application authority.

A deployment operator who can issue a recovery grant does not automatically receive:
- provider secret read access;
- finance reports;
- tenant data;
- client impersonation.

The recovered human principal still passes application authorization for the specifically granted recovery operation.

## Two-person control

When the deployment has multiple trusted operators, high-risk break-glass creation should support two-person approval.

The baseline must not make availability depend on two people for a single-owner small deployment, but it records:
- who initiated;
- who approved, if applicable;
- reason/ticket;
- target identity;
- expiration.

A future enterprise policy may require dual control.

## Audit and alerting

Always record safe events for:
- bootstrap grant creation/revocation/expiry;
- bootstrap redemption success/failure category;
- first admin creation;
- recovery grant creation/revocation/expiry;
- break-glass redemption/elevation;
- emergency role/config repair;
- break-glass end/automatic expiry;
- subject/session revocation after recovery.

Do not store token value.

Break-glass creation/use triggers an immediate security/operations notification through the available alert channel once operational observability exists.

## Recovery evidence

Recovery operations include:
- reason;
- initiating infrastructure identity;
- intended human subject;
- timestamp;
- grant ID;
- affected administrative membership/config;
- correlation ID;
- result.

If the central audit path is unavailable during a true outage, emergency tooling writes a protected local recovery record that is ingested/reconciled into the authoritative audit log after service restoration.

## Post-recovery actions

After successful break-glass:
1. validate the repaired admin/identity state;
2. revoke emergency grant;
3. revoke/rotate potentially compromised sessions/credentials as appropriate;
4. restore normal least-privilege roles;
5. review audit trail;
6. record root cause/remediation;
7. rotate bootstrap/recovery secrets if exposure is possible.

A recovery grant is never converted into a permanent credential.

## Rate limiting / abuse

Bootstrap/recovery redemption:
- bounded attempts;
- account/token enumeration resistant errors;
- rate limit/backoff;
- no provider/network side effect;
- security alert after suspicious failures.

Because the token is high entropy, rate limiting is defense in depth, not a replacement for entropy/expiry/binding.

## Database/consistency

Grant consumption and administrator creation/restoration are transactionally protected.

Use:
- unique/invariant preventing multiple first-admin creation races;
- row lock/advisory lock or equivalent targeted coordination;
- idempotent recovery operation ID.

Crash after commit must not make the token reusable.

## Secret handling

Bootstrap/recovery clear token is SECRET under #61.

Storage follows #56:
- verifier/hash only;
- clear shown once;
- no readback;
- secure random generation;
- constant-time verification.

## Testing

No real IdP admin or real recovery secret is required in CI.

Use synthetic OIDC subject/MFA fixtures and generated random test grants.

## Prohibited designs

- admin/admin;
- default password;
- hardcoded master secret;
- shared permanent emergency API key;
- recovery/security questions;
- hidden bypass header/query parameter;
- bootstrap based only on first requester winning;
- permanent unrestricted break-glass account enabled by default;
- direct DB edit as the normal documented recovery procedure without audited tooling.

## Consequences

Positive:
- no standing master credential;
- bootstrap race and takeover are explicit;
- emergency access remains identity-bound, temporary and auditable;
- small self-hosted deployment remains recoverable without weakening normal web auth.

Costs:
- deployment must preserve a secure infrastructure recovery capability;
- true IdP outage prevents ordinary web admin until identity service/configuration is restored;
- recovery runbooks and alerts are required before public RC.
