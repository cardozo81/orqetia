# Observability retention, cardinality and cost policy

**Policy version:** `observability-v1`  
**Issue:** #154  
**Owner:** runtime/operations observability boundary

This document extends the correlation/redaction contract from #18. It does not
select a telemetry vendor and does not create an application-owned telemetry
database.

## Invariants

- payload logging remains fail-closed;
- provider secrets, credentials, provider financial truth and client-private raw
  input/output are never telemetry dimensions;
- metric labels remain bounded, low-cardinality values;
- tenant/client/session/task/attempt/work/correlation/trace identifiers may be
  correlation fields in logs/spans, but are not metric labels;
- collector indexing must not promote those high-cardinality correlation fields
  into metric-like dimensions;
- sampling never changes execution/accounting state;
- all spans sharing one `trace_id` use the same deterministic sampling decision;
- security events required by security/audit policy are not dropped merely to
  satisfy a telemetry cost budget.

## Default retention

| Environment | Operational logs | Metrics | Traces | Security logs |
| --- | ---: | ---: | ---: | ---: |
| local | 3 d | 7 d | 3 d | 30 d |
| test | 1 d | 3 d | 1 d | 7 d |
| staging | 14 d | 30 d | 7 d | 90 d |
| production | 30 d | 90 d | 14 d | 180 d |

The audit ledger governed by #150 is a different authoritative store. Telemetry
purge must never delete or rewrite those immutable audit records.

If legal/security policy mandates longer retention, the stricter policy wins. A
deployment may shorten only operational telemetry where privacy/security policy
permits it; it may not shorten required security/audit retention below its
governed minimum.

## Trace sampling

Default application guidance:

- local/test: 100%;
- staging: 25%;
- production: 10%.

Sampling is deterministic over `trace_id` via SHA-256, therefore every span in
the same trace makes the same keep/drop decision. Correlation IDs remain in
structured logs independently of trace export.

During an incident, operators may temporarily increase trace sampling if the
collector/backend has capacity and privacy restrictions remain unchanged. Payload
logging must remain disabled.

## Metric cardinality

The canonical metric contracts stay limited to:

- queue_name: budget 8 values;
- operation_type: 64;
- disposition: 16;
- provider_outcome: 32;
- attempt_status: 16;
- execution_mode: 4;
- health: 8;
- quarantine: 4;
- work_state: 16.

A metric may use at most four labels. Adding a new label requires changing the
versioned policy and its isolated contract test.

Never use tenant_id, client_id, provider/model IDs, session/task/attempt/work IDs,
trace/correlation IDs, prompt/payload/secret/credential material as metric labels.

## Volume budgets and saturation alerts

`OperationalObservabilityPolicy.volume` defines environment-specific soft and
hard events/minute budgets for logs, metrics and traces. The 80% hard-budget point
is the default saturation alert threshold.

These are collector/export capacity guardrails, not permission to silently remove
required security evidence. Recommended response order when saturated:

1. alert and preserve correlation/security events;
2. aggregate metric observations;
3. reduce operational trace sampling deterministically;
4. suppress duplicate/noisy operational events at the source only after review;
5. scale or reconfigure the collector/export path if the pressure persists.

Security events and #150 audit records are never the first shedding class.

## Purge procedure

Telemetry retention is enforced by the collector/backend, because ORQETIA has no
application-owned telemetry store.

For a scheduled purge:

1. identify the environment and signal class;
2. load `observability-v1` defaults and any stricter legal/security policy;
3. exclude authoritative audit records governed by #150;
4. delete/export-expire only records older than the effective retention cutoff;
5. retain correlation metadata necessary for still-retained security incidents;
6. record the purge execution/result in the operational change log;
7. verify storage/cardinality/volume dashboards returned below alert thresholds.

For emergency privacy deletion, follow #55/#61 and incident/security procedures.
Do not search or purge using raw secret/payload values.

## Deployment responsibility

Collectors/exporters must implement these retention and budget values. Backend
credentials/endpoints remain secret configuration and are intentionally outside
this policy. No paid observability service is required by CI.
