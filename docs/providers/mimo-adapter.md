# Xiaomi MiMo Responses adapter

## Scope

Issue #126 ports Xiaomi MiMo as a provider-neutral, single-attempt ORQETIA
adapter. It consumes an exact target already resolved by the Provider Registry and
does not own selection, retry, fallback, cycles, delay, health, quarantine, pricing,
credential lifecycle, or client-facing policy.

## Wire contract

- endpoint: `POST https://api.xiaomimimo.com/v1/responses`;
- authentication: ephemeral `api-key` credential supplied by composition;
- target model: forwarded exactly from the resolved target;
- reasoning: omitted for provider default, otherwise forwarded through
  `reasoning.effort`;
- text output: Responses `text.format.type=text`;
- structured output: Responses `text.format.type=json_object`;
- the durable JSON Schema is added to the provider instructions as a normative
  constraint and is always enforced again by the local validator;
- OpenAI-adapter defaults such as `service_tier`, `store`, empty `tools`, and
  JSON-Schema `strict` are not injected.

MiMo supports both API-key and Bearer authentication, but this port keeps the
RASAi-characterized PAYG boundary: `api-key`.

## Structured output

MiMo JSON mode guarantees JSON syntax rather than full schema conformance. ORQETIA
therefore parses the returned text and validates the value locally against the
durable schema before persistence. Invalid JSON or schema mismatch becomes
`MALFORMED_OUTPUT` and is not persisted as success.

## Failure and ambiguity boundary

The adapter executes exactly one HTTP request and never sleeps or retries. HTTP 401
and 403 normalize to authentication/permission failure; HTTP 402 represents exhausted
credit/balance; 429 can represent rate limiting or quota exhaustion and is
disambiguated from bounded provider error type/code tokens when available; 5xx is
transient metadata for the orchestrator.

`Retry-After` is metadata only. A transport exception after dispatch may have begun
is raised as `ProviderDispatchAmbiguousError`; the durable runtime boundary must
terminalize or reconcile that ambiguity without silent redispatch.

Provider error messages are not persisted. Credentials are used only in the
in-memory request header and are redacted from representations/evidence.

## Usage

Responses usage is normalized to canonical input/output tokens. Cached input,
reasoning output, and provider total tokens are preserved as non-monetary native
usage when returned. Pricing/cost remains outside the adapter.

## Characterization sources

Read-only characterization used for this port:

- RASAi `ResponsesSemanticProvider` / `MiMoProvider` behavior;
- Xiaomi MiMo official Responses API and structured-output documentation.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_mimo` uses deterministic in-memory fakes only. Ordinary CI
performs no Xiaomi MiMo network call and incurs no provider cost.
