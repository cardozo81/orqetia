# #34 canonical revalidation coverage

This file is the executable traceability map from the canonical matrix to the zero-cost test gate.

Sources:
- historical RASAi baseline: \`c66188e6e3088603b08eb750eece272451d6342c\`;
- ORQETIA evolution: ADR-0020 / #80;
- RASAi PR #201 carry-over source: \`8008a3e24550f7c4b199beb0adeefa9c4e618538\`.

RASAi remains read-only.

## Canonical baseline

| Contract | Evidence |
|---|---|
| CHAR-001 | \`test_char_001_adapter_is_single_attempt\` |
| CHAR-002 | \`test_char_002_provider_is_called_once_per_cycle_even_when_duplicated\` |
| CHAR-003 | \`test_char_003_pool_is_re_evaluated_between_cycles\` |
| CHAR-004 | \`test_char_004_retry_after_is_bounded_and_timer_has_single_effective_delay\` |
| CHAR-005 | \`test_char_005_terminal_error_taxonomy\` |
| CHAR-006 | \`test_char_006_terminal_quarantine_survives_across_needs\` |
| CHAR-007 | \`test_char_007_transient_failure_does_not_terminally_quarantine\` |
| CHAR-008 | \`test_char_008_explicit_provider_is_unitary_pool\` |
| CHAR-009 / CHAR-010 | \`test_char_009_010_unpriced_is_eligible_but_sorts_after_priced\` |
| CHAR-011 | \`test_char_011_each_need_restarts_cycle_budget\` |
| CHAR-012 | \`test_char_012_partial_progress_is_not_lost\` |
| CHAR-013 | \`test_char_013_input_blocked_terminates_immediately\` |
| CHAR-014 | #86/#87 attempt provenance tests: stable attempt identity, retry/fallback distinct attempts |
| CHAR-015 | \`test_char_015_provider_total_wins_and_reasoning_is_not_double_counted\` |
| CHAR-016 | CHAR-009/010 pricing-independent eligibility plus #80 UNPRICED eligibility test |
| CHAR-017 | \`test_char_017_observed_cost_has_precedence_over_estimate\` |
| CHAR-018 | currency-separation and UNPRICED tests |
| CHAR-019 | \`test_char_019_mixed_native_and_token_usage_stays_unpriced_without_contract\` |
| CHAR-020 | \`test_char_020_missing_cache_split_is_fail_safe\` |
| CHAR-021 | \`test_char_021_reasoning_billing_is_catalog_controlled\` |

## ORQETIA #80 evolution

\`tests.contracts.test_execution_target_reference\` proves:
- omitted execution => AUTO;
- lower comparable cost first;
- cheaper COMPLETE prevents higher-cost call;
- no-progress escalates;
- partial preserves accepted/missing and passes only missing forward;
- next cycle re-evaluates ordering;
- UNPRICED remains eligible;
- different currencies use policy comparison-group order rather than implicit FX;
- EXPLICIT_TARGET resolves/fixes provider+model+reasoning profile;
- invalid target is rejected rather than silently falling back;
- explicit retries keep target but create distinct attempt IDs;
- cost change does not change explicit target;
- terminal explicit failure does not cross-target fallback.

## RASAi PR #201 carry-over

### ORQETIA-CARRYOVER-001 / #86

\`tests.contracts.test_attempt_operation_reference\` proves:
- one dispatch has one stable attempt_id;
- exchange/usage/pricing/diagnostic facts use that ID;
- retry of identical payload gets a new attempt_id;
- fallback gets a new attempt_id;
- new records do not guess correlation from metadata;
- ambiguous legacy fingerprint remains unresolved.

### ORQETIA-CARRYOVER-002 / #87

The same suite proves:
- taskless operation is valid only when its operation contract permits it;
- operation is explicit;
- task-required operations reject missing task;
- no generic round requirement is introduced.

### ORQETIA-CARRYOVER-003 / #88

\`tests.contracts.test_exchange_evidence_reference\` proves:
- secret sanitization before evidence persistence;
- hash over sanitized persisted body;
- raw API body equals persisted sanitized body;
- metadata humanization does not rewrite technical tokens;
- transport escaping round-trips;
- provider_id remains domain identity;
- generic status humanizer is not a provider fallback;
- attempt_id remains the evidence correlation key;
- evidence is immutable in the oracle;
- client evidence DTO excludes provider finance/secrets.

## Gate command

The canonical revalidation gate contains **54 deterministic tests**:

- 19 historical canonical characterization tests;
- 11 attempt/taskless carry-over tests;
- 11 exchange evidence/provider identity carry-over tests;
- 13 AUTO/EXPLICIT_TARGET evolution tests.

Run:

\`\`\`bash
python3 -m unittest \
  tests.characterization.test_canonical_contract \
  tests.contracts.test_attempt_operation_reference \
  tests.contracts.test_exchange_evidence_reference \
  tests.contracts.test_execution_target_reference \
  -v
\`\`\`

No network, provider, credential, database or paid resource is required.

#21 tenancy and #12 authorization tests remain separate security/authorization gates; they are not counted in the 54 canonical behavior tests.
