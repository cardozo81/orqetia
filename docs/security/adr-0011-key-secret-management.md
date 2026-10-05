# ADR-0011 — Cryptography, key management and secret storage

- Status: **Accepted**
- Issue: #56
- Depends on: #3, #13, #53
- Security controls: SEC-004, SEC-005, SEC-007, SEC-008, SEC-009, SEC-010, SEC-012

## Decision

ORQETIA separates secret classes by whether the runtime must recover their clear value.

### Non-recoverable credentials

When ORQETIA only needs to verify a presented credential, store a non-recoverable verifier/hash, not encrypted plaintext.

Examples:
- ORQETIA-issued client integration secret where the selected protocol permits hash-only verification;
- one-time recovery codes.

### Recoverable provider credentials

Provider API credentials must be recoverable by the provider execution runtime but must not be returned to clients and are not re-displayed to ordinary Backoffice users after persistence.

Production implementations use one of two approved storage modes behind the same SecretStorePort:

1. external managed secret store/KMS — preferred when available;
2. ORQETIA envelope encryption — acceptable for self-hosted/small deployment when a managed secret store is unavailable.

The relational control plane stores only safe metadata/reference plus encrypted envelope material when envelope mode is selected.

## Secret classes

### SECRET — recoverable runtime secret

Examples:
- provider API key/token;
- ORQETIA encryption KEK/DEK material;
- private signing key if ORQETIA ever owns one;
- bootstrap/break-glass secret.

### SECRET — non-recoverable verifier

Examples:
- ORQETIA client secret verifier;
- recovery-code verifier.

### Non-secret identifiers

Examples:
- credential UUID;
- client_id;
- provider account safe identifier;
- secret version;
- last four/fingerprint generated from a safe one-way function.

A safe identifier is never sufficient to reconstruct/authenticate with the secret.

## SecretStorePort

Application/domain code sees an opaque interface conceptually equivalent to:

- create(secret_class, plaintext, metadata) -> SecretRef;
- resolve_for_runtime(secret_ref, purpose) -> ephemeral secret value;
- rotate(secret_ref/new version);
- revoke(secret_ref/version);
- metadata(secret_ref);
- health/key-version inspection without plaintext.

Only infrastructure/provider execution code can call resolve_for_runtime.

Client-facing and ordinary reporting code never receives this capability.

## Backoffice write-only semantics

Backoffice may:
- create/import a provider credential;
- rotate/replace;
- disable/revoke;
- view safe metadata/fingerprint/status/timestamps;
- initiate an authorized validation workflow.

Backoffice ordinary UI/API may not retrieve the existing clear provider secret after it is stored.

Editing means replacement/rotation, not read-modify-write of plaintext.

An exceptional human cleartext-reveal feature is **not** part of the baseline and would require a separate threat model/ADR.

## External managed secret-store mode

Preferred production model:
- DB stores SecretRef/provider credential metadata only;
- secret value is stored in the configured secret manager;
- runtime identity has read access only to the provider secrets it needs;
- Backoffice application identity may create/rotate metadata through controlled capabilities but does not gain broad read/list privileges;
- secret-store/KMS audit logging is enabled where available.

Vendor selection remains deployment-specific.

## Envelope-encryption mode

For self-hosted deployments without a managed secret store:

### Cryptography

- random 256-bit data-encryption key (DEK) per provider credential version;
- authenticated encryption with AES-256-GCM;
- unique cryptographically random nonce per encryption;
- associated authenticated data binds ciphertext to stable security context;
- DEK is wrapped/encrypted by a key-encryption key (KEK);
- KEK plaintext is not stored in PostgreSQL.

Associated data includes stable values such as:
- secret/credential ID;
- provider account ID;
- credential version;
- algorithm/schema version.

Changing associated context requires controlled re-encryption rather than silently decrypting under mismatched metadata.

### Stored envelope fields

May include:
- ciphertext;
- nonce;
- wrapped DEK;
- KEK key/version identifier;
- encryption algorithm/version;
- safe fingerprint;
- created/rotated/revoked timestamps.

Never store:
- plaintext secret;
- plaintext DEK;
- KEK material.

## KEK storage and hierarchy

The KEK is external to the encrypted database.

Preferred:
- cloud/on-prem KMS/HSM/secret manager.

Self-hosted fallback:
- runtime-injected master key from a root secret facility controlled outside Git/container/DB;
- access limited to the dedicated application/runtime identity that performs envelope operations;
- root key is never emitted by config/debug endpoints or environment dumps.

A plaintext KEK committed to Git, baked into the image, stored in the same DB or included in ordinary backups invalidates the envelope-security model.

## Key rotation

### KEK rotation

Normal KEK rotation re-wraps DEKs under the new KEK version without re-encrypting every provider secret payload.

Requirements:
- old KEK remains available during controlled migration/recovery window;
- each envelope records key version;
- re-wrap is idempotent and resumable;
- progress/reconciliation is recorded;
- old KEK is revoked only after verification and backup/recovery policy permits.

### Data algorithm/DEK migration

When changing encryption algorithm or payload structure:
- decrypt in controlled runtime memory;
- generate new nonce/DEK as appropriate;
- encrypt with new version;
- atomically switch active envelope metadata;
- never log plaintext.

## Provider credential rotation

Provider credential lifecycle supports:
- PENDING;
- ACTIVE;
- DEPRECATED/ROTATING;
- REVOKED;
- ERROR/INVALID when required by later provider-account design.

Zero-downtime rotation may temporarily keep two provider credential versions active according to provider capability/policy.

New executions select only an administratively eligible active version.

Historical attempts retain only safe credential/version references, never secret material.

## Runtime resolution

Provider worker resolves a secret:
- just in time;
- after authorization/policy selects a specific provider credential reference;
- for the narrow provider-call purpose;
- outside any serialization/logging/event payload path.

The secret is held for the shortest practical lifetime.

Python does not provide a reliable guarantee of zeroizing every string copy from process memory; therefore the control is minimization, isolation, process access restriction and avoidance of unnecessary copies — not a false zeroization claim.

## Queues, events and logs

SECRET values are prohibited in:
- queue messages;
- outbox/inbox payloads;
- domain events;
- audit event metadata;
- traces;
- metrics labels;
- structured logs;
- exception messages;
- PR/issues/fixtures.

Async work carries only a SecretRef/provider credential safe identifier.

## Redaction

Infrastructure uses redacting secret-wrapper types whose string/repr form is never the secret.

Error handling:
- provider error body is treated as untrusted/sensitive;
- authorization headers and provider keys are removed before logging;
- known secret values must not be interpolated into errors;
- HTTP clients must not log request headers/bodies containing secrets at ordinary log levels.

Redaction is defense in depth; the primary rule is not to send secret-bearing objects into logging/tracing APIs.

## Client integration credentials

When ORQETIA owns a shared client secret:
- generate with CSPRNG and sufficient entropy;
- show clear value only at issuance/rotation;
- store verifier only;
- use constant-time verification;
- support overlap/rotation/revocation;
- never re-display.

Where private_key_jwt is used, ORQETIA/IdP stores the client's public verification material; the client retains its private key.

Exact credential protocol is finalized in #24.

## TLS

Public and provider traffic:
- HTTPS;
- certificate and hostname validation enabled;
- no disable-verify production flag;
- secrets not placed in URL/query string;
- redirects reviewed so Authorization/secret headers are not forwarded to untrusted host.

## Provider endpoint security

Clients do not supply arbitrary provider endpoints.

Provider account/control-plane configuration uses:
- known provider adapter;
- approved HTTPS origin/endpoint policy;
- explicit administrative privilege;
- SSRF validation under SEC-005.

DNS/IP/outbound controls are implemented in the provider boundary according to deployment capability; endpoint configuration never becomes a generic server-side fetch primitive.

## Backup and recovery

Database backups contain only:
- ciphertext/envelopes;
- safe secret metadata;
- non-recoverable verifiers.

Root KEK/secret-store recovery material is backed up separately with stronger access controls where required.

Recovery plan must test:
- database restore;
- key/secret-store availability;
- ability to decrypt an approved synthetic test secret;
- inability to recover a revoked/nonexistent secret according to policy.

Do not use real provider credentials in recovery tests.

## Separation of duties

Roles/capabilities are separated:
- Backoffice credential administrator can create/rotate/revoke metadata/secret but not query all clear values;
- provider execution runtime can resolve only required active secrets;
- finance/reporting has no secret-read capability;
- client-facing API has no provider-secret-read capability;
- DB administrator alone cannot decrypt envelope-mode secrets without KEK access.

Exact operational role mapping is implemented under #25/#59.

## Audit

Always audit safe metadata for human/admin:
- create;
- rotate;
- revoke;
- failed secret operation;
- privilege/configuration changes.

Runtime usage is correlated through provider_credential_id/version on attempts without logging the value.

Managed KMS/secret-manager access logs are retained according to security policy where available.

## Local development and CI

Normal CI uses synthetic/non-secret fixtures only.

Local real provider credentials, when a human explicitly uses them:
- are supplied through ignored local secret configuration or approved developer secret tooling;
- never committed;
- never required for ordinary tests;
- are not copied into test snapshots/artifacts.

## Failure behavior

Fail closed when:
- KEK/secret manager unavailable;
- envelope authentication/tag validation fails;
- key version unknown;
- credential revoked/inactive;
- provider endpoint violates policy.

Do not silently fall back to plaintext configuration or a different secret.

## Consequences

Positive:
- provider credentials remain ORQETIA-owned and hidden from clients;
- DB compromise alone does not reveal envelope-mode secrets;
- ordinary Backoffice compromise does not automatically provide secret readback;
- key rotation can re-wrap DEKs efficiently;
- runtime/provider and reporting boundaries stay separate.

Costs:
- secret-store/KMS availability becomes an execution dependency;
- self-hosted envelope mode requires disciplined external KEK operations;
- rotation/recovery need explicit runbooks and tests.
