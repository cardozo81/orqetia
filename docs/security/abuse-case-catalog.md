# Security abuse-case test catalog

Each case becomes an executable test when its target boundary exists.

| ID | Abuse case | Expected result |
|---|---|---|
| AB-001 | Tenant A requests Tenant B task by UUID | 404/403 safe response; no data |
| AB-002 | Client A1 changes client_id to A2 in body/header/query | request rejected; authenticated scope unchanged |
| AB-003 | Client submits max_cycles/provider/timeout/pricing fields | protected fields rejected/not accepted by client schema |
| AB-004 | Client calls Backoffice route with valid client token | denied |
| AB-005 | Low-privilege Backoffice role exports financial/secret data | denied + audit |
| AB-006 | Revoked credential/token attempts new task | denied before side effect |
| AB-007 | Logout session cookie is replayed | denied |
| AB-008 | OIDC token with wrong issuer/audience/nonce/signature | denied |
| AB-009 | SQL metacharacters/path/header injection payload | treated as data/rejected; no interpreter change |
| AB-010 | Browser mutation lacks CSRF/origin protection | denied when browser session boundary exists |
| AB-011 | Oversized/deep request | bounded rejection |
| AB-012 | Unbounded pagination/export attempt | bounded/rejected |
| AB-013 | Worker executes queued task with mismatched tenant scope | denied/quarantined; no provider call |
| AB-014 | Provider endpoint resolves to localhost/link-local/private target without explicit internal allowance | rejected |
| AB-015 | Provider returns huge/malformed/error payload containing secret-like data | bounded; safe error/log |
| AB-016 | Provider redirect attempts to send Authorization to another host | secret not forwarded |
| AB-017 | Same event delivered twice | exactly one logical effect |
| AB-018 | Same idempotency key + different payload | 409 conflict; no second effect |
| AB-019 | Worker dies after external call before final persistence | recovered/reconciled without blind duplicate call |
| AB-020 | Concurrent cancel and completion | only valid terminal state/accounting |
| AB-021 | GLOBAL_PUBLIC filters isolate one tenant/small cohort | query rejected/coarsened |
| AB-022 | Client asks for provider cost/currency/credential fingerprint | forbidden fields absent |
| AB-023 | Secret appears in provider exception | redacted from client/log/trace |
| AB-024 | Workflow uses unpinned external action | CI baseline fails |
| AB-025 | Private key/API token committed | CI baseline fails |
| AB-026 | Read model stale ownership differs from authoritative owner | mutation uses authoritative owner and denies |
