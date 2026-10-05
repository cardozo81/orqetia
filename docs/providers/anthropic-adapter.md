# Anthropic Messages adapter

## Scope

Issue #132 ports Anthropic through the native Messages HTTP API. The adapter consumes
the exact target already resolved by the Provider Registry and executes exactly one
provider attempt.

It owns no provider/model selection, retry, fallback, cycles, delay, health,
quarantine, pricing, credential lifecycle, or client-facing policy.

## Wire contract

- endpoint: `POST https://api.anthropic.com/v1/messages`;
- authentication: ephemeral `x-api-key`;
- API version: `anthropic-version: 2023-06-01`;
- exact model from the resolved target;
- bounded `max_tokens` preserved from the characterized baseline;
- optional system instructions plus one user message containing the durable input;
- structured output uses `output_config.format.type=json_schema`;
- no tools or provider-native orchestration are enabled.

## Output and refusal boundary

Only Anthropic content blocks with `type=text` are eligible for canonical output.
Thinking/reasoning blocks are ignored and never persisted as the response.

A native `stop_reason=refusal` fails closed as malformed/refused output.
`stop_reason=max_tokens` is treated as incomplete output. Structured text must parse
as JSON and pass the local durable-schema validator before persistence.

## Usage

Anthropic input accounting is normalized as:

`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.

Cache-read tokens are preserved as non-monetary native usage. Output tokens and the
derived provider total are retained. Cache creation is not double-counted outside the
canonical input total.

Provider pricing, balances and account details remain outside the adapter and outside
client-facing contracts.

## Failure and ambiguity boundary

Authentication/permission, rate limit, timeout, overloaded/server failures and
provider-native error envelopes are normalized without retry. `Retry-After` is
metadata only.

A transport exception after dispatch may have begun becomes
`ProviderDispatchAmbiguousError`; the durable orchestration boundary must terminalize
or reconcile it and must not silently redispatch.

Credentials and provider error messages are never persisted.

## Characterization sources

Read-only characterization used for this port:

- RASAi `AnthropicProvider`;
- RASAi tests covering cache creation/read usage;
- the refusal and structured-output behavior already homologated there.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_anthropic` uses deterministic in-memory fakes only. Ordinary
CI performs no Anthropic network call and incurs no provider cost.
