# Cohere Chat V2 adapter

## Scope

Issue #133 ports Cohere through the native Chat V2 HTTP API. The adapter consumes the
exact target already resolved by the Provider Registry and executes one provider
attempt only.

It owns no provider/model selection, retry, fallback, cycle, delay, health,
quarantine, pricing, commercial-mode selection, credential lifecycle, or client-facing
policy.

## Wire contract

- endpoint: `POST https://api.cohere.com/v2/chat`;
- authentication: ephemeral Bearer credential;
- model: forwarded exactly from the resolved target;
- messages: optional system instructions plus the durable user input;
- structured output: `response_format.type=json_object` with a projected schema;
- no tools, documents, RAG, rerank, or provider-native orchestration are enabled.

## Schema projection

The durable JSON Schema remains canonical and is always used by local validation.
Only the outbound provider copy is projected. Unsupported composition/value/cardinality
keywords are removed while structural object/array types, required fields, enums,
`anyOf`, and `additionalProperties` remain.

The projection never changes arbitrary payload content outside the schema boundary.

## Output and native finish reasons

Only Chat V2 `message.content[]` items with `type=text` become canonical output.

Native finish reasons fail closed:

- `TIMEOUT` -> timeout;
- `ERROR` -> transient/server failure;
- `TOOL_CALL` -> malformed unauthorized tool call.

Structured text must parse as JSON and pass the local durable-schema validator before
persistence.

## Usage

When Cohere returns both `usage.billed_units` and `usage.tokens`, ORQETIA prefers
`billed_units`, matching the characterized provider contract. Input/output units are
normalized to canonical token usage and the derived provider total is retained as
non-monetary native usage.

Commercial mode (`TRIAL`, `PRODUCTION`, or other accounting classification),
pricing, provider balances and account details remain outside this adapter and outside
client-facing contracts.

## Failure and ambiguity boundary

Authentication/permission, rate limit, timeout and server failures are normalized
without retry. `Retry-After` is metadata only.

A transport exception after dispatch may have begun becomes
`ProviderDispatchAmbiguousError`; the durable orchestration boundary must not
silently redispatch it.

Credentials and provider error messages are not persisted.

## Characterization sources

Read-only characterization used for this port:

- RASAi `CohereProvider`;
- RASAi `cohere_wire_schema`;
- RASAi Cohere provider tests covering billed-unit preference and Chat V2 output.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_cohere` uses deterministic in-memory fakes only. Ordinary CI
performs no Cohere network call and incurs no provider cost.
