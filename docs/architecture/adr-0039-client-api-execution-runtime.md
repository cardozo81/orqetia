# ADR — Client API execution runtime boundary

**Status:** Accepted  
**Date:** 2026-10-06  
**Issue:** #143

## Decision

The `/v1` sessions/tasks/result/attempt/exchange/catalog routes are no longer HTTP stubs.
They delegate to a typed `ClientExecutionRuntime` that composes the canonical Execution,
Control Plane and Provider Registry boundaries.

Tenant/client authority always comes from the authenticated principal. Path/query/body
identifiers only select resources inside that authoritative scope.

## Session creation

`POST /v1/sessions` resolves the current effective administrative policy from #137 and
copies its immutable `SessionPolicySnapshot` into the durable Execution session.
Clients cannot submit cycles, attempts, retry delay, Retry-After cap, quota or
authorized targets.

Session creation is idempotent by tenant/client + operation + hashed Idempotency-Key +
request fingerprint.

## Task creation

Omitting `execution` creates an AUTO task and freezes no target.

`EXPLICIT_TARGET` still requires `tasks:target`. The requested target is resolved through
the current approved ProviderRegistry and the session's already-authorized target
envelope. The task freezes only the resolved exact target.

Server-side `OperationRequirementResolver` derives requirement facts. Browser input
cannot define or modify the requirement vector.

Task payload content is stored through an artifact port; `execution.tasks` receives only
a durable reference and SHA-256 fingerprint. The physical artifact strategy is #144.

After task persistence, the runtime materializes deterministic work
`execution.task.orchestrate` in the execution queue. Replays use deterministic work IDs.
`PostgresWorkQueue.enqueue` therefore treats an existing identical work ID as an
idempotent replay.

The actual orchestration worker/composer is tracked separately by #145; #143 owns the
public runtime/submission boundary, not the orchestration policy implementation.

## Reads, cancellation and evidence

Every session/task/attempt/exchange read is ownership-scoped.

Cancellation is idempotent. CREATED/QUEUED work becomes CANCELLED; RUNNING work moves
to CANCELLING and the worker completes cancellation.

Results are read only through a client-safe artifact port. Attempt exchange evidence is
returned only after exact task/attempt ownership validation and contains the sanitized
raw evidence contract from #88. No provider secret/account/credential, provider cost,
currency, pricing or credit data is projected.

## Catalog

`GET /v1/providers` and `/v1/models` materialize only approved ProviderRegistry metadata
intersected with the effective client target envelope. Catalog approval alone never
grants client authorization.

## Idempotency

Execution owns a small persistent client API idempotency journal. It stores only hashes,
owner, operation and resource ID; no request payload or bearer token.

An Idempotency-Key replay with a different semantic request fails with 409.

## Known downstream prerequisites

- #144 provides the production durable artifact adapter behind the ports used here.
- #145 registers and composes the `execution.task.orchestrate` worker path.

These are explicit blockers for #23/#27 and are not hidden inside the HTTP layer.

## Validation

Validation is intentionally narrow: migration, FastAPI shell contract and one synthetic
ASGI runtime suite covering ownership, AUTO/EXPLICIT_TARGET, cancellation, sanitized
result/evidence and authorized provider/model filtering. No provider or real IdP call
is made.
