# Phase 2 observability and correlation contract

Issue #18 defines the runtime telemetry boundary for Session/Task execution. This
contract deliberately does not implement routing decisions owned by #8 and does not
introduce a paid telemetry backend.

## Structured events

Runtime processes emit JSON Lines through the shared observability emitter. The
emitter adds an UTC timestamp and applies fail-closed redaction before serialization.

Safe runtime correlation fields include, when available:

- correlation_id, causation_id and trace_id;
- tenant_id and client_id;
- session_id, task_id, attempt_id and work_id;
- cycle_index and attempt_index;
- provider_id, model_id and reasoning_profile;
- queue/operation identifiers, work attempt count and attempt status;
- normalized provider outcome, safe error class, latency and token usage.

Inline work payloads, prompts, raw inputs/outputs, HTTP bodies, credentials, provider
accounts, provider cost amounts, balances and client charges are not telemetry fields.
Sensitive keys supplied accidentally are replaced with `[REDACTED]`. Unknown object
types are not rendered with `repr`, preventing accidental secret disclosure.

## Correlation propagation

`WorkItem` / `WorkLease` remain the durable carrier for `correlation_id`,
`causation_id` and `trace_id`. The provider dispatch handler copies correlation and
trace identifiers into `ProviderAttemptRequest` without changing provider target or
retry semantics.

For task-scoped provider work, the observable chain is therefore:

`tenant/client -> session -> task -> work -> attempt -> provider request`.

Correlation identifiers are diagnostic metadata only. They never replace
tenant/client ownership checks or authorization.

## Metrics

Phase 2 freezes metric names and bounded label sets without selecting a metrics
backend:

- `orqetia_work_events_total{queue_name,operation_type,disposition}`;
- `orqetia_provider_attempts_total{provider_outcome,attempt_status}`;
- `orqetia_provider_attempt_latency_ms{provider_outcome}`.

Tenant, client, session, task, attempt, work, provider/model and trace identifiers are
forbidden metric labels because their cardinality is unbounded or deployment-driven.

## Traces

The contract reserves two span names:

- `orqetia.work`;
- `orqetia.provider_attempt`.

Allowed span attributes are identifiers and safe execution metadata only. Payload,
prompt, raw input/output, secret and financial fields are excluded. A concrete tracing
exporter is intentionally deferred; `telemetry_trace_sample_rate` defines the runtime
sampling boundary and defaults to `0.0`.

## Payload logging, retention and sampling

`telemetry_log_payloads` defaults to false and configuration fails if it is enabled.
A future explicit data policy must exist before payload logging can be introduced.

ORQETIA does not persist an application-owned telemetry store in Phase 2. Logs are
emitted to the process output stream; collector/export retention belongs to operational
hardening and must comply with the data lifecycle policy. This avoids creating a hidden
cross-tenant data store or variable-cost observability dependency.

The trace sampling rate is bounded to `0.0..1.0`. Sampling never changes durable
execution state and never affects provider dispatch, retry, quarantine or accounting.

## Security and client boundary

Telemetry is operational/internal. Client-facing APIs do not expose provider secrets,
provider accounts, balances, provider cost values or internal pricing. Internal cost
reference identifiers may be correlated where they already exist, but monetary truth
is not logged by this contract.

## Tests

The dedicated observability gate proves:

- redaction of secret, payload and financial fields;
- payload logging remains fail-closed;
- metric labels stay low-cardinality;
- span contracts contain no payload/secret attributes;
- Work correlation excludes inline payloads;
- correlation_id and trace_id propagate into the provider adapter request;
- provider dispatch events correlate tenant/client/session/task/work/attempt without
  logging raw payloads or provider cost.


## Operational hardening policy

Retention, cardinality, volume and deterministic trace-sampling controls are
versioned separately in
`docs/runtime/observability-retention-and-cost-policy.md` (`observability-v1`).
The original #18 redaction/correlation invariants remain authoritative.
