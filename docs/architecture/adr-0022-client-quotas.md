# ADR — Client quotas, reservation and reconciliation

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #17

## Ownership split

Control Plane owns the effective quota definition: scope, metric, limit, period,
burst, enforcement mode, provider/native-unit filter and version.

Usage & Accounting owns mutable consumption state: fixed windows, reservations,
reconciliation, expiry, release, rejection and utilization.

Provider account credits/rate limits remain a separate internal concern of #59.
A client quota never exposes or aliases a provider balance.

## Enforcement contract

Execution must reserve quota before a provider side effect. Reservation is atomic and
idempotent within tenant/client + policy version + idempotency key.

Supported metrics:

- requests;
- tasks;
- concurrent tasks;
- tokens;
- native units;
- provider-specific requests.

Provider-specific quota restricts an already-authorized target; it does not grant
provider permission.

For fixed-window metrics, the UTC-aligned window resets by configured period. Burst
adds explicit capacity above the normal limit. For concurrent tasks, the reservation
is the active gauge and has no reset period/burst.

HARD quota rejects a reservation whose projected committed amount exceeds
`limit + burst`. SOFT quota allows the reservation and reports overage.

Reservations expire fail-safe to avoid permanent capacity leaks. Usage that actually
occurred can still be reconciled after reservation expiry. Explicit release is used
when work is abandoned before consumption.

## Reconciliation

Periodic consumption is recorded only during reconciliation, using the observed
actual amount. Reconciliation is idempotent and the actual amount becomes immutable.
If actual usage exceeds the reservation, the factual usage is still recorded; overage
is reported and subsequent reservations see the higher consumption.

Concurrent-task reconciliation releases the active reservation instead of creating
historical period consumption.

## Isolation and exposure

Every mutation is bound to a tenant and client. Tenant/client mismatch fails closed.
Native-unit and provider-specific policies validate their dimensions explicitly.

Client-facing utilization may expose technical limit/used/reserved/remaining values.
It must not expose provider cost, currency, provider credit balance, pricing rules or
commercial charge.

Backoffice configuration UI and reporting projections consume these contracts in
later #22/#26 work; they do not redefine quota semantics.

## Persistence

`control.quota_policies` is the physical owner for versioned definitions.

`accounting.quota_windows` and `accounting.quota_reservations` are the durable
consumption boundary. No cross-context foreign key is introduced.

Ordinary CI uses deterministic fakes and PostgreSQL migrations only. No paid provider
or external quota service is required.
