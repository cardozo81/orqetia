# API abuse controls and technical backpressure

Issue: #151  
Policy state: DEVELOPMENT

These controls protect the HTTP/queue boundary and are intentionally separate
from client business quotas (#17/#139). They do not change orchestration
retry/cycle policy or commercial capacity.

## Default technical limits

| Route class | Body | JSON depth | JSON nodes | Rate / process |
| --- | ---: | ---: | ---: | ---: |
| ordinary reads | no body | n/a | n/a | 240/min |
| task polling/result/attempt reads | no body | n/a | n/a | 120/min |
| estimates | 256 KiB | 20 | 5,000 | 30/min |
| ordinary mutations | 256 KiB | 20 | 5,000 | 60/min |
| credential mutations | 32 KiB | 12 | 1,000 | 20/min |

The process-local concurrent request ceiling is 128. The execution submission
queue rejects new task submissions when READY depth reaches 10,000, returning a
bounded Retry-After (default 5 seconds).

## Identity and privacy

Technical fixed-window rate keys are SHA-256 hashed before storage. The controller
can apply one key to request origin and a second to the authenticated principal.
Saturation snapshots contain aggregate counters only; tenant/client identifiers,
tokens, payloads and secrets are not metric labels.

## Responses

- 413 `REQUEST_BODY_TOO_LARGE` for technical body caps;
- 422 `REQUEST_COMPLEXITY_EXCEEDED` for excessive JSON depth/node count;
- 429 `RATE_LIMITED`, `API_BACKPRESSURE` or `QUEUE_BACKPRESSURE`;
- 429 responses include `Retry-After` when a bounded retry delay exists;
- 503 `BACKPRESSURE_PROBE_UNAVAILABLE` when task submission cannot verify the
  durable execution queue depth.

All errors use the existing sanitized ErrorEnvelope.

## Queue relationship

The durable PostgreSQL queue remains the source of queue depth. The API checks
only READY execution work due for processing. This is admission control; it does
not alter queue leases, worker retry ownership, client quota reservations or
provider-side retry policy.

## Configuration

`create_app` accepts an injected `InMemoryApiAbuseController` built from a
versioned `ApiAbusePolicy`. Production composition may override defaults with
measured capacity values without changing application semantics. The default
controller is active when no override is supplied.
