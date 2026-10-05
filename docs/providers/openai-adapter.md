# OpenAI Responses adapter — #124

## Boundary

The OpenAI adapter is ORQETIA's first concrete provider port and establishes the
single-attempt HTTP baseline for the Responses-family providers.

It consumes an exact `ProviderTarget` already resolved by the Provider Registry. It
does not select provider/model/reasoning profile and owns no retry, fallback, cycle,
health, quarantine, pricing or billing policy.

The adapter uses the canonical OpenAI Responses endpoint:

`POST https://api.openai.com/v1/responses`

The provider request is executed once. Any transport exception whose external effect
cannot be proven is raised as `ProviderDispatchAmbiguousError`. The existing durable
`ProviderAttemptHandler` terminalizes such exceptions as an ambiguous attempt and does
not silently redispatch them.

## Request materialization

Durable queue and attempt records continue carrying only
`request_reference` + `request_fingerprint`. The concrete adapter does not read
application tables or a secret store directly.

Composition injects:

- `ProviderRequestPayloadReader` to materialize the already-authorized payload;
- `ProviderResponsePayloadWriter` to persist sanitized output and return a durable
  response reference;
- `ProviderCredential` as an ephemeral in-memory secret value;
- `StructuredOutputValidator` when a structured-output contract is requested;
- `ProviderHttpTransport`, defaulting to `HttpxProviderHttpTransport`.

This keeps Backoffice/credential ownership outside the adapter and avoids introducing a
database or secret-management dependency before those control-plane issues are
implemented.

## Wire contract

The adapter forwards the exact model and approved reasoning profile received in the
target. It does not substitute a cheaper model or default target.

The Responses request uses provider-native fields:

- `model`;
- `instructions` when present;
- `input` with one user `input_text` item;
- `reasoning.effort` when the resolved target specifies an explicit profile;
- `text.format.type=json_schema` with `strict=true` for structured output;
- `text.format.type=text` otherwise;
- `service_tier=default`;
- `store=false`;
- an empty tools list.

The empty tools list is intentional. Tool execution is not part of the #124 homologated
capability surface.

OpenAI's current Responses contract exposes output items and usage including
`input_tokens`, `output_tokens`, cached-input details and reasoning-token details.
ORQETIA normalizes those fields without attaching pricing.

## Structured output

Provider-side strict JSON Schema does not replace local validation.

For structured requests, ORQETIA:

1. requires a local `StructuredOutputValidator` before dispatch;
2. requests strict JSON Schema output;
3. decodes the returned output text as JSON;
4. validates the decoded value locally against the original schema;
5. persists only the validated canonical JSON form.

Missing validator, invalid JSON or local schema failure fails closed. No response is
persisted as a successful structured result in those cases.

## Error normalization

Known HTTP/provider outcomes are normalized provider-neutrally:

| Provider signal | ORQETIA outcome |
|---|---|
| 401 / 403 | `AUTH_FAILURE` |
| 402 or explicit credit/balance exhaustion | `CREDIT_EXHAUSTED` |
| explicit quota exhaustion | `QUOTA_EXHAUSTED` |
| 429 | `RATE_LIMITED` |
| 408 | `TIMEOUT` |
| 5xx | `TRANSIENT_ERROR` |
| other known non-2xx contract failures | `TERMINAL_ERROR` |
| malformed/incomplete successful response | `MALFORMED_OUTPUT` |

`Retry-After` integer seconds are captured as metadata only. The adapter never sleeps
or retries.

Transport failures without a trustworthy provider outcome are not converted into a
retryable provider result. They become `ProviderDispatchAmbiguousError` so the durable
attempt journal can terminalize the uncertainty.

## Secrets and response evidence

The provider credential is redacted from `repr`/string conversion and is used only to
construct the in-memory Authorization header. It is not placed in:

- `ProviderAttemptRequest`;
- `ProviderAttemptResult`;
- response references;
- normalized error classes;
- durable output content;
- provider contract fixtures.

Error response messages/bodies are not persisted. Only bounded/sanitized error
type/code tokens are used for classification.

## Latency

The existing `ProviderAttemptResult.simulated_latency_ms` storage field originated with
the deterministic simulator. #124 adds the canonical read alias `latency_ms`, and
runtime telemetry now consumes that alias. This preserves backward compatibility while
removing simulator terminology from new provider consumers.

## Validation and CI

`tests/providers/test_openai.py` uses only deterministic fake payload, response writer,
validator and HTTP transport ports. It covers:

- text success and exact target forwarding;
- strict structured output and local validation;
- invalid structured output;
- malformed/incomplete response;
- auth/permission, rate limit, quota, credit, timeout and server errors;
- Retry-After metadata without retry;
- usage normalization;
- target mismatch;
- request materialization failure before network dispatch;
- transport ambiguity propagation;
- single-attempt call count;
- credential redaction and absence from persisted evidence.

Normal CI never invokes OpenAI or any other paid provider.

## Characterization sources

The adapter was characterized against the current read-only RASAi implementation at
`095d7e4674968703259596497dc0ded849de3bad` and the current OpenAI Responses API
contract. RASAi remains a requirements/reference source only; ORQETIA has no runtime or
build dependency on it.
