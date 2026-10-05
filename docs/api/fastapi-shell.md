# FastAPI client API shell

Issue: #103
Canonical contract: #4 / contracts/openapi/orqetia-v1.openapi.json

## Contract ownership

FastAPI does not redefine the product contract.

The application serves the versioned canonical document at /openapi.json.
Tests independently compare the actual /v1 route/method surface with that document.

## Current Phase 1 behavior

All canonical /v1 routes exist.

After authentication/scope validation, business endpoints return HTTP 501 with code NOT_IMPLEMENTED, a safe error envelope, and a correlation ID.

This is deliberate. Phase 1 must not fake successful sessions/tasks/results before #6/#7 and their persistence/runtime owners exist.

## Authentication boundary

HTTP extracts the Bearer wire credential and passes the opaque token to BearerAuthenticator.

The shell does not decode JWT claims, validate signatures itself, implement OIDC discovery, trust client-supplied tenant/client identity, or infer permissions from token text.

The default composition root denies authentication until a standards-based adapter is wired under #12/#54.

Scope gates already match the public API contract, including the additional tasks:target requirement for EXPLICIT_TARGET.

Object ownership cannot be faked while repositories/resources do not exist. Resource implementations must revalidate authoritative tenant/client ownership before returning or mutating data.

## Correlation and errors

Every response carries X-Correlation-ID. A syntactically safe caller-supplied ID may be preserved; otherwise the server generates a UUID.

Errors use the client-safe envelope from #4 and do not reflect request secret values or stack traces.

## Container

The local Compose api service runs Uvicorn and binds port 8000 to 127.0.0.1 only.

Worker/scheduler remain on the development lifecycle harness until #105.

## Not implemented here

- session/task database operations;
- execution/orchestration;
- OAuth/OIDC/JWT backend;
- provider calls;
- health/readiness (#106);
- Backoffice/admin API.
