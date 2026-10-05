# GitHub Copilot SDK adapter

## Scope

Issue #134 ports GitHub Copilot through the official Copilot SDK boundary. Unlike
the HTTP provider families, Copilot is isolated behind an injected SDK transport and
still obeys the same ORQETIA single-attempt contract.

The adapter consumes the exact target already resolved by the Provider Registry and
owns no target selection, retry, fallback, cycles, delay, health, quarantine, pricing,
credential lifecycle, or client-facing policy.

## Authentication and SDK isolation

Provider credentials are supplied explicitly by secure composition. The production
transport configures the SDK with:

- the explicit GitHub token;
- `use_logged_in_user=false`;
- `available_tools=[]` on every created session.

This forbids silent fallback to Copilot CLI/GitHub-login credentials and adds an
explicit no-tools boundary.

The official SDK is loaded lazily at runtime. It is not an ordinary CI dependency,
so importing ORQETIA does not require a Copilot installation and the deterministic
test suite performs no external/provider call.

## Request projection

Materialized ORQETIA input, optional instructions, and optional structured-output
schema are serialized as inert JSON inside one evidence-bound prompt. The prompt
explicitly forbids browsing, files, commands, tools, and invented external evidence.

The exact resolved model is passed separately to the SDK session and is not selected
inside the adapter.

## Structured output

The characterized SDK has no HTTP wire-schema field equivalent. For structured
requests, the durable schema is included in the inert prompt and remains authoritative
for local validation.

Returned assistant text may contain a JSON code fence; that fence is removed only at
the structured-output parsing boundary. JSON must then pass the local validator
before canonical JSON is persisted.

## Usage and cost

The characterized SDK response does not provide a stable token-usage contract at this
boundary. ORQETIA therefore records zero/unknown canonical usage rather than inventing
tokens or cost.

Provider subscription details, balances, internal cost, pricing and account metadata
remain outside this adapter and outside client-facing contracts.

## Failure and ambiguity boundary

Known explicit credential, rate-limit and timeout errors normalize to canonical
outcomes. SDK/runtime unavailability before model dispatch is terminal configuration
failure.

Unknown errors after `send_and_wait` begins are ambiguous external effects and raise
`ProviderDispatchAmbiguousError`; ORQETIA must not silently redispatch them.

The adapter performs exactly one SDK send. Session/client cleanup is always attempted.

## Characterization sources

Read-only characterization used for this port:

- RASAi `GitHubCopilotProvider`;
- RASAi tests of the official Python SDK call shape;
- RASAi optional `github-copilot-sdk>=1,<2` integration boundary.

RASAi remains read-only and is not a runtime/build dependency.

## CI

`tests.providers.test_copilot` uses an injected deterministic transport and a
synthetic in-memory `copilot` module for the concrete SDK-shape test. Ordinary CI
uses no GitHub Copilot subscription, token, network request, or provider-paid call.
