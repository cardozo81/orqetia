# Alibaba Qwen Chat Completions adapter

## Scope

Issue #128 establishes the provider-neutral Chat Completions HTTP family and ports
Alibaba Qwen as a thin, single-attempt specialization. The adapter consumes an exact
target resolved upstream and owns no target selection, retry, fallback, cycle, delay,
health, quarantine, pricing, account, or credential-lifecycle policy.

## Wire contract

The characterized default endpoint is the Alibaba Cloud Model Studio Virginia
OpenAI-compatible endpoint:

`POST https://dashscope-us.aliyuncs.com/compatible-mode/v1/chat/completions`

Regional/workspace endpoint selection is a composition concern. The shared adapter
therefore accepts a secure HTTPS endpoint override without putting region into the
provider target domain.

- authentication: ephemeral Bearer credential;
- exact `model` from the resolved target;
- optional instructions become a system message;
- materialized input becomes the user message;
- plain text does not request a response format;
- structured output uses `response_format.type=json_schema`;
- Qwen enables `strict=true` and still validates locally before persistence.

The port does not invent a Qwen thinking/reasoning parameter. Provider/model
capability policy remains in the registry; the exact target identity is preserved in
the durable result.

## Failure and ambiguity boundary

Exactly one HTTP dispatch is permitted. The adapter never sleeps or retries.
`Retry-After` is normalized as metadata only.

Model Studio error codes can be top-level JSON fields, so the Chat Completions family
normalizes bounded top-level `type`/`code` as well as OpenAI-style nested errors.
Messages are never persisted as error evidence.

Qwen-specific distinctions include:

- invalid key/access failures -> `AUTH_FAILURE`;
- `Arrearage` -> `CREDIT_EXHAUSTED`;
- exhausted free-tier policy -> `QUOTA_EXHAUSTED`;
- HTTP 429 throttling, including throughput allocation quota, -> `RATE_LIMITED`;
- 5xx -> transient metadata;
- HTTP 408 -> timeout.

A transport exception after dispatch may have begun raises
`ProviderDispatchAmbiguousError`; orchestration must terminalize/reconcile it and
must not silently redispatch.

## Usage

Chat Completions `prompt_tokens` and `completion_tokens` normalize to canonical
input/output tokens. Cached prompt tokens, reasoning completion tokens, and provider
total tokens are retained as non-monetary native usage when present.

Provider prices, internal cost, balances, accounts, and credits remain outside the
adapter and outside client-facing contracts.

## Characterization

Read-only sources used for this port:

- RASAi `QwenProvider` and its structured-output characterization;
- Alibaba Cloud Model Studio OpenAI-compatible Chat Completions documentation;
- Alibaba Cloud structured-output and error-code documentation.

RASAi is not modified and is not a runtime/build dependency.

## CI

`tests.providers.test_qwen` uses deterministic in-memory fakes only. Ordinary CI
performs no Alibaba/Qwen network call and incurs no provider cost.
