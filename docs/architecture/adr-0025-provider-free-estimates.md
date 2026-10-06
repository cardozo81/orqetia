# ADR — Provider-free technical estimates

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #5

## Decision

`POST /v1/estimates` is synchronous and provider-free. It never invokes an AI
provider and never returns monetary information.

The operation combines a deterministic local input-token approximation with the
governed CLIENT_ONLY or GLOBAL_PUBLIC benchmark from #11 and the same execution-target
authorization envelope used by runtime execution.

## Execution modes

If `execution` is omitted, the request is AUTO.

AUTO receives authorized/capability-compatible targets in internal policy order.
The reference resolver reuses the canonical orchestration AUTO rank for comparable
internal provider-cost estimates; no monetary value crosses the client boundary.

EXPLICIT_TARGET requires the target permission and the exact target must be
authorized. The estimate never substitutes another target.

## Statistical scope

CLIENT_ONLY and GLOBAL_PUBLIC preserve #11. CLIENT_ONLY cold start never silently
switches to GLOBAL_PUBLIC. Any alternative scope is only explicit fallback metadata.

## Output

Only technical fields are client-facing: token estimates, requested/effective mode,
effective target, scope, methodology/benchmark version, as_of, safe sample/cohort
size, confidence, fallback metadata and limitations.

Provider cost, currency, pricing, provider account/credential, credit balance and
future client charge are absent by construction.

## Input approximation

Until model-specific local tokenizers are wired, canonical JSON UTF-8 byte length is
divided by four and rounded upward. `estimation_method` makes the approximation
explicit; it is not provider-observed truth.

## Ownership and idempotency

A CLIENT_ONLY benchmark snapshot is revalidated against the authenticated
tenant/client before it is converted into a response. Mismatched owner, scope or
target fails closed even if a lookup adapter returns malformed data.

The HTTP adapter validates the required `Idempotency-Key` declared by the canonical
OpenAPI. The estimate itself is side-effect free; the header keeps transport behavior
consistent with the API-wide idempotency envelope.

## Failure mode

No authorized target or no governed benchmark means fail closed. A deployment without
a configured estimate service returns service unavailable instead of calling a
provider or bypassing target authorization.

Ordinary CI uses synthetic data/fakes only and incurs no provider cost.
