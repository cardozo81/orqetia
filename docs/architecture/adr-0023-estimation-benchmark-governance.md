# ADR — CLIENT_ONLY and GLOBAL_PUBLIC benchmark governance

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #11

## Scope

Statistical Estimation owns versioned benchmark snapshots used by future
`POST /v1/estimates`. It consumes only typed technical rollups permitted by
#44/#55/#61. Raw prompts, contexts and outputs are not part of the benchmark input
contract and are never persisted in benchmark snapshots.

## CLIENT_ONLY

CLIENT_ONLY filters exclusively by the authenticated tenant/client owner. It never
uses another client's samples.

Cold start is explicit. If the minimum history threshold is not met, the result is
unavailable and may state that GLOBAL_PUBLIC is an available alternative. The system
does **not** silently switch scopes; the caller must explicitly request/authorize the
different scope.

## GLOBAL_PUBLIC

GLOBAL_PUBLIC accepts no tenant/client filter. Cohorts are partitioned only by a fixed
typed feature key:

- provider;
- model;
- reasoning profile;
- input-size bucket;
- output class;
- schema class.

The default release gate requires at least 30 samples from at least 5 distinct
tenant/client pairs. Both thresholds are configurable only through a versioned
methodology policy.

A single client with high volume cannot satisfy the cohort gate. Exact internal sample
and client counts are retained for governance, but client metadata exposes bucketed
counts only. No snapshot carries tenant/client identity.

Arbitrary ad-hoc filters are deliberately absent from the GLOBAL_PUBLIC contract,
preventing a caller from repeatedly narrowing a cohort until another client becomes
inferable.

## Methodology

Snapshots contain:

- methodology version;
- benchmark version;
- `as_of`;
- sample and cohort counts;
- confidence;
- median token metrics;
- p90 output/total tokens;
- typed feature key.

Samples older than the configured retention horizon are excluded. Median/p90 are used
as robust baseline statistics; future outlier/calibration methods require a new
methodology version rather than silently changing historical interpretation.

Confidence is deterministic from sample/cohort depth. Drift can be computed between
snapshots of the same scope/key from median total-token movement.

## Privacy and classification

GLOBAL_PUBLIC snapshot fields are aggregate statistics, not source records. They must
still be treated as governed data because inference risk depends on cohort shape.

No SECRET, RESTRICTED financial, provider credential, raw CLIENT_PRIVATE content or
cross-client identifiers are admitted into the benchmark schema.

## Persistence and rebuild

`estimation.benchmark_snapshots` stores aggregate-only immutable snapshots.
Snapshots are rebuildable from permitted rollup sources and carry `as_of` plus
methodology/benchmark version.

The future estimates API (#5) consumes these snapshots; it must return scope,
methodology version, benchmark version, `as_of`, safe sample size, confidence and
limitations without exposing internal cohort membership.

Ordinary CI uses synthetic datasets only and has no paid-resource dependency.
