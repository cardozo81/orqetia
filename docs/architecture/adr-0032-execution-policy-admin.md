# ADR — Immutable execution-policy versions and client target envelopes

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #137

## Decision

Control Plane owns immutable execution-policy versions and the active assignment for
each tenant/client.

A policy version contains:

- max_cycles;
- max_attempts;
- cycle_delay_seconds;
- retry_after_cap_seconds;
- the exact authorized provider/model/reasoning-profile target envelope.

Publishing a policy creates a new immutable row. Historical policy versions are never
edited. Activation updates only the owner assignment with optimistic concurrency.

## Authorization boundary

Pricing, provider health and AUTO cost ranking never create authorization.

A target can participate only when it is present in the effective authorized target
envelope. EXPLICIT_TARGET remains fixed to an already-authorized target and AUTO may
rank only within that envelope.

## Resolution

The Control Plane resolver returns the effective version for one exact tenant/client.
There is no implicit global/default fallback in this baseline; absence of an assignment
fails closed.

Execution persists the effective policy version identifier and its authorized-target
snapshot with the session, preserving explainability across later policy changes.

## Concurrency

Client policy assignment has its own monotonically increasing assignment_version.
A concurrent stale publication fails rather than silently replacing another
administrator's assignment.

## Validation

The issue is validated through isolated execution-policy administration tests and the
Alembic migration chain. Provider calls are not involved.
