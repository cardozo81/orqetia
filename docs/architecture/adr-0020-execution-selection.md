# ADR-0020 — Execution selection: AUTO cost escalation vs EXPLICIT_TARGET

- Status: **Accepted**
- Issue: #80
- Depends on: revalidated #21 and #12
- Historical canonical baseline: accepted #31/#34 snapshot before current revalidation
- Downstream revalidation: #31/#34 must be closed again after this ADR/tests
- Related: ADR-0018 attempt identity, ADR-0007 tenancy, ADR-0009 authorization

## Decision register

| Decision | Classification |
|---|---|
| Omitted execution target means AUTO | PUBLIC_API_CONTRACT |
| AUTO eligibility is independent of pricing | ORCHESTRATION_CORE |
| Comparable candidates are tried lower estimated provider cost first | ORCHESTRATION_CORE |
| UNPRICED/non-comparable candidates remain eligible | ORCHESTRATION_CORE |
| No implicit FX/native-unit conversion | ORCHESTRATION_CORE |
| EXPLICIT_TARGET resolves and freezes provider/model/reasoning profile | PUBLIC_API_CONTRACT + ORCHESTRATION_CORE |
| Explicit target never performs cross-target fallback because of cost/failure | ORCHESTRATION_CORE |
| Cycles/retry/Retry-After/timeout/quarantine remain ORQETIA-owned | ORCHESTRATION_CORE |
| Provider cost remains internal for both modes | PERSISTENCE_CONTRACT / CLIENT_SPECIFIC_NOT_APPLICABLE to client billing |

## Separation of concerns

Execution selection decides the candidate target space.

Orchestration owns:
- attempts;
- cycle budget;
- Retry-After;
- delay;
- timeout;
- response validation;
- partial accepted/missing state;
- terminal/transient handling;
- session health/quarantine;
- cancellation.

A client selecting a target does not become owner of orchestration behavior.

## Request model

Omitted field:

    {}

is equivalent to:

    {
      "execution": {
        "mode": "AUTO"
      }
    }

Explicit request:

    {
      "execution": {
        "mode": "EXPLICIT_TARGET",
        "target": {
          "provider": "OPENAI",
          "model": "optional-approved-model",
          "reasoning_profile": "optional-approved-profile"
        }
      }
    }

The canonical schema is:
- \`contracts/execution/task-execution-selection.schema.json\`.

Unknown fields are rejected by the API contract.

## AUTO

AUTO is the default.

### Eligibility

Candidate admission requires all applicable runtime/control conditions:

    integrated
    AND configured
    AND capability-compatible
    AND client-authorized
    AND model/profile effective
    AND policy eligible
    AND session-health eligible

Pricing does **not** decide eligibility.

Missing price cannot remove a valid provider.

### Economic comparison groups

Cost ordering is valid only inside an approved comparison group.

A comparison group represents a commercial basis where numeric estimates can be compared directly, for example:
- MONEY:USD;
- MONEY:EUR;
- a future explicitly defined native-credit group.

Within one directly comparable priced group:
1. lower estimated provider cost first;
2. deterministic policy/provider rank breaks ties.

Between groups that are not directly comparable:
- use an administrative deterministic group rank;
- do not claim the lower-ranked group is economically cheaper;
- do not invent FX;
- do not convert native credits/units to money without an explicit pricing contract.

UNPRICED is a valid group/state and never equals zero.

### Cost escalation inside a cycle

Within each cycle:
1. build the eligible candidate pool for the current missing requirement;
2. rank candidates;
3. invoke each candidate at most once in that cycle;
4. stop immediately on COMPLETE;
5. preserve accepted progress on PARTIAL_PROGRESS;
6. continue only the missing requirement;
7. transient/no-progress may move to the next candidate;
8. terminal failure applies canonical quarantine/exclusion and AUTO may continue to the next eligible candidate.

Therefore a higher-cost comparable candidate is not called when a cheaper candidate already satisfied the requirement.

### New cycle

A new cycle re-evaluates:
- eligibility;
- health/quarantine;
- remaining requirement;
- pricing estimate/comparison metadata;
- ordering.

The ordering is not frozen for the task/session.

## EXPLICIT_TARGET

EXPLICIT_TARGET is optional and requires #12/#21 authorization.

### Resolution

Provider is required.

If model is omitted:
- resolve the server-authorized default model for the requested provider.

If reasoning/profile is omitted:
- resolve the server-authorized default profile for the provider/model.

If any combination is unavailable, incompatible or unauthorized:
- reject before provider dispatch;
- do not silently replace it;
- do not reinterpret the request as AUTO.

### Frozen effective target

After successful resolution, persist the effective target:

    provider + model + reasoning_profile

The target becomes a hard constraint for the task.

Later default/catalog changes do not rewrite an existing task.

Emergency administrative revocation may stop future attempts, but it does not choose a replacement provider/model/profile.

### Explicit execution pool

The orchestration candidate pool contains exactly one effective target.

Across normal retries/cycles:
- provider stays the same;
- model stays the same;
- reasoning/profile stays the same;
- each new provider dispatch creates a new \`attempt_id\` per ADR-0018.

Cost is still estimated/accounted internally, but does not influence selection.

### Explicit failure behavior

Transient/no-progress:
- may consume retry/cycle policy on the same target.

Partial progress:
- preserves accepted/missing;
- future attempts remain on the same target.

Terminal provider/model/auth/quota error:
- applies terminal policy/quarantine;
- does **not** authorize another provider/model/profile;
- task ends according to canonical orchestration state when the unitary target is no longer eligible.

## Target and policy persistence

Task-level:
- effective_policy_version_id;
- requested_execution_mode;
- requested target fields;
- fully resolved effective target fields for EXPLICIT_TARGET;
- target_resolution_reason/status.

AUTO:
- no fixed task-level effective provider/model/profile;
- each \`attempt_id\` persists actual provider/model/profile;
- candidate ordering/provenance is recorded per cycle/decision where needed.

EXPLICIT_TARGET:
- fixed effective target persists before first provider dispatch.

## Pricing provenance

For AUTO ranking, preserve enough internal metadata to explain the decision:
- candidate provider/model/profile;
- pricing catalog/rule/version;
- estimated amount when priced;
- currency/native basis;
- comparison group;
- comparison group rank;
- policy/tie-break rank;
- cycle;
- remaining requirement context.

Client-facing APIs do not expose provider cost, currency/rates, balances or commercial terms in this phase.

## Attempt provenance

Every provider dispatch remains a separate attempt.

Normal retry/fallback rules:
- retry same target => new attempt_id;
- AUTO fallback candidate => new attempt_id;
- explicit retries => new attempt_id but identical effective target.

Request fingerprint may remain identical and is not identity.

## API authorization

AUTO task creation:
- base task-write authorization.

EXPLICIT_TARGET:
- base task-write authorization;
- \`tasks:target\`;
- server-side provider/model/profile entitlement;
- normal tenant/client ownership;
- quota/rate/policy checks.

The request cannot set max cycles/retry/delay/timeout/quota.

## Error contract

Exact HTTP envelope is defined by #4.

Semantic categories:
- malformed/unsupported execution shape -> validation error;
- explicit target permission missing -> forbidden;
- target combination invalid/unavailable -> safe target-resolution error before dispatch;
- AUTO has no eligible target -> orchestration unavailable according to task contract.

Error details must not enumerate hidden providers/models belonging outside the client's authorized envelope.

## Test contract

Deterministic tests prove:
1. omitted execution => AUTO;
2. AUTO lower comparable estimated cost first;
3. cheaper COMPLETE prevents more expensive call;
4. failure/no-progress escalates to next candidate in same cycle;
5. partial preserves accepted and next candidate receives only missing requirement;
6. new cycle re-evaluates cost ordering;
7. UNPRICED remains eligible and never treated as zero;
8. different currencies/native groups use policy group order, not implicit FX;
9. EXPLICIT_TARGET resolves approved defaults and freezes the target;
10. explicit retries/cycles never change provider/model/profile;
11. cost changes do not alter explicit target;
12. terminal explicit failure does not cross-target fallback;
13. each normal explicit retry creates a new attempt_id;
14. unauthorized explicit target is rejected before dispatch via #12/#21 contracts.

## Deliberately unchanged

This ADR does not change:
- adapter single-attempt ownership;
- one provider per cycle rule;
- canonical Retry-After/timer ownership;
- terminal/transient taxonomy;
- session quarantine;
- pricing formulas;
- usage/accounting semantics;
- provider adapters;
- RASAi consumer-domain logic.

## Consequences

Positive:
- AUTO optimizes cost only when comparison is legitimate;
- service clients can intentionally fix an approved target;
- explicit requests cannot unexpectedly fall back to another provider/model/profile;
- canonical retry/partial/quarantine remain centralized.

Costs:
- control plane needs target-entitlement/default metadata;
- AUTO decision provenance needs comparison-group metadata;
- cross-currency candidates cannot be globally sorted by a fabricated scalar.
