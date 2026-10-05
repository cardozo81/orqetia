# Data classification and access matrix — ORQETIA

- Issue: #61
- Depends on: #13, #53, #55, #56
- Status: **Accepted**
- Security controls: SEC-001, SEC-004, SEC-009, SEC-011, SEC-012

## Model

ORQETIA classifies data along independent dimensions.

### Confidentiality class

One of:
- SECRET;
- RESTRICTED;
- CONFIDENTIAL;
- CLIENT_PRIVATE;
- INTERNAL;
- PUBLIC.

### Privacy flags

Independent flags, when applicable:
- PERSONAL_DATA;
- SENSITIVE_PERSONAL_DATA;
- CHILD_OR_VULNERABLE_DATA;
- PSEUDONYMIZED_PERSONAL_DATA;
- ANONYMIZED_APPROVED.

### Scope

- GLOBAL;
- TENANT;
- CLIENT;
- SUBJECT;
- PROVIDER_ACCOUNT;
- INTERNAL_SECURITY.

### Lifecycle

Every persisted class maps to:
- purpose/processing activity;
- retention policy;
- encryption requirement;
- export policy;
- audit policy.

A confidentiality label never proves that data is anonymous/non-personal.

## Default rule

New data is deny-by-default.

A new persistent field/event/export dimension that may carry non-PUBLIC data must declare:
- confidentiality class;
- ownership/scope;
- privacy flags when applicable;
- authorized audiences;
- retention/purpose;
- redaction/export behavior.

Unknown classification is not treated as INTERNAL or PUBLIC.

## Classes

### SECRET

Examples:
- provider API key/token;
- recoverable client/shared secret;
- private signing/encryption key;
- KEK/DEK material;
- browser session secret/cookie;
- OAuth access/refresh/ID token;
- PKCE verifier;
- TOTP seed;
- recovery code/bootstrap/break-glass secret.

Controls:
- never in logs/traces/metrics/issues/PRs/reports/read models/general queues/events;
- runtime least-privilege access only;
- #56 secret storage;
- not re-displayed after storage when avoidable;
- no ordinary export;
- encryption/secret-manager required.

SECRET cannot be downgraded merely by masking part of the value.

### RESTRICTED

Examples:
- provider contractual/commercial terms;
- provider credit/balance;
- provider observed cost/invoice evidence;
- highly sensitive admin/security audit evidence;
- incident forensic evidence;
- break-glass/recovery administrative evidence that is not itself SECRET.

Access:
- explicit Backoffice finance/provider/security roles according to purpose;
- no Client Portal/client API;
- all human reads/exports audited where practical.

Storage/export:
- encrypted at rest by platform/storage controls;
- export explicitly authorized, bounded and watermarked/audited;
- never GLOBAL_PUBLIC.

### CONFIDENTIAL

Examples:
- estimated provider cost;
- internal pricing catalogs/rules;
- provider account safe identifiers where commercially sensitive;
- provider_credential_id/fingerprint/version;
- cross-client operational intelligence;
- internal quota/capacity/provider health details that reveal commercial operations.

Access:
- explicit Backoffice operational/finance roles;
- worker runtime when required;
- no client-facing serialization in the baseline.

### CLIENT_PRIVATE

Examples:
- prompts/input/context;
- outputs/results;
- session/task details;
- client token/native usage;
- client integration credential metadata/fingerprint;
- customer-human profile/membership data;
- client-specific reports;
- CLIENT_ONLY statistical aggregates.

Access:
- authoritative tenant/client owner with required role/scope;
- explicitly authorized Backoffice access for support/security/operations with purpose and audit where sensitive;
- never another tenant/client.

A client may see its own technical token usage but not provider cost/credit/contract data.

### INTERNAL

Examples:
- non-sensitive deployment metadata;
- application version;
- non-sensitive generic health status;
- architecture/configuration metadata that does not reveal secrets, commercial data or client content.

Access:
- internal/Backoffice/operations as needed;
- client exposure only through an explicitly approved public/client contract.

INTERNAL is not a shortcut for unreviewed data.

### PUBLIC

Only data explicitly approved for unauthenticated/public release.

Examples:
- public API documentation;
- approved generic provider/model catalog fields;
- approved status/marketing documentation;
- GLOBAL_PUBLIC aggregate after privacy/statistical release checks.

Absence of an access restriction does not automatically make data PUBLIC.

## Inheritance and derived data

Derived data inherits the highest relevant protection of its inputs unless an explicit declassification/transformation rule proves otherwise.

Examples:
- summary generated from client prompt remains CLIENT_PRIVATE;
- hash/fingerprint of provider credential remains at least CONFIDENTIAL when it identifies a credential;
- pseudonymized client data remains CLIENT_PRIVATE/personal unless approved anonymization criteria are satisfied;
- aggregate cost across clients remains CONFIDENTIAL or RESTRICTED depending content, not PUBLIC by aggregation alone.

## Audience matrix

| Data class | Service client API | Customer Portal | Backoffice Ops | Backoffice Finance | Security/Admin | Provider Worker | Logs/Traces | General Queue/Event | GLOBAL_PUBLIC |
|---|---|---|---|---|---|---|---|---|---|
| SECRET | DENY | DENY | write/rotate metadata only; no ordinary reveal | DENY | exceptional capability only if specifically designed | resolve narrowly when required | DENY | DENY; reference only | DENY |
| RESTRICTED | DENY | DENY | explicit purpose/privilege | explicit privilege | explicit purpose/privilege | only if execution strictly requires specific field | safe IDs only | safe references/minimum fields | DENY |
| CONFIDENTIAL | DENY | DENY | explicit privilege | explicit privilege | explicit privilege | as required | minimize/redact | minimize/safe references | DENY |
| CLIENT_PRIVATE | OWN + scope/role | OWN + role | purpose-bound privilege | only if required and authorized | purpose-bound privilege | task-scoped required content | identifiers/minimized only; raw content default DENY | task-scoped payload only when queue contract requires; no secrets | DENY raw; aggregate only after approved release |
| INTERNAL | endpoint-dependent | endpoint-dependent | ALLOW as required | ALLOW as required | ALLOW as required | ALLOW as required | ALLOW if non-sensitive | ALLOW if contract permits | DENY unless explicitly declassified |
| PUBLIC | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | contract-dependent | ALLOW |

"ALLOW" still requires normal authentication/authorization where the surface itself is not public.

## Role examples

### Client/service side

May receive:
- own session/task/result;
- own technical token/native usage;
- public/allowed provider/model descriptors;
- own integration credential metadata;
- token estimates.

Must not receive:
- provider secret/account credential;
- provider_credential_id/fingerprint;
- provider cost/currency/balance;
- internal pricing/catalog commercial fields;
- another client's data;
- cross-client operational intelligence.

### Backoffice provider operator

May manage:
- provider account;
- provider credential lifecycle;
- provider operational status;
- safe credential metadata.

Clear secret readback remains denied by baseline after persistence.

### Backoffice finance

May read:
- provider observed/estimated cost;
- pricing catalogs;
- balances/contract terms as authorized;
- client usage/cost internal analytics.

Does not gain provider secret-read capability merely because finance can see cost.

### Security administrator

May access incident/audit evidence under explicit purpose.

Security role does not automatically grant finance or provider-secret access.

## Client/provider credential identifiers

Provider credential safe ID/fingerprint:
- CONFIDENTIAL;
- never included in client-facing attempt/usage serialization;
- may be used in Backoffice reports and internal attempt correlation.

Client integration credential safe ID/fingerprint:
- CLIENT_PRIVATE;
- may be exposed to the owning authorized client user for identification/revocation;
- clear secret remains SECRET and show-once only.

## Financial data

Baseline provider-side finance:
- observed provider cost/invoice/balance: RESTRICTED;
- estimated provider cost/internal pricing: CONFIDENTIAL;
- provider currency/contract rate: CONFIDENTIAL or RESTRICTED according to source/terms.

Client-facing API/Portal receives **no monetary provider fields** in the first delivery.

Future client_charge/commercial billing (#41) receives a separate classification/access contract.

## Prompt/input/output

Minimum classification: CLIENT_PRIVATE.

If content includes personal/sensitive data, apply the corresponding privacy flags.

Raw content:
- excluded from normal logs/traces;
- excluded from GLOBAL_PUBLIC;
- not used for another client;
- provider transmission only under approved task/provider/privacy policy;
- retention under #55.

Support tools must use redacted/minimized views unless raw content access is explicitly required, authorized and audited.

## Logs and traces

Allowed baseline:
- correlation/trace ID;
- safe internal resource IDs;
- status/error class;
- latency;
- code/version;
- minimal tenant/client identifiers where justified and protected.

Prohibited:
- SECRET;
- raw prompt/output by default;
- authorization headers;
- cookies/tokens;
- provider error bodies without sanitization;
- provider cost/client financial values in broadly accessible operational logs.

Log access and retention preserve the source data classification.

## Queue/event contracts

General event buses/queues carry the minimum information necessary.

Rules:
- SECRET is replaced by SecretRef/safe credential ID;
- event does not lower classification;
- CLIENT_PRIVATE payload is included only if the asynchronous consumer actually needs it;
- large/raw task payload may use a protected payload store/reference instead of duplicated queue copies;
- consumers authenticate/authorize data scope from trusted event metadata, not client input.

## Cache

Cache never reduces classification.

Requirements:
- tenant/client/security scope in cache keys;
- SECRET is not placed in general-purpose cache;
- CLIENT_PRIVATE cache entries are scoped and bounded;
- authorization-sensitive cached decisions have short validity/version invalidation;
- cache miss/failure never widens access.

## Exports

Export is a separate authorization action.

Requirements:
- explicit export privilege/scope;
- tenant/client filters enforced server-side;
- field allowlist by audience/class;
- row/size/time bounds;
- export generated from authoritative/read-model contracts approved for that audience;
- RESTRICTED/CONFIDENTIAL administrative export audited;
- export artifacts inherit highest included class and have bounded lifetime/access.

CSV/spreadsheet exports must defend against formula injection where user-controlled text is included.

## UI and browser

Client UI receives only fields it is authorized to display; sensitive fields are absent from the response schema rather than hidden with CSS.

Backoffice UI:
- separates provider operations, finance and security privileges;
- masks safe credential metadata appropriately;
- never embeds SECRET in frontend bundle/source/state.

## API errors

Error payloads are INTERNAL/PUBLIC-safe envelopes only.

Do not include:
- secret;
- SQL;
- stack trace;
- provider raw body;
- cross-tenant object metadata;
- confidential financial details.

## Backups and environments

Backup classification equals the highest class it contains.

Production data is not copied to development/test by default.

Any exceptional sanitized dataset creation must prove:
- SECRET removal;
- tenant/privacy minimization;
- reidentification-risk control;
- authorization and audit.

Synthetic data is the default for tests.

## Field/schema review gate

A PR adding/changing persistent/API/event fields must answer:
- classification;
- privacy flags;
- owner/scope;
- audiences;
- logging/export behavior;
- retention;
- migration/backfill implications.

For client-facing schemas, absence of a field is preferred to runtime redaction of a field the client should never receive.

## Consequences

This model keeps commercial/provider sensitivity, client confidentiality and LGPD status separate while giving API/UI/log/event/export implementations an enforceable deny-by-default matrix.
