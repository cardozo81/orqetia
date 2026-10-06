# ADR-0042 — Schema evolution and backward compatibility for v1

- Status: **Accepted**
- Issue: #155
- Lifecycle: **DEVELOPMENT**
- Related: #4, #27, #33, #46, #51

## Decision

ORQETIA treats public Client API schemas, execution-selection schemas and event
contracts as versioned compatibility boundaries. A contract change is classified
before merge/direct-main integration as one of:

- **additive** — new optional surface that does not invalidate an existing
  conforming request/response;
- **compatible** — metadata/documentation-only or otherwise semantically
  compatible change;
- **breaking** — an existing conforming producer/consumer may stop working or
  receive a contract it cannot safely interpret.

The checker is intentionally conservative. A false-positive breaking
classification is preferable to silently changing v1 semantics.

## Client API v1 rules

Within `/v1`:

Compatible/additive examples:
- add an optional request property;
- add a new endpoint under `/v1`;
- add a new response media type/status when existing response contracts remain;
- documentation/example/description clarification.

Breaking examples:
- remove an endpoint, parameter, property, response or schema;
- add a required request parameter/property/body;
- change a property type, `$ref`, format, pattern or bounded constraint;
- change required scopes;
- change the required-property set;
- change a `oneOf`/`anyOf`/`allOf` composition;
- add a public path outside `/v1` before an explicit lifecycle decision.

## Required fields

Changing the required-property set is classified as breaking in either
direction. Optionalizing a response property can break consumers that relied on
its presence; making a request property required can break producers.

A narrower context-specific relaxation may be approved only through the explicit
breaking-change process below.

## Enums

Enum values are closed by default. Adding or removing a value is breaking because
generated/exhaustive consumers may not tolerate a new value.

A schema may opt into additive enum growth only by explicitly carrying:

`x-orqetia-extensible-enum: true`

This marker is itself part of the contract and should be used only where unknown
future values are safe for consumers.

## Error envelope

`ErrorEnvelope` is a public schema. Removing/changing required fields, field
types or semantics is breaking. New optional detail fields are additive.

Adding a newly documented HTTP error status is additive only when the existing
error envelope and authentication/authorization semantics remain valid. Changing
required scopes is always classified as breaking.

## Execution selection

`contracts/execution/task-execution-selection.schema.json` is checked as an
independent public JSON Schema because the OpenAPI references it externally.

Changing execution modes, target structure or composition is breaking unless the
old schema explicitly declared the affected enum extensible.

AUTO/EXPLICIT_TARGET semantics from #80 cannot be changed as a documentation-only
edit.

## Event contracts

`event_version` is scoped to `event_type`.

Within the same event version:
- adding an optional payload field is allowed only if old consumers safely ignore
  it;
- required-field, type, semantic or classification changes are breaking;
- enum rules are the same closed-by-default rules as the Client API.

A breaking event change requires a new `event_version`, producer/consumer
transition and replay compatibility. Historical persisted event payloads are
never silently rewritten.

Machine-readable event schemas belong under
`contracts/events/<event-type>/v<event_version>.schema.json` when a concrete
event type is implemented. The common envelope is versioned separately at
`contracts/events/envelope/v1.schema.json` and is checked for parity with the
runtime `EventEnvelope`.

## Alembic expand/contract

Database persistence is not the public API, but deployment compatibility still
requires an expand/contract discipline:

1. **expand** — add nullable/default-safe columns, new tables/indexes or parallel
   representation without removing what the running code still reads;
2. deploy writers/readers that can tolerate both representations;
3. backfill/rebuild with bounded, resumable operations where needed;
4. verify no old reader/writer requires the legacy shape;
5. **contract** — remove/rename/tighten only in a later migration.

Destructive rename/drop, NOT NULL without safe population, enum narrowing and
in-place semantic reinterpretation are contract-phase changes and must never be
combined with the first expand migration when rolling compatibility matters.

Downgrade functions are recovery aids, not a promise that destructive production
rollback is always safe.

## Explicit breaking-change approval

The compatibility workflow fails a breaking delta by default and prints an exact
fingerprint derived from:

- contract identifier;
- old/new canonical JSON SHA-256;
- detected breaking changes.

During DEVELOPMENT, a deliberate breaking change may proceed only when
`contracts/compatibility/approved-breaking-changes.json` contains the exact
fingerprint, an issue reference and a concrete reason.

Changing the approval file to a broad wildcard is prohibited. Approval is tied
to exact bytes and does not declare RC/GA.

A future public RC/GA breaking change is additionally governed by #46.

## Documentation synchronization

Any change to the Client OpenAPI or external execution-selection schema must
change `docs/api/client-api-v1-manual.md` in the same change set. This preserves
#27 as the human-readable companion of the canonical machine contract.

## Versioning/deprecation

- public namespace remains `/v1`;
- do not create `/v2` preemptively;
- deprecation must be documented before removal;
- no maturity/compatibility work may declare RC, GA or production;
- lifecycle state changes remain explicit decisions under #46.

## Gate

The isolated `Schema compatibility` workflow:

1. compares base revision contracts with HEAD;
2. classifies OpenAPI and execution-selection deltas;
3. rejects unapproved breaking changes;
4. requires manual synchronization for public Client API contract changes;
5. runs deterministic compatibility fixtures only.

No provider call or paid service is used.
