# ADR-0043 — Estimate accuracy, calibration, drift and benchmark rebuild

- Status: **Accepted**
- Issue: #157
- Related: #5, #11, #45
- Lifecycle: **DEVELOPMENT**

## Decision

Benchmark availability and benchmark quality are separate concerns.

#11 continues to decide whether a CLIENT_ONLY or GLOBAL_PUBLIC snapshot can be
built from a privacy-safe cohort. #157 adds a deterministic quality gate that
decides whether an existing immutable snapshot is still safe to serve.

The quality gate does not call a provider and does not inspect raw prompts or
outputs.

## Quality dimensions

A snapshot is assessed by:

- methodology version;
- snapshot age from `as_of`;
- confidence;
- drift versus the previous snapshot of the same scope/feature key;
- calibration observations produced while that exact `benchmark_version` was
  active.

Calibration compares estimated and observed **technical total tokens** only.
Provider cost, currency, client_charge and raw content are outside this contract.

## Accuracy metrics

For each calibration observation:

`absolute_error_ratio = abs(estimated - observed) / max(observed, 1)`

The gate computes:

- median absolute error ratio;
- p90 absolute error ratio;
- signed aggregate bias ratio.

The denominator guard avoids division by zero but does not pretend a zero-token
observation has ordinary percentage semantics.

Default M9 thresholds are methodology-controlled:

- minimum 20 calibration observations;
- minimum serving confidence MEDIUM;
- maximum snapshot age 7 days;
- median absolute error ratio <= 0.25;
- p90 absolute error ratio <= 0.50;
- absolute bias ratio <= 0.20;
- drift ratio <= 0.30.

Changing thresholds requires a new methodology version or an explicit policy
change. They are not client-controlled request parameters.

## Drift

Drift is the absolute movement of median total tokens versus the previous
snapshot of the same scope and feature key:

`abs(current_median - previous_median) / max(previous_median, 1)`

Snapshots from another feature key/scope cannot be used as drift baselines.

## Calibration provenance

Only observations whose `benchmark_version` exactly matches the snapshot under
assessment and whose timestamps are between snapshot `as_of` and assessment
time are admitted.

This prevents post-hoc observations from a different benchmark generation from
being mixed into the current quality score.

## Staleness and rebuild

A snapshot becomes non-servable when any rebuild reason is present:

- stale snapshot;
- methodology mismatch;
- confidence below serving threshold;
- drift threshold exceeded;
- accuracy threshold exceeded;
- bias threshold exceeded.

Insufficient calibration history is exposed as a limitation but does not by
itself fabricate a failure while the snapshot is otherwise fresh and permitted.

Rebuild appends a **new immutable BenchmarkSnapshot** using the existing
`BenchmarkBuilder`; it never mutates the historical snapshot. Provenance is
carried by old/new benchmark versions, methodology version and `as_of`.

## Safe fallback

When a CLIENT_ONLY snapshot is non-servable, the gate may advertise
GLOBAL_PUBLIC as `fallback_available`. It never performs that scope switch
implicitly; the caller must explicitly request/authorize the alternate scope.

A non-servable GLOBAL_PUBLIC snapshot does not fall back to CLIENT_ONLY because
that would silently change the requested statistical universe.

## Privacy

Quality/calibration data contains benchmark version, technical token counts and
timestamps only. Raw prompt/output content and cross-client identifiers are not
part of the calibration sample.

GLOBAL_PUBLIC privacy/cohort publication rules remain governed by #11 and are
further hardened by #158.

## Runtime integration

The quality gate is a reusable boundary for `EstimateBenchmarkLookup`. #157
does not inject a new provider call or alter AUTO/EXPLICIT_TARGET semantics.

A lookup that applies the gate returns the existing `BenchmarkBuildResult`:
non-servable snapshots are represented as unavailable with explicit reason,
limitations and optional explicit fallback.

## Validation

The isolated `Estimation quality` workflow uses deterministic synthetic
snapshots/calibration samples only. No provider or paid resource is used.
