# Tenancy authorization matrix — baseline

Legend:
- ALLOW = may be authorized when role/scope conditions are satisfied.
- DENY = baseline product rule.
- ADMIN = Backoffice privilege required.
- OWN = only resources in authenticated tenant/client ownership.

| Action | Service client | Customer human | Backoffice |
|---|---|---|---|
| Create tenant | DENY | DENY | ADMIN |
| Create service client | DENY | DENY baseline | ADMIN |
| Suspend/offboard tenant/client | DENY | DENY | ADMIN |
| Assign execution policy | DENY | DENY | ADMIN |
| Assign provider permission | DENY | DENY | ADMIN |
| Assign quota | DENY | DENY | ADMIN |
| Manage provider account/secret | DENY | DENY | ADMIN |
| Create own integration credential | scope-dependent | ALLOW for administered OWN client | ADMIN/support only with explicit privilege |
| Rotate/revoke own credential | scope-dependent | ALLOW for administered OWN client | ADMIN/support only with explicit privilege |
| Create execution session/task — AUTO | ALLOW OWN + base execution scope | ALLOW OWN when product UI permits | ADMIN only for explicit operational action |
| Create task — EXPLICIT_TARGET | OWN + tasks:write + tasks:target + enabled target entitlement | OWN + role + enabled target entitlement when product UI permits | ADMIN only with explicit operational privilege + target entitlement |
| Read task/result | OWN + scope | OWN + role/scope | ADMIN privilege + purpose/audit |
| Cancel task | OWN + scope | OWN + role/scope | ADMIN privilege |
| Read technical usage | OWN + scope | OWN + role/scope | ADMIN privilege |
| Read provider cost/credits/contracts | DENY | DENY | ADMIN finance/provider privilege |
| Read provider secret cleartext | DENY | DENY | DENY after persistence except tightly controlled runtime secret boundary |
| Export cross-client operational data | DENY | DENY | ADMIN reporting privilege + audit |

## Property-level rules

Client-facing responses may expose:
- requested/effective provider/model/reasoning target when the client is authorized for that task and public policy permits;
- technical token/native usage;
- status/latency/provenance safe fields.

Client-facing responses do not expose:
- provider_account_id when sensitive;
- provider_credential_id/fingerprint unless an explicit safe public need exists (baseline: no);
- provider cost;
- pricing amounts/currency;
- provider credit/balance;
- commercial provider terms;
- secret references/material;
- internal error/debug/security evidence.

Exact classification is finalized in #61.
