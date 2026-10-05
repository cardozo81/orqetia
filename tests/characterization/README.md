# Canonical characterization tests (#34)

These tests freeze behavior characterized from the read-only RASAi baseline documented in:

`docs/architecture/rasai-canonical-characterization-matrix.md`

## Purpose

This directory is a **test oracle**, not production implementation.

It exists before the ORQETIA stack ADR so that future Python/TypeScript/other implementations can be checked against stable behavior instead of reinterpreting the RASAi runtime.

## Run

```bash
python -m unittest tests.characterization.test_canonical_contract -v
```

No network, credentials, provider calls, database or paid resources are required.

## Rules for future ports

A port of orchestration/routing/accounting semantics must:

1. identify affected `CHAR-xxx` contracts;
2. preserve the applicable oracle cases or replace them with equivalent production contract tests;
3. never import `tests.characterization.reference_contract` from production code;
4. use an ADR before intentionally changing a `CANONICAL_PRESERVE` behavior.
