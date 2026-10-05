# Mistral Chat Completions adapter

## Scope

Issue #129 ports Mistral as a provider-neutral, single-attempt ORQETIA adapter.
It consumes an exact target already resolved by the Provider Registry and owns no
selection, retry, fallback, cycles, delay, health, quarantine, pricing, credential
lifecycle, or client-facing policy.

## Wire contract

- endpoint: `POST https://api.mistral.ai/v1/chat/completions`;
- authentication: ephemeral Bearer credential supplied by composition;
- target model: forwarded exactly from the resolved target;
- `service_tier=standard_only` is always sent, preserving the characterized
  Standard-service constraint;
- text requests use canonical Chat Completions messages;
- structured output uses strict JSON Schema and is validated locally again before
  persistence;
- no provider tools or provider-specific orchestration are enabled.

The shared `ChatCompletionsHttpAdapter` owns protocol mechanics. The optional
service-tier field defaults to absent, so Qwen and future providers are unchanged
unless their thin specialization explicitly configures it.

## Failure and ambiguity boundary

Exactly one HTTP request is executed. The adapter never sleeps or retries.
Authentication/permission, timeout, rate-limit and server failures are normalized to
ORQETIA outcomes. `Retry-After` is metadata only.

A transport exception after dispatch may have begun becomes
`ProviderDispatchAmbiguousError`; the durable orchestration boundary must terminalize
or reconcile the ambiguity and must not silently redispatch.

Provider error messages are never persisted. Credentials are used only in the
in-memory request header and remain redacted from durable evidence.

## Usage and cost boundary

Chat Completions token usage is normalized to canonical input/output tokens and
non-monetary native usage where available. Provider pricing, balances and internal
cost remain outside the adapter and outside client-facing contracts.

## Characterization sources

Read-only characterization used for this port:

- RASAi `MistralProvider`, a Qwen-family Chat Completions specialization;
- RASAi tests preserving `service_tier=standard_only` and strict structured output.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_mistral` uses deterministic in-memory fakes only. Ordinary CI
performs no Mistral network call and incurs no provider cost.
