# xAI / Grok Responses adapter

## Scope

Issue #127 ports xAI / Grok as a provider-neutral, single-attempt ORQETIA
adapter. The adapter consumes an exact target already resolved by the Provider
Registry and owns no selection, retry, fallback, cycle, delay, health, quarantine,
pricing, credential lifecycle, or client-facing policy.

## Wire contract

- endpoint: `POST https://api.x.ai/v1/responses`;
- authentication: ephemeral Bearer credential supplied by composition;
- target model: forwarded exactly from the resolved target;
- reasoning: omitted for provider default, otherwise forwarded through
  `reasoning.effort`;
- structured output: Responses JSON Schema with strict adherence;
- `service_tier=default` is explicit so this adapter never opts into premium
  priority processing;
- `store=false` is explicit because xAI Responses otherwise stores responses by
  default;
- no provider tools are enabled by this port.

## Structured output

The durable JSON Schema is sent through the Responses structured-output wire and the
returned JSON is parsed and validated locally again before persistence. Invalid JSON
or local schema mismatch becomes `MALFORMED_OUTPUT` and is not persisted as success.

## Failure and ambiguity boundary

Exactly one HTTP request is executed. The adapter never sleeps or retries.
Authentication/permission is normalized from HTTP status and bounded provider error
tokens; this includes the xAI-documented invalid-key case that may arrive as HTTP 400.
HTTP 429 is rate-limit metadata, 5xx is transient metadata, and `Retry-After` never
causes adapter-owned delay.

A transport exception after dispatch may have begun is
`ProviderDispatchAmbiguousError`; the durable runtime/orchestration boundary must
terminalize or reconcile it without silent redispatch.

Provider error messages are not persisted. Credential values exist only in the
in-memory Authorization header and are redacted from evidence/representations.

## Usage and cost boundary

Canonical input/output tokens are normalized from Responses usage. Cached input,
reasoning output, and provider total tokens are retained only as non-monetary native
usage. xAI cost fields, pricing, credits, and account balances remain outside this
adapter and client-facing contracts.

## Characterization sources

Read-only characterization used for this port:

- RASAi `XAIProvider` behavior;
- xAI official Responses, reasoning, structured-output, authentication, priority
  processing, and debugging documentation.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_xai` is fully deterministic and uses only in-memory fakes.
Ordinary CI performs no xAI network call and incurs no provider cost.
