# Canonical characterization tests

These tests are a **test-only semantic oracle** for ORQETIA. Production code must not import from this directory or from \`tests/contracts\`.

## Sources

Historical canonical baseline:
- \`cardozo81/RASAI-Readiness-Auditor@c66188e6e3088603b08eb750eece272451d6342c\`.

Post-baseline read-only requirements source:
- RASAi PR #201;
- RASAi main \`8008a3e24550f7c4b199beb0adeefa9c4e618538\`.

## Gate layers

### Baseline canonical characterization

\`test_canonical_contract.py\` freezes the original orchestration/accounting invariants from #31.

### ORQETIA execution-target evolution

\`tests/contracts/test_execution_target_reference.py\` proves ADR-0020/#80:
- target omitted => AUTO;
- comparable lower cost first;
- escalation only when needed;
- partial keeps accepted/missing;
- ordering recalculated by cycle;
- UNPRICED remains eligible;
- no implicit FX;
- explicit provider/model/profile remains frozen across normal retries;
- terminal explicit failure does not cross-target fallback.

### Authorization / tenancy

- \`tests/contracts/test_tenancy_target_reference.py\`;
- \`tests/contracts/test_authz_target_reference.py\`.

These prove the client-selectable target cannot widen tenant/provider/model/profile entitlement or orchestration limits.

### RASAi PR #201 carry-over

- \`tests/contracts/test_attempt_operation_reference.py\` — #86/#87;
- \`tests/contracts/test_exchange_evidence_reference.py\` — #88.

They prove:
- stable \`attempt_id\`;
- retry/fallback distinct attempt identities;
- taskless operation with explicit operation;
- evidence correlation by attempt ID;
- sanitized raw evidence integrity;
- typed provider identity.

### Cross-contract revalidation

\`test_revalidation_contracts.py\` proves the contracts compose correctly:
- explicit retry keeps target while attempt identity changes;
- AUTO fallback uses distinct attempts;
- attempt/exchange/usage/pricing correlation is stable;
- sanitized evidence exposes the persisted attempt identity and raw semantic token;
- taskless execution stays governed/correlated.

## Mapping to #31

The current canonical matrix in \`docs/architecture/rasai-canonical-characterization-matrix.md\` is authoritative.

A semantic change to a CANONICAL_PRESERVE row requires an ADR before implementation.

ADR-0020 is the approved evolution for the richer ORQETIA execution-target contract.

## Cost / isolation

All tests:
- use stdlib/fakes only;
- make no network/provider call;
- need no credential;
- need no database;
- incur no variable provider cost.
