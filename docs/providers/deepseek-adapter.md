# DeepSeek Responses adapter

## Scope

Issue #125 ports DeepSeek as a provider-neutral, single-attempt ORQETIA adapter.
The adapter consumes an exact target already resolved by the Provider Registry and
does not own selection, retry, fallback, cycles, delay, health, quarantine, pricing,
credentials lifecycle, or client-facing policy.

## Wire contract

- endpoint: `POST https://api.deepseek.com/responses`;
- authentication: ephemeral Bearer credential supplied by composition;
- target model: forwarded exactly from the resolved target;
- reasoning: omitted for provider default, otherwise forwarded through the Responses
  `reasoning.effort` field;
- text output: Responses `text.format.type=text`;
- structured output: Responses `text.format.type=json_schema`, with name and schema;
- OpenAI-only `strict`, `service_tier`, `store`, and empty `tools` defaults are not
  injected into DeepSeek requests.

The shared `ResponsesHttpAdapter` owns protocol mechanics only. Provider-specific
wire toggles stay in the thin provider adapter.

## Structured output

Structured output fails closed unless a local `StructuredOutputValidator` is
available. Provider-returned JSON is parsed and validated locally against the durable
schema before canonical JSON is persisted. Invalid JSON or schema mismatch is
`MALFORMED_OUTPUT` and is never persisted as a successful response.

## Failure and ambiguity boundary

The adapter executes exactly one HTTP request. It never sleeps or retries. HTTP and
provider failures are normalized to ORQETIA outcomes. `Retry-After` is metadata only.

A transport exception after dispatch may have begun is raised as
`ProviderDispatchAmbiguousError`; the orchestration/runtime durability boundary must
terminalize or reconcile that ambiguity and must not silently redispatch it.

Provider error message bodies are not persisted. Only bounded type/code tokens can
enter normalized error metadata.

## Usage

Responses usage is normalized to canonical input/output tokens. When returned, cached
input tokens, reasoning output tokens, and provider total tokens are preserved as
non-monetary native usage.

Provider pricing/cost is intentionally absent from this adapter.

## Characterization sources

Read-only characterization used for this port:

- RASAi `ResponsesSemanticProvider` / `DeepSeekProvider` behavior;
- DeepSeek official Responses API documentation, including `/responses`,
  `text.format=json_schema`, Responses usage fields, and reasoning effort.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_deepseek` uses only deterministic in-memory fakes. Ordinary CI
performs no DeepSeek network call and incurs no provider cost.
