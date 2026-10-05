# Provider Registry and capability model — #9

## Scope

The Provider Registry is ORQETIA's provider-neutral, read-only resolution boundary for
provider/model/reasoning-profile metadata. It does **not** invoke providers and does not
instantiate adapters.

The registry owns static homologation and resolution facts:

- known provider/model/profile identities;
- provider/model/profile approval and mode eligibility;
- provider-offered capabilities versus ORQETIA-approved capabilities;
- approved defaults used only when the service client omitted model/profile;
- deterministic AUTO and EXPLICIT_TARGET eligibility within a target set already
  authorized by the control plane;
- adapter-resolution metadata (`adapter_key` + protocol version) without concrete SDK
  coupling.

The initial provider taxonomy is inventory only: OpenAI, DeepSeek, Xiaomi MiMo,
xAI/Grok, Alibaba Qwen, Gemini, Anthropic, Mistral, Cohere, Kimi/Moonshot and GitHub
Copilot. Presence in the taxonomy does not imply approval, credentials, contract,
adapter availability or production enablement.

Perplexity and Manus remain outside this registry inventory and continue under their
specific roadmap boundaries.

## Offered is not approved

`offered_capabilities` describes what a provider/model claims or exposes.
`approved_capabilities` is the subset ORQETIA has homologated.

The registry validates that approved capabilities are a subset of offered capabilities.
A capability that is offered but not approved remains ineligible. Unknown or
incompatible provider/model/profile/capability resolution fails closed.

## AUTO

`eligible_targets(mode=AUTO, ...)` receives:

- capability requirements resolved by the caller;
- the target set already authorized for the tenant/client policy snapshot.

It returns only registry-approved and AUTO-eligible provider/model/profile targets in a
deterministic identity order. There is deliberately no pricing parameter or financial
field in this contract: pricing cannot create eligibility. The orchestration policy
engine may apply economic ranking only after registry eligibility has been established.

Dynamic health and quarantine stay in the ExecutionSession runtime boundary and are
applied by the canonical orchestration policy engine. The registry does not duplicate
health ownership.

## EXPLICIT_TARGET

`resolve_explicit_target(...)` preserves an explicitly supplied provider/model/profile.
If model or reasoning profile is omitted, the registry may resolve only a configured,
approved default. An explicitly supplied unknown/incompatible value never falls back to
a different target.

After resolution, the exact target must belong to the target set already authorized by
the control plane. The registry performs that membership check but does not own or
recompute AuthZ policy.

Cost never authorizes substitution of the resolved explicit target.

## Adapter boundary

A resolved target includes an `AdapterResolution` descriptor. This is sufficient for an
application composition layer to select a concrete adapter later without making domain
consumers depend on adapter classes or provider SDKs.

Real adapter implementations, SDK behavior and provider parity remain issue #10 and
later provider-port work.

## Public/read-only metadata

`public_metadata(...)` is authorization-scoped and contains only:

- provider id/display name;
- model id;
- reasoning profile;
- approved capabilities.

It does not contain adapter keys, secrets, API keys, provider accounts, credentials,
balances, provider credits, internal pricing, contracted prices or actual ORQETIA
financial consumption.

No HTTP endpoint is added by #9. The canonical surface is an internal provider-neutral
service contract; a future endpoint requires an explicit API/OpenAPI requirement.

## Determinism and persistence

The registry is immutable in-process metadata. Resolution is deterministic and performs
no network calls, sleeps or paid provider calls. Persistence/control-plane population can
be introduced later without changing the reader contract.

## Tests

`tests/providers/test_registry.py` proves offline:

- known approved provider/model/profile resolution;
- fail-closed unknown/unapproved provider/model/profile behavior;
- offered-versus-approved capability separation;
- approved-only defaults;
- deterministic AUTO eligibility independent of pricing;
- explicit-only target handling and no silent fallback;
- membership in an already-authorized target snapshot;
- dependency on the registry reader contract rather than adapter implementations;
- adapter metadata resolution without adapter instantiation;
- authorization-scoped, non-sensitive public metadata;
- initial taxonomy boundaries.
