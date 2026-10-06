# ADR — Backoffice operational and financial intelligence

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #60

## Decision

Operational/financial intelligence is a Backoffice-only analytical layer over the
typed reporting rollups from #26. It never scans raw execution/accounting ledgers for
normal dashboard queries.

Supported cross-dimensions include tenant, client, client access credential, provider,
provider account, provider credential, model, session, task, attempt, policy version,
status/error class and time period.

## Metrics

For each grouping the service derives:

- requests, tasks, attempts and technical token/native usage;
- peak concurrent execution and maximum quota utilization;
- success/partial/failure counts and failure rate;
- latency average and throughput;
- cycles/retries/fallbacks and retry rate;
- health/quarantine event counters;
- peak attempts per projected time bucket;
- estimated provider cost by currency;
- observed provider cost by currency;
- UNPRICED attempts.

Currencies remain independent totals. There is no FX conversion or cross-currency
grand total.

## Capacity and quotas

External provider capacity and client quota utilization are consumed through explicit
owner ports. Provider capacity remains authoritative in #59; client quotas remain
authoritative in #17. Intelligence joins/surfaces their safe Backoffice indicators but
does not rewrite those facts.

Unknown external balance remains unknown.

## Authorization

Financial intelligence requires explicit Backoffice financial permission. When a role
is tenant-restricted, every intelligence query must include an allowed tenant filter;
omitting the filter is rejected instead of broadening the query.

Filters are fingerprinted safely for traceability. No secret, raw idempotency key or
client-facing projection receives these financial results.

## Performance

Source rows are bounded to 50,000 already-aggregated rollups per analytical request.
Larger workloads require pre-aggregation or an asynchronous export/report job rather
than falling back to a ledger scan.

Synthetic volume tests exercise grouping over 1,000 rollups with no provider calls.


## Completion hardening

The final #60 hardening extends the same `readmodel.report_rollups` model rather than
creating a competing analytical source. Optional execution identities
(session/task/attempt/policy version) and operational counters are added by migration
`20261005_0015`.

Existing #26 rows remain valid because newly introduced counters default to zero and
execution identities are nullable. Normal analytical queries remain bounded and
index-backed.
