# Gemini Interactions adapter

## Scope

Issue #131 ports Gemini through the native Interactions HTTP API. The adapter is
provider-neutral at the ORQETIA boundary and executes exactly one attempt against the
exact target already resolved by the Provider Registry.

It owns no target selection, retry, fallback, cycles, delay, health, quarantine,
pricing, credential lifecycle, or client-facing policy.

## Wire contract

- endpoint: `POST https://generativelanguage.googleapis.com/v1beta/interactions`;
- authentication: ephemeral `x-goog-api-key` supplied by composition;
- model: forwarded exactly from the resolved target;
- input: materialized instructions and input text;
- structured output: Interactions `response_format` with
  `mime_type=application/json` and a Gemini-compatible schema;
- no tools, search, grounding, or provider-native fallback is enabled by this port.

## Schema projection

The durable JSON Schema remains canonical and is always used by local validation.
Only a provider-compatible copy is projected to the Interactions wire. Unsupported
keywords are removed without weakening the post-response validation contract.

Object property names and `$defs` keys are preserved as data; only schema keywords
are filtered.

## Response extraction

The adapter accepts the characterized response shapes in order:

1. top-level `output_text`;
2. the most recent `steps[].type=model_output` text content.

Missing textual output is malformed. Structured text must parse as JSON and pass the
local durable-schema validator before persistence.

## Usage

The adapter normalizes both Interactions-style and Gemini usage-metadata aliases:

- prompt/input tokens;
- candidate/output tokens;
- cached input tokens;
- thought/reasoning tokens;
- provider total tokens.

Native detail remains non-monetary. Provider pricing, balances, account details and
internal cost stay outside client-facing contracts.

## Failure and ambiguity boundary

HTTP 401/403 and native authentication tokens map to auth failure. HTTP 429 remains a
rate-limit outcome; quota/resource-exhaustion tokens on non-429 failures can map to
quota exhaustion. Timeout and 5xx outcomes are normalized without adapter retry.

`Retry-After` is metadata only. Transport ambiguity after dispatch may have begun
raises `ProviderDispatchAmbiguousError`; the durable orchestration boundary must not
silently redispatch it.

Credentials and provider error messages are not persisted.

## Characterization sources

Read-only characterization used for this port:

- RASAi `GeminiProvider`;
- RASAi `gemini_wire_schema`;
- the Interactions response and usage shapes already homologated in RASAi.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_gemini` uses deterministic in-memory fakes only. Ordinary CI
performs no Gemini network call and incurs no provider cost.
