# Canonical characterization tests (#34)

These tests freeze the historical RASAi canonical baseline and the explicitly approved ORQETIA generalizations/carry-overs documented in:

`docs/architecture/rasai-canonical-characterization-matrix.md`

Traceability is maintained in:

`tests/characterization/REVALIDATION_COVERAGE.md`

## Purpose

This directory and the referenced `tests/contracts` modules are **test oracles**, not production implementation.

They let future production ports be checked against stable behavior instead of reinterpreting RASAi or silently changing ORQETIA contracts.

## Canonical revalidation gate

The gate contains 54 deterministic tests:

- 19 historical canonical tests;
- 11 attempt/taskless carry-over tests (#86/#87);
- 11 exchange evidence/provider identity tests (#88);
- 13 AUTO/EXPLICIT_TARGET tests (#80).

Run:

```bash
python3 -m unittest \
  tests.characterization.test_canonical_contract \
  tests.contracts.test_attempt_operation_reference \
  tests.contracts.test_exchange_evidence_reference \
  tests.contracts.test_execution_target_reference \
  -v
```

No network, credentials, provider calls, database or paid resources are required.

#21 tenancy and #12 authorization suites remain separate security gates.

## Rules for future ports

A port of orchestration/routing/accounting semantics must:

1. identify affected `CHAR-xxx`, #80 or PR #201 carry-over contracts;
2. preserve the applicable oracle cases or replace them with equivalent production contract tests;
3. never import test oracle modules from production code;
4. use an ADR before intentionally changing a `CANONICAL_PRESERVE` behavior;
5. keep RASAi read-only and avoid runtime/build dependency on it.
