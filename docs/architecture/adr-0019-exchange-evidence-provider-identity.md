# ADR-0019 — Sanitized exchange evidence integrity and provider identity

- Status: **Accepted**
- Issue: #88
- Amends: ADR-0018 (#86/#87), data classification #61
- Source requirement: RASAi PR #201 / main \`8008a3e24550f7c4b199beb0adeefa9c4e618538\`
- RASAi is read-only and is not a runtime/build dependency.

## Decision register

| Decision | Classification |
|---|---|
| Secrets/private material are sanitized before exchange evidence persistence | \`PERSISTENCE_CONTRACT\` |
| Persisted sanitized raw evidence is the technical source of truth exposed by raw-evidence APIs | \`OBSERVABILITY_CONTRACT\` |
| Presentation layers may humanize metadata but never rewrite persisted raw evidence tokens | \`PUBLIC_API_CONTRACT\` |
| Raw-evidence transport escaping/encoding may change representation, not semantic content | \`PUBLIC_API_CONTRACT\` |
| \`provider_id\` is a typed domain identity, separate from \`provider_name\` display metadata | \`ORCHESTRATION_CORE\` + \`PUBLIC_API_CONTRACT\` |
| RASAi HTML/CSS/labels/generic fallback behavior is not imported | \`CLIENT_SPECIFIC_NOT_APPLICABLE\` |

## Evidence boundary

Exchange evidence is provider communication captured for traceability:
- request;
- response;
- prompt/instructions when separately represented;
- structured-output schema;
- provider payload;
- transport-safe error body when policy permits.

Evidence is not the same thing as presentation metadata.

### Humanizable metadata

Examples:
- public operation label;
- provider display name;
- status label;
- derived explanation;
- localized title;
- truncation notice.

### Sanitized raw evidence

Examples:
- persisted request body;
- persisted response body;
- prompt text;
- JSON/schema text;
- provider wire payload after secret redaction.

The two surfaces are represented by separate DTO fields/types.

## Sanitization order

The mandatory order is:

    raw provider communication
      -> secret/private-data sanitization
      -> classification/size/truncation policy
      -> persistence
      -> integrity hash over persisted sanitized evidence
      -> API/UI presentation

A UI/API layer does not receive unsanitized raw content for later redaction.

Sanitization must not be deferred to the browser.

## Persisted source of truth

For evidence captured as raw text, persistence stores the sanitized body as the authoritative technical representation.

Recommended logical fields:
- \`evidence_id\`;
- \`exchange_id\`;
- \`attempt_id\`;
- \`kind\` = REQUEST | RESPONSE | PROMPT | SCHEMA | OTHER;
- \`media_type\`;
- \`encoding\`;
- \`sanitized_body\` or protected body reference;
- \`sanitized_sha256\`;
- \`truncated\`;
- \`original_size_bytes\` when safely known;
- \`persisted_size_bytes\`;
- \`created_at\`.

A protected payload/blob store may replace the inline body for size reasons, but the same evidence contract applies.

## Semantic immutability

After sanitized evidence is persisted:
- presentation code must not translate/replace technical tokens inside the body;
- generic enum/status humanizers do not run over the body;
- provider/model public naming does not rewrite body text;
- localization does not rewrite body text;
- support/report code does not substitute user-friendly terminology inside the body.

Example:

    SEMANTIC_READINESS

inside persisted sanitized evidence remains that token when exposed as raw evidence.

A human-readable label may separately say something like "Semantic readiness" in a metadata field.

## Transport representation

The raw-evidence API should prefer returning the persisted body as a string/value with:
- media type;
- hash;
- truncation flag;
- classification-safe metadata.

Transport escaping is allowed:
- JSON string escaping;
- HTML escaping;
- base64 when binary/encoding requires it.

The receiver must be able to recover the same persisted sanitized content.

HTML escaping \`<raw>\` to \`&lt;raw&gt;\` is safe transport rendering. Replacing \`SEMANTIC_READINESS\` with a translated label is not.

## Integrity hash

\`sanitized_sha256\` hashes the persisted sanitized evidence bytes/text encoding, not the pre-sanitization secret-bearing input.

The hash proves integrity of what ORQETIA is permitted to persist/expose.

A separate private forensic hash of original content is **not** part of the baseline and would require its own security/privacy design.

## Truncation

If capture limits truncate evidence:
- truncation occurs before final persistence/hash;
- \`truncated=true\` is explicit;
- the API/UI never reconstructs or invents missing content;
- hash refers to the persisted truncated sanitized body unless a future contract explicitly defines another hash.

## Attempt correlation

Every new exchange/evidence item belongs to an explicit \`attempt_id\` per ADR-0018.

Evidence lookup does not correlate by:
- provider;
- model;
- timestamp/proximity;
- purpose/operation label;
- request hash.

Those fields may be filters/diagnostics, not identity.

## Data classification

Sanitization does not automatically make content PUBLIC or non-personal.

Baseline:
- request/response/prompt/schema evidence inherits at least the classification required by its source;
- client prompt/output evidence is normally \`CLIENT_PRIVATE\`;
- personal/sensitive-data privacy flags still apply after secret redaction;
- \`SECRET\` values are removed before evidence persistence;
- \`RESTRICTED\`/internal provider/commercial fields are omitted unless a specifically authorized evidence contract requires them.

A sanitized provider API key placeholder does not declassify the surrounding client content.

## Raw evidence API boundary

Client-facing raw evidence, if exposed:
- requires tenant/client ownership and explicit scope;
- returns only that client's authorized evidence;
- never includes provider secret/account/credential;
- never includes internal provider cost/pricing/balance;
- exposes \`attempt_id\` as the correlation identity;
- returns sanitized raw content separately from metadata labels.

Backoffice may have broader evidence access according to purpose/RBAC/audit, but still never receives persisted provider secrets because they are not part of evidence.

## DTO separation

Conceptual contract:

    ExchangeEvidenceDto {
      attempt_id
      exchange_id
      provider: {
        provider_id
        provider_name
      }
      metadata: {
        operation
        operation_label
        status
        status_label
        ...
      }
      request_evidence: {
        media_type
        sanitized_raw_body
        sanitized_sha256
        truncated
      }
      response_evidence: { ... }
    }

\`metadata.*_label\` may be localized/humanized.

\`sanitized_raw_body\` may not be semantically humanized.

## Provider identity

Provider identity is domain data.

### provider_id

- canonical identifier from Provider Registry;
- stable within its versioned registry contract;
- used for routing/provenance/filtering;
- never passed through generic status/enum fallback logic.

Examples may include:
- \`OPENAI\`;
- \`GEMINI\`;
- \`ANTHROPIC\`;
- \`DEEPSEEK\`;
- \`MISTRAL\`;
- \`COHERE\`;
- \`KIMI\`.

These examples do not imply all providers are implemented.

### provider_name

- display metadata;
- may be "OpenAI", "Anthropic", etc.;
- may later be localized/brand-governed without changing \`provider_id\`.

A missing display name falls back to the provider identity itself or a provider-specific registry default, not to a generic status/error label.

## Generic humanization boundary

Generic helpers for status/error/enum labels must accept only the metadata types they are designed for.

They must not accept:
- ProviderId;
- SanitizedRawEvidence;
- secret-wrapper types.

Type separation is preferred over a runtime "if uppercase then maybe enum" heuristic.

## Logging

Raw evidence is not ordinary log content.

Logs/traces:
- carry \`attempt_id\`, \`exchange_id\`, provider_id and safe status metadata;
- do not dump request/response bodies by default;
- do not log secret-bearing pre-sanitization values;
- may record evidence hash/truncation flag where useful.

## Mutability / corrections

Persisted evidence is append/immutable in normal operation.

If a privacy/security correction requires removal or replacement:
- original evidence is tombstoned/restricted according to #55;
- a new revision/reference is created where legally/operationally required;
- the system does not silently rewrite the body while retaining the same integrity hash/version.

## Tests

The test-only contract proves:
1. secret sanitization occurs before persistence;
2. integrity hash is over sanitized persisted content;
3. raw API returns the same persisted sanitized body;
4. metadata humanization does not modify raw body tokens;
5. HTML transport escaping can round-trip to the same body;
6. provider_id remains typed/canonical;
7. provider display naming is separate;
8. generic status fallback never rewrites provider identity;
9. exchange/evidence keeps ADR-0018 attempt_id;
10. client raw evidence DTO excludes provider secret/cost fields.

## Deliberately not copied from RASAi

- HTML/CSS;
- report-catalog/CAT-01..CAT-10;
- pt-BR report labels;
- Recommendation Governance;
- Directed Analysis;
- Competitive/Search Intelligence;
- \`safe_visible_fallback()\`;
- provider visual rules;
- RASAi-specific audit tables.

## Consequences

Positive:
- technical evidence is trustworthy and reproducible;
- UI localization cannot corrupt provider communication;
- provider identity cannot be mistaken for generic internal status codes;
- sanitization/privacy boundary is explicit.

Costs:
- raw evidence and presentation metadata need separate DTOs/types;
- evidence corrections require controlled revision/tombstone workflows;
- consumers that want friendly descriptions must render them separately.
