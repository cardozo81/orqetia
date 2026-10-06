# ORQETIA Capacity Targets and Synthetic Load Profile

Issue: #147
State: DEVELOPMENT
Depends on: #44, #145

These targets are engineering regression budgets for the current DEVELOPMENT
architecture. They are not a public SLA and do not imply production sizing.

## Purpose

The profile provides a repeatable lower-bound sanity check for three local paths:

1. FastAPI routing/security middleware through an in-process ASGI request;
2. PostgreSQL durable work queue enqueue/claim/complete;
3. client read-model cache/write/lookup/pagination from the existing #45 benchmark.

No provider adapter or paid external dependency is invoked.

## Reference CI profile

The isolated gate runs:

| Surface | Synthetic work | Regression threshold |
| --- | ---: | ---: |
| API shell | 250 sequential GET /health/live requests through ASGITransport | p95 <= 150 ms |
| Durable queue | 100 CLIENT_PRIVATE work items: enqueue + claim + complete | total <= 15 s |
| Read models | 5,000 create + cache put/get + bounded page | total <= 10 s |

These thresholds are intentionally loose enough to avoid treating ordinary hosted
runner jitter as a product failure. A regression that exceeds them is large enough
to require investigation before increasing the limit.

Threshold increases require:
- measured evidence;
- explanation of the regression/source;
- confirmation that the algorithm/data access path remains bounded;
- update to this document in the same change.

## Initial capacity assumptions

The M0/M9 reference operating envelope is deliberately modest:

- public/client API remains horizontally stateless except server-side session/state
  stores owned by their bounded contexts;
- request body limits and page limits remain bounded by the public contracts;
- queue payload stays <= 64 KiB and never carries SECRET material;
- queue work is durable and claimed in batches capped by the queue contract;
- ordinary client-facing pages use cursor/bounded reads rather than full ledger
  scans;
- provider calls are outside the API request latency budget and execute in workers;
- provider concurrency/cost limits are governed separately by quotas/policy.

A first real deployment must measure its own database/network/provider capacity
before any RC/production claim. The numbers above are CI regression budgets, not a
promise that a given host sustains a particular tenant count.

## SLI set for deployment validation

Before RC on a concrete environment, capture at minimum:

- API request rate and p50/p95/p99 server latency by route class;
- 4xx/5xx rate excluding expected client validation failures;
- work queue depth, oldest-ready age, claim throughput and dead-letter rate;
- worker utilization/concurrency and lease-reclaim rate;
- PostgreSQL connection-pool saturation, transaction latency and lock pressure;
- read-model freshness/as_of lag and query latency;
- task submission-to-first-attempt latency;
- task completion latency separated from provider latency when possible;
- artifact/read-model storage growth;
- quota/backpressure rejection rates.

Do not put tenant/client raw identifiers, secrets or prompt/result content in metric
labels.

## Escalation criteria

A dedicated analytics/data store or more complex queue technology is considered
only after measured SLO/capacity evidence shows PostgreSQL/current queue no longer
meets the required envelope. Architectural complexity is not introduced merely to
raise synthetic benchmark numbers.

## Running locally

With a migrated PostgreSQL test database configured through the normal ORQETIA
settings:

    uv run --python 3.14 python scripts/benchmark_capacity_profile.py

The read-model-only benchmark remains available independently:

    uv run --python 3.14 python scripts/benchmark_read_models.py

Both use synthetic data only.
