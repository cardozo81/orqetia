# ORQETIA_TEST_PROVIDER — deterministic provider simulator (#19)

## Purpose

`ORQETIA_TEST_PROVIDER` is the official offline provider adapter for orchestration and runtime
tests. It exists so retry, cycle, fallback, partial-progress, quarantine, rate-limit and
cost-selection behavior can be proven without network access, paid credentials or variable
provider behavior.

The simulator is production package code only because later runtime components need a stable
adapter port. It is **not** a real provider integration and is never selected implicitly in
production configuration.

## Single-attempt boundary

The adapter receives one `ProviderAttemptRequest` and returns one normalized
`ProviderAttemptResult`.

The caller owns:

- creation of `attempt_id`;
- candidate selection;
- retry/fallback;
- cycle changes;
- session quarantine;
- task state transitions;
- scheduling/Retry-After delays.

The simulator preserves the supplied `attempt_id` and target. It never manufactures a retry,
fallback target or replacement model.

## Deterministic fixture

Every simulator instance receives an immutable `SimulatorFixture` containing:

- `fixture_id`;
- explicit `seed` metadata;
- optional candidate commercial metadata;
- an ordered tuple of scenario steps.

The seed is traceability metadata, not a source of randomness. There is no implicit PRNG.

Each step declares the expected:

- cycle;
- attempt index;
- optional exact provider/model/reasoning target.

A mismatch raises `SimulatorFixtureMismatch`. Consuming beyond the final step raises
`SimulatorFixtureExhausted`. This makes an unexpected provider call a test failure instead of
silently returning a default.

## Supported scenarios

- success;
- structured success;
- requirement not satisfied;
- partial progress;
- transient error;
- terminal error;
- rate limit;
- Retry-After;
- timeout;
- credit exhausted;
- quota exhausted;
- auth failure;
- malformed output;
- unavailable.

Synthetic latency is metadata only. The adapter never sleeps, so a fixture can represent a
five-second timeout while the test executes immediately.

## Usage and cost metadata

A step can return deterministic:

- input/output token counts;
- provider-native usage units;
- synthetic internal cost amount;
- currency/native unit;
- comparison group.

This information is test input for future orchestration/accounting contracts. #19 does not expose
financial metadata to client APIs and does not implement accounting truth.

## Candidate metadata

`SimulatorCandidate` can represent independent provider/model/reasoning targets with:

- comparison group;
- group rank;
- estimated cost;
- currency/native unit;
- policy rank.

`UNPRICED` is represented by `estimated_cost=None` and comparison group `UNPRICED`.
Absence of price is never represented as zero.

The simulator does not sort candidates. Ranking remains the responsibility of #8 according to
ADR-0020.

## Observability

Every successful fixture invocation appends an immutable in-memory `SimulatorInvocation`
containing safe correlation data:

- fixture/seed;
- invocation index;
- attempt ID;
- operation;
- task/session IDs when present;
- cycle and attempt index;
- target;
- scenario/outcome;
- simulated latency;
- retry-after;
- usage.

No secret, provider credential or real account metadata exists in the adapter.

## EXPLICIT_TARGET proof

To test EXPLICIT_TARGET, configure every fixture step with the same expected target and submit
new attempt IDs for retries/cycles. Any cross-target request fails the fixture expectation. This
proves the adapter executes exactly what the caller selected without importing orchestration
logic into the provider.

## Non-goals

- Provider Registry/capability discovery (#9);
- real provider SDK/HTTP calls;
- candidate ranking;
- retry/fallback/cycle policy (#8);
- worker delay scheduling (#15);
- persistence of provider attempts/exchanges;
- pricing/accounting truth;
- paid resources;
- RASAi modification.
