# ADR-0018 — Attempt identity, operation scope and exchange correlation

- Status: **Accepted**
- Issues: #86, #87
- Amends: ADR-0005 (#33), ADR-0014 (#14), canonical matrix #31/#34
- Source requirement: RASAi PR #201, merged to RASAi main as \`8008a3e24550f7c4b199beb0adeefa9c4e618538\`
- RASAi is read-only and is not a runtime/build dependency.

## Decision register

| Decision | Classification |
|---|---|
| \`attempt_id\` is the stable first-class identity of one provider dispatch attempt | \`ORCHESTRATION_CORE\` |
| Exchange/usage/pricing/diagnostic facts persist the same \`attempt_id\` | \`PERSISTENCE_CONTRACT\` |
| Retry/fallback create new attempts even when payload is identical | \`ORCHESTRATION_CORE\` |
| \`operation\` is a required typed semantic identifier for an attempt | \`ORCHESTRATION_CORE\` |
| \`task_id\` is optional when an operation contract is explicitly taskless | \`PERSISTENCE_CONTRACT\` |
| ORQETIA does not introduce a generic \`round\` entity without its own product requirement | \`CLIENT_SPECIFIC_NOT_APPLICABLE\` |
| Client/admin APIs may expose safe opaque \`attempt_id\`, but never infer identity from metadata | \`PUBLIC_API_CONTRACT\` |
| Logs/traces/exchange telemetry correlate by \`attempt_id\` | \`OBSERVABILITY_CONTRACT\` |
| RASAi Directed Analysis / Competitive Intelligence business meanings are not imported | \`CLIENT_SPECIFIC_NOT_APPLICABLE\` |

## Attempt identity

A provider attempt receives exactly one \`attempt_id\` **before** the external provider dispatch is initiated.

The identifier remains constant for all facts describing that one dispatch attempt:
- provider/model selection result;
- durable attempt row;
- outbound exchange/request evidence;
- inbound response evidence;
- normalized diagnostic/error;
- usage;
- pricing application;
- estimated/observed provider cost;
- tracing/telemetry;
- administrative drill-down;
- safe client-facing provenance when that API exposes attempts.

The identifier is opaque. UUIDv7 is preferred by ADR-0005 for persisted IDs, but the semantic contract is the stable identity itself, not the UUID version.

## Attempt vs request fingerprint

A request fingerprint/hash is not an attempt identity.

Fingerprint may support:
- idempotency diagnostics;
- duplicate-content analysis;
- historical/import reconciliation;
- integrity checks.

It must not be used as the primary join between attempt and exchange/usage/pricing.

Two attempts may have byte-identical or semantically identical requests and still have different \`attempt_id\` values.

New ORQETIA data never correlates an exchange to an attempt using provider/model/timestamp/proximity/purpose/hash inference.

## Retry and fallback

A new provider dispatch creates a new attempt.

Therefore:
- orchestration retry of the same target => new \`attempt_id\`;
- a new AUTO candidate/fallback provider => new \`attempt_id\`;
- retry after a known provider failure => new \`attempt_id\`;
- request payload may remain identical and its fingerprint may remain identical.

The previous/new relationship is represented explicitly when useful:
- \`retry_of_attempt_id\`;
- \`fallback_from_attempt_id\`;
- \`attempt_index\`;
- \`cycle\`.

These relationships never replace the current attempt's own ID.

This ADR does not change canonical retry, fallback, candidate ranking, quarantine, pricing or cycle policy.

## Provider-side idempotent replay exception

ADR-0014 defines the crash-after-dispatch case.

If an upstream provider supports a verified idempotency token, ORQETIA may re-send the **same logical provider attempt** after an ambiguous transport failure using the same \`attempt_id\` and upstream idempotency token.

That is recovery of the same ambiguous attempt, not creation of a normal orchestration retry.

If upstream idempotency is unavailable, the attempt remains ambiguous and must not be silently re-dispatched.

## Operation

Every attempt has a stable typed \`operation\`.

\`operation\` describes the ORQETIA-level purpose/capability being executed. It is:
- controlled by an ORQETIA contract/registry;
- not arbitrary free-form human text;
- independent from UI labels/localization;
- suitable for audit/filtering/versioned contracts.

Examples will be defined by ORQETIA product contracts. RASAi-specific values such as \`DIRECTED_ANALYSIS\` are not imported merely for compatibility.

Free-form descriptive metadata may exist separately as display/diagnostic context.

## Taskless operations

A provider attempt does not universally require a Task.

\`task_id\` is nullable only when the selected operation contract explicitly permits taskless execution.

Taskless does **not** mean ungoverned. The attempt still requires:
- \`attempt_id\`;
- \`operation\`;
- tenant/client ownership when client-scoped;
- provider/model identity;
- policy/security context required by that operation;
- timestamps/status;
- usage/pricing/diagnostic correlation when produced.

No synthetic Task is created solely to satisfy telemetry or database non-null constraints.

## Session relationship

Current public task execution remains Session/Task-oriented.

For generic core persistence:
- \`session_id\` may be nullable only for an operation contract that explicitly allows execution outside a session;
- if \`task_id\` is present, the attempt must inherit/validate the owning task/session tenant/client context;
- if \`task_id\` is absent, ownership and policy context must still be directly reconstructible.

This preserves the existing runtime design without turning Session/Task into universal prerequisites for every future operation.

## Round

ORQETIA currently has no generic \`round\` concept.

No \`round_id\` column/entity is introduced for RASAi compatibility. A future ORQETIA product feature may define a batch/round concept through its own ADR if it has independent semantics.

## Exchange

\`Exchange\` is the persisted sanitized evidence/provenance of provider communication belonging to exactly one attempt.

For newly created ORQETIA exchanges:
- \`attempt_id\` is mandatory;
- one attempt may have multiple exchange records only when the adapter protocol explicitly produces multiple wire exchanges for that same logical dispatch;
- every exchange uses the attempt's stable identity.

Exchange evidence integrity/sanitization is further defined by #88.

## Usage, pricing and diagnostics

All facts describing provider execution are keyed by \`attempt_id\`:
- technical usage;
- native usage;
- pricing application;
- estimated/observed provider cost;
- normalized diagnostic;
- provider response status/timing.

Cross-context references remain logical IDs per ADR-0005/ADR-0006; a shared database does not create cross-context ownership.

## Historical/import correlation

ORQETIA is a new product and does not need RASAi historical compatibility.

If ORQETIA later imports legacy data without explicit \`attempt_id\`:
- legacy records remain explicitly uncorrelated unless a deterministic import rule can prove identity;
- a unique exact fingerprint may be used only inside a versioned migration/import reconciler;
- ambiguity remains unresolved;
- timestamp/proximity guessing is prohibited.

This fallback never applies to newly generated ORQETIA records.

## Provider identity

Attempts store typed \`provider_id\` and \`model_id\` domain identities.

Provider identity is not inferred from presentation labels. Provider display naming is a separate contract (#88).

## API boundary

When an authorized API exposes attempt provenance:
- \`attempt_id\` is the canonical opaque identifier;
- \`operation\` may be exposed according to the API contract;
- nullable \`task_id\` is represented explicitly, not replaced with a fake task;
- request fingerprints are diagnostic fields only and are not an alternate identifier.

Client-facing APIs continue to exclude provider credentials, internal monetary data and confidential control-plane metadata.

## Events and observability

Events such as \`provider.attempt.completed\` / \`provider.attempt.failed\` carry:
- \`attempt_id\`;
- \`operation\`;
- \`task_id\`/session reference only when present/applicable.

Logs/traces use \`attempt_id\` as the durable provider-call correlation key.

Trace/span IDs may correlate distributed telemetry but do not replace the persisted attempt identity.

## Data-model consequences

The initial logical model becomes:

- \`execution.provider_attempts.attempt_id\` — PK/stable identity;
- \`operation\` — NOT NULL typed code;
- \`task_id\` — nullable under operation contract;
- \`session_id\` — nullable only under operation contract;
- optional \`retry_of_attempt_id\` / \`fallback_from_attempt_id\`;
- request fingerprint — auxiliary only;
- \`execution.provider_exchanges.attempt_id\` — mandatory logical owner/reference;
- usage/pricing/diagnostic facts — mandatory \`attempt_id\`.

No migration is required now because no production schema has been implemented.

## Test contract

The test-only oracle proves:
1. one provider dispatch creates one stable \`attempt_id\`;
2. exchange receives that same \`attempt_id\`;
3. usage/pricing/diagnostic facts receive that same \`attempt_id\`;
4. retry with identical request fingerprint creates a different \`attempt_id\`;
5. fallback creates a different \`attempt_id\`;
6. taskless attempt is valid only for an operation explicitly permitting it;
7. taskless attempt still requires non-empty \`operation\`;
8. new exchange binding cannot fall back to timestamp/provider/model/purpose/fingerprint guessing;
9. ambiguous historical fingerprint matching remains unresolved;
10. no round concept is introduced.

## Deliberately not copied from RASAi

- \`DIRECTED_ANALYSIS\`;
- Competitive/Search Intelligence;
- RASAi task/round tables;
- audit/report catalog structures;
- HTML/CSS/labels;
- RASAi identifier prefixes;
- historical SQLite migration mechanics.

## Consequences

Positive:
- execution, evidence and accounting correlation is deterministic;
- retries/fallback preserve provenance;
- future taskless core operations do not require fake domain rows;
- API/observability share one provider-call identity.

Costs:
- every provider-bound pipeline must propagate \`attempt_id\`;
- operation contracts need explicit task/session requirements;
- adapters that create multiple wire exchanges need an explicit sub-exchange sequence rather than inventing new attempts.
