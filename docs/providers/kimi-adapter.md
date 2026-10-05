# Kimi / Moonshot Chat Completions adapter

## Scope

Issue #130 ports the international Kimi / Moonshot Chat Completions API as a
provider-neutral, single-attempt ORQETIA adapter. The adapter consumes the exact
target already resolved by the Provider Registry and owns no target selection,
retry, fallback, cycles, delay, health, quarantine, pricing, or credential lifecycle.

## Wire contract

- endpoint: `POST https://api.moonshot.ai/v1/chat/completions`;
- authentication: ephemeral Bearer credential supplied by composition;
- model: forwarded exactly from the resolved target;
- reasoning: the exact resolved profile is forwarded as `reasoning_effort`;
- `PROVIDER_DEFAULT` omits `reasoning_effort` rather than inventing a value;
- strict JSON Schema structured output is used when requested;
- no tools, web search, prompt-cache options, or China endpoint are inferred.

## Schema projection

The durable schema remains canonical and is always used by local validation. The
provider wire receives a conservative strict-schema subset. Value, length, numeric,
and cardinality constraints outside the characterized Kimi wire subset are removed
from the outbound schema copy only.

Structural object/array types, required fields, enums, and
`additionalProperties` remain. The projection does not mutate the durable schema or
arbitrary request content.

## Output and privacy

Only final `message.content` becomes canonical output. Provider
`reasoning_content` is not persisted as the response. Invalid structured output
fails local validation and is not stored as success.

Credentials remain ephemeral and redacted. Provider balances, pricing, account
details, and internal cost remain outside client-facing contracts.

## Failure and ambiguity boundary

Exactly one HTTP request is executed. `Retry-After` is metadata only; the adapter
does not sleep or retry. A transport exception after dispatch may have begun becomes
`ProviderDispatchAmbiguousError`; the durable orchestration boundary must not
silently redispatch it.

## Usage

Prompt/completion usage is normalized to canonical input/output tokens. Cached-input
tokens and provider total tokens are retained as non-monetary native usage.
Provider-reported cache-write tokens remain inside prompt totals and are not
double-counted.

## Characterization sources

Read-only characterization used for this port:

- RASAi `KimiProvider`;
- RASAi `kimi_wire_schema` and Kimi provider tests;
- the international Moonshot endpoint contract characterized by that baseline.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_kimi` uses deterministic in-memory fakes only. Ordinary CI
performs no Moonshot network call and incurs no provider cost.
