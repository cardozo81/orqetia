# Availability, maintenance and controlled-degradation policy

**Policy version:** `availability-v1`  
**Issue:** #159

This is a technical pre-RC policy, not a commercial SLA.

## Process states

A live process is `HEALTHY`. Readiness is evaluated separately as `READY`,
`DEGRADED` or `UNAVAILABLE`.

Critical dependency outages make a process unavailable. Non-critical outages
produce degraded behavior when a safe subset remains serviceable.

## Dependency matrix

| Dependency | API | Worker | Scheduler |
| --- | --- | --- | --- |
| PostgreSQL | critical | critical | critical |
| authentication | critical | n/a | n/a |
| secret store | degraded unless required path uses it | critical | n/a |
| provider | degraded | degraded/requeue | n/a |
| read model | degraded; affected reads fail safely | n/a | n/a |

Authentication and secret access fail closed. No principal or secret is guessed.

## Planned maintenance

- `normal`: ordinary admission and work claiming;
- `draining`: API mutations return 503 + `Retry-After`; safe reads may
  continue; worker/scheduler stop new claims and in-flight work drains under the
  existing shutdown grace;
- `maintenance`: readiness is unavailable and all `/v1` calls return a
  sanitized 503 + `Retry-After`.

`/health/live` stays independent from dependencies and maintenance.

## Recovery criteria

Before returning to normal: database readiness passes, auth/secret dependencies
required by the process are available, outstanding leases are completed or
recovered, schema compatibility is known, and provider/read-model degradation is
either cleared or explicitly accepted.

## Client contract

Maintenance/unavailability responses expose only generic error codes,
`Retry-After`, and normal correlation metadata. Internal dependency exceptions
are not returned.

Provider outage alone does not make Client API unavailable; durable tasks may
wait/retry according to the canonical orchestration policy.

## Communication

A status page may later project these technical states, but no vendor is required.
Never publish tenant/client identifiers, provider credentials, internal costs or
sensitive incident evidence.
