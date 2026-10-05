# Incremental provider adapter parity — #10

## Scope

Issue #10 defines a machine-verifiable migration strategy for provider adapters. It does
not port a real provider, add a provider SDK, configure credentials or perform a network
call.

The canonical runtime boundary remains `ProviderAdapter.invoke()`: one durable provider
attempt in, one normalized attempt result out. Retry, fallback, cycles, delay,
cancellation and target selection remain orchestration responsibilities.

## Read-only characterization source

The migration matrix was characterized from the current read-only RASAi reference:

- repository: `cardozo81/RASAI-Readiness-Auditor`;
- source SHA: `095d7e4674968703259596497dc0ded849de3bad`;
- `docs/COMPATIBILITY.md`;
- `docs/PROVIDER_REGISTRY.md`;
- `docs/AI_PROVIDER_EXTENSIONS.md`;
- `src/rasai/openai_provider.py`;
- `src/rasai/provider_wire_schema.py`;
- `src/rasai/copilot_provider.py`.

This is a characterization input only. ORQETIA has no runtime/build dependency on RASAi,
and no RASAi file or issue is mutated by this work.

## Migration order

The order groups compatible transport families to maximize reuse before introducing
provider-specific wire variants. It is **not** AUTO ranking, economic preference,
provider approval or production enablement.

| Order | Provider | Characterized transport family | Migration intent |
|---:|---|---|---|
| 1 | OpenAI | Responses HTTP | establish the first canonical HTTP adapter baseline |
| 2 | DeepSeek | Responses HTTP | reuse the proven Responses transport boundary |
| 3 | Xiaomi MiMo | Responses HTTP | reuse Responses without inheriting unsupported provider modalities |
| 4 | xAI / Grok | Responses HTTP | finish the characterized Responses family |
| 5 | Alibaba Qwen | Chat Completions HTTP | establish the generic chat-completions projection |
| 6 | Mistral | Chat Completions HTTP | reuse the chat family while preserving provider-specific request constraints |
| 7 | Kimi / Moonshot | Chat Completions HTTP | reuse the chat family with strict structured-output characterization |
| 8 | Gemini | Gemini Interactions HTTP | add the first provider-native HTTP projection |
| 9 | Anthropic | Anthropic Messages HTTP | add the Messages-specific projection |
| 10 | Cohere | Cohere Chat V2 HTTP | add the Chat V2-specific projection |
| 11 | GitHub Copilot | Copilot SDK | isolate the exceptional SDK transport after HTTP contracts are stable |

Each row is implemented later as its own small provider-port issue. A provider port must
not opportunistically implement the next row.

## Mandatory parity gates

Every provider port must satisfy the complete
`MANDATORY_ADAPTER_PARITY_GATES` contract:

1. `SINGLE_ATTEMPT` — one adapter invocation represents exactly one logical provider
   attempt.
2. `STRUCTURED_OUTPUT` — supported structured-output behavior is projected explicitly
   and validated locally; unsupported provider features are not inferred.
3. `ERROR_NORMALIZATION` — provider-specific failures are mapped into ORQETIA's
   provider-neutral outcomes without leaking provider response bodies.
4. `USAGE_NORMALIZATION` — native usage is preserved while the normalized usage
   contract remains stable.
5. `CAPABILITY_ALIGNMENT` — adapter behavior cannot exceed capabilities approved by
   the Provider Registry.
6. `NO_RETRY_ORCHESTRATION` — no retry, fallback, cycle scheduling or durable sleep
   inside the adapter.
7. `NO_SECRET_CAPTURE` — credentials never enter request/result evidence, logs,
   fixtures or sanitized exchange data.
8. `OFFLINE_CONTRACT_TESTS` — normal automated validation uses fake/mock transport or
   deterministic SDK doubles only.
9. `AMBIGUOUS_EFFECT_BOUNDARY` — ambiguous external outcomes are returned to the
   durable attempt boundary and never redispatched silently by the adapter.
10. `NO_PAID_CI` — ordinary CI never invokes a paid provider.

A port is not homologated when any mandatory gate is missing.

## Fixture strategy

HTTP adapters should inject a transport seam and use deterministic fixtures covering:

- successful text/structured output;
- malformed or incomplete output;
- authentication/authorization failure;
- rate limiting and retry metadata;
- quota/credit exhaustion when the provider exposes it;
- timeout/network ambiguity;
- native usage normalization;
- provider-specific response-envelope extraction.

SDK adapters must use an equivalent deterministic SDK double. A test may prove that
tools or implicit local credentials are disabled when those restrictions are part of the
homologated adapter contract, but it must not require a real SDK session or subscription.

## Relationship with Provider Registry

The Registry decides whether provider/model/profile/capabilities are known, approved and
eligible. The adapter executes only the exact resolved target it receives.

Therefore:

- pricing does not make an adapter eligible;
- adapters do not select targets;
- adapters do not read the full provider inventory;
- adapters do not own health/quarantine;
- adapters do not contain provider-account balances or internal financial data;
- adapter resolution metadata from #9 is the composition seam for a future concrete
  implementation.

## Provider-specific characterization boundary

The RASAi source shows multiple transport families, not one universal wire format.
ORQETIA must preserve that distinction instead of forcing every provider through an
OpenAI-shaped payload.

Characterized examples include Responses-based providers, OpenAI-compatible/general
chat-completions providers, Gemini Interactions, Anthropic Messages, Cohere Chat V2 and
GitHub Copilot SDK. Provider-specific credential formats, commercial plans, unsupported
tools/search features and RASAi-local configuration conventions are **not** inherited
automatically.

## CI

`tests/providers/test_parity.py` validates the plan offline. The existing provider
workflow also runs registry and parity contract tests together with
`ORQETIA_TEST_PROVIDER`.

No real provider smoke is required by #10. A future real smoke is manual, explicit and
outside ordinary CI.
