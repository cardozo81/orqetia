# ORQETIA /v1 client API contract

- Issue: #4
- Product state: **DEVELOPMENT**
- Canonical machine-readable contract: `contracts/openapi/orqetia-v1.openapi.json`

## API boundary

`/v1` is the client/service-facing API.

Backoffice is a separate security and data surface. Its future API is versioned separately under an administrative boundary (target: `/admin/v1`) and is owned by #22/#26. Client and admin schemas must not be merged merely for implementation convenience.

## Long-running execution

Task creation returns **202 Accepted** and a durable Task resource. Provider work continues asynchronously through the runtime/queue.

HTTP requests do not remain open for the orchestration lifecycle.

## Execution selection

`POST /v1/sessions/{session_id}/tasks` accepts optional `execution`.

Omitted `execution` means AUTO.

EXPLICIT_TARGET is allowed only when #12/#21 authorization succeeds. OpenAPI records:
- base `tasks:write` scope;
- conditional `tasks:target` scope;
- object ownership;
- target-resolution failure before dispatch.

The request schema does not expose:
- max cycles;
- retry policy;
- delay;
- timeout caps;
- quarantine policy;
- administrative quota.

## Attempt identity

`attempt_id` is the client-safe provider-dispatch correlation identity.

Task responses expose safe attempt provenance. A retry/fallback keeps its own distinct `attempt_id`.

Client APIs do not use request hash, timestamp, provider/model proximity or display labels as attempt identity.

## Taskless operations

The core/persistence contract permits a taskless Attempt when the specific ORQETIA operation contract allows it.

The initial public `/v1` does **not** introduce an arbitrary generic taskless creation endpoint.

The shared `AttemptView` allows `task_id = null` so future operation-specific endpoints can expose legitimate taskless attempts without inventing a Task or round.

## Exchange evidence

For task-bound attempts, the API may expose retained sanitized exchange evidence under:

`GET /v1/tasks/{task_id}/attempts/{attempt_id}/exchanges`

Rules:
- ownership is revalidated from the task/attempt;
- raw evidence was sanitized before persistence;
- `sanitized_raw_body` is not semantically humanized;
- provider identity uses separate `provider_id` / `provider_name`;
- retention/privacy policy may mean evidence is absent/not retained;
- provider credentials and monetary data are never included.

## Financial boundary

The client OpenAPI intentionally has no fields for:
- provider cost;
- currency;
- unit price;
- cost/token;
- provider credit/balance;
- provider credential ID/fingerprint;
- client_charge.

Technical token/native usage is allowed.

Future commercial billing (#41) is a separate contract and must not mutate provider-cost history.

## Idempotency

Mutating/repeatable operations use `Idempotency-Key` where duplicate logical effects would be harmful.

The header is not authentication and does not replace resource ownership.

Same key + different semantic request is a 409 conflict per #14.

## Errors

Client errors use a stable safe envelope:
- `code`;
- `message`;
- `correlation_id`;
- optional safe `details`.

Relevant statuses include:
- 401 authentication;
- 403 scope/object/target authorization;
- 404 resource hidden/not found;
- 409 state/idempotency conflict;
- 413 content policy limit;
- 422 semantic/target validation;
- 429 rate/concurrency/quota policy.

Errors never expose stack traces, SQL, raw provider bodies, secrets or internal monetary data.

## Rate/content limits

The contract exposes bounded pagination (max 100 per page) and 413/429 behavior.

Payload byte/token limits remain server-policy configuration until the dedicated payload/capacity issues finalize exact values. Clients cannot increase those limits through request fields.

## Catalog

`/v1/providers` and `/v1/models` return only the provider/model envelope visible to the authenticated client.

They are not inventory dumps of all internal provider accounts/models.

## Security model

The canonical spec uses bearer JWT as the wire authentication scheme and vendor extensions for operation scopes/conditional scopes.

ADR-0009 remains authoritative for token validation and actual OAuth/OIDC issuance.

Every object read/write still enforces tenant/client ownership server-side.

## Versioning

Breaking public contract changes require:
- a compatibility transition within `/v1` when feasible; or
- a future versioned API boundary.

OpenAPI is reviewed/checked as a product contract; generated framework docs do not silently redefine it.
