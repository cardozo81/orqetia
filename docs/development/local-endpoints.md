# Local endpoint map

Issue #165. Base URL: `https://localhost:8443`. OpenAPI is unchanged.

All writes retain canonical idempotency and owner checks. Explicit targets additionally require `tasks:target`.
Human POSTs require same-origin, CSRF and the canonical permission; admin-sensitive operations also enforce step-up.
Local MFA is simulated, so this is DEVELOPMENT evidence only. Examples below describe browser/form or HTTP actions; replace path placeholders with owned IDs.

## Client API

| Surface | Method | Path | Auth | Role/scope | Purpose | Example |
| --- | --- | --- | --- | --- | --- | --- |
| Client API | POST | `/v1/sessions` | Bearer | sessions:write | create session | POST /v1/sessions; body: canonical OpenAPI schema |
| Client API | GET | `/v1/sessions/{session_id}` | Bearer | sessions:read | get session | GET /v1/sessions/{session_id} |
| Client API | POST | `/v1/sessions/{session_id}/tasks` | Bearer | tasks:write | create task | POST /v1/sessions/{session_id}/tasks; body: canonical OpenAPI schema |
| Client API | GET | `/v1/tasks/{task_id}` | Bearer | tasks:read | get task | GET /v1/tasks/{task_id} |
| Client API | GET | `/v1/tasks/{task_id}/result` | Bearer | tasks:read | get task result | GET /v1/tasks/{task_id}/result |
| Client API | POST | `/v1/tasks/{task_id}/cancel` | Bearer | tasks:cancel | cancel task | POST /v1/tasks/{task_id}/cancel; body: canonical OpenAPI schema |
| Client API | GET | `/v1/tasks/{task_id}/attempts` | Bearer | tasks:read | list task attempts | GET /v1/tasks/{task_id}/attempts |
| Client API | GET | `/v1/tasks/{task_id}/attempts/{attempt_id}/exchanges` | Bearer | tasks:read | list attempt exchanges | GET /v1/tasks/{task_id}/attempts/{attempt_id}/exchanges |
| Client API | POST | `/v1/estimates` | Bearer | estimates:write | create estimate | POST /v1/estimates; body: canonical OpenAPI schema |
| Client API | GET | `/v1/credentials` | Bearer | credentials:read | list client credentials | GET /v1/credentials |
| Client API | POST | `/v1/credentials` | Bearer | credentials:write | issue client credential | POST /v1/credentials; body: canonical OpenAPI schema |
| Client API | POST | `/v1/credentials/{credential_id}/rotate` | Bearer | credentials:write | rotate client credential | POST /v1/credentials/{credential_id}/rotate; body: canonical OpenAPI schema |
| Client API | POST | `/v1/credentials/{credential_id}/revoke` | Bearer | credentials:write | revoke client credential | POST /v1/credentials/{credential_id}/revoke; body: canonical OpenAPI schema |
| Client API | GET | `/v1/providers` | Bearer | catalog:read | list providers | GET /v1/providers |
| Client API | GET | `/v1/models` | Bearer | catalog:read | list models | GET /v1/models |
| Client API | GET | `/v1/usage` | Bearer | usage:read | get usage | GET /v1/usage |

## Backoffice

| Surface | Method | Path | Auth | Role/scope | Purpose | Example |
| --- | --- | --- | --- | --- | --- | --- |
| Backoffice | GET | `/backoffice/login` | Login transaction (state/nonce/PKCE) | Authenticated session / route guard | login | GET /backoffice/login |
| Backoffice | GET | `/backoffice/callback` | Login transaction (state/nonce/PKCE) | Authenticated session / route guard | callback | GET /backoffice/callback |
| Backoffice | GET | `/backoffice` | Secure HttpOnly BFF session | Authenticated session / route guard | home | GET /backoffice |
| Backoffice | POST | `/backoffice/logout` | Secure HttpOnly BFF session | Authenticated session / route guard | logout | Submit /backoffice/logout form |
| Backoffice | GET | `/backoffice/tenants` | Secure HttpOnly BFF session | tenancy:admin | tenants page | GET /backoffice/tenants |
| Backoffice | POST | `/backoffice/tenants/create` | Secure HttpOnly BFF session | tenancy:admin | create tenant | Submit /backoffice/tenants/create form |
| Backoffice | POST | `/backoffice/tenants/{tenant_id}/status` | Secure HttpOnly BFF session | tenancy:admin | set tenant status | Submit /backoffice/tenants/{tenant_id}/status form |
| Backoffice | POST | `/backoffice/clients/create` | Secure HttpOnly BFF session | tenancy:admin | create client | Submit /backoffice/clients/create form |
| Backoffice | POST | `/backoffice/clients/status` | Secure HttpOnly BFF session | tenancy:admin | set client status | Submit /backoffice/clients/status form |
| Backoffice | GET | `/backoffice/users` | Secure HttpOnly BFF session | users:admin | users page | GET /backoffice/users |
| Backoffice | POST | `/backoffice/users/create` | Secure HttpOnly BFF session | users:admin | create user binding | Submit /backoffice/users/create form |
| Backoffice | POST | `/backoffice/users/roles` | Secure HttpOnly BFF session | users:admin | update user roles | Submit /backoffice/users/roles form |
| Backoffice | POST | `/backoffice/users/status` | Secure HttpOnly BFF session | users:admin | update user status | Submit /backoffice/users/status form |
| Backoffice | GET | `/backoffice/policies` | Secure HttpOnly BFF session | policy:admin | policies page | GET /backoffice/policies |
| Backoffice | POST | `/backoffice/policies/publish` | Secure HttpOnly BFF session | policy:admin | publish policy | Submit /backoffice/policies/publish form |
| Backoffice | GET | `/backoffice/quotas` | Secure HttpOnly BFF session | quotas:admin | quotas page | GET /backoffice/quotas |
| Backoffice | POST | `/backoffice/quotas/publish` | Secure HttpOnly BFF session | quotas:admin | publish quota | Submit /backoffice/quotas/publish form |
| Backoffice | GET | `/backoffice/providers` | Secure HttpOnly BFF session | providers:admin | providers page | GET /backoffice/providers |
| Backoffice | POST | `/backoffice/providers/accounts/create` | Secure HttpOnly BFF session | providers:admin | create provider account | Submit /backoffice/providers/accounts/create form |
| Backoffice | POST | `/backoffice/providers/accounts/status` | Secure HttpOnly BFF session | providers:admin | provider account status | Submit /backoffice/providers/accounts/status form |
| Backoffice | POST | `/backoffice/providers/credentials/create` | Secure HttpOnly BFF session | providers:admin | create provider credential | Submit /backoffice/providers/credentials/create form |
| Backoffice | POST | `/backoffice/providers/credentials/rotate` | Secure HttpOnly BFF session | providers:admin | rotate provider credential | Submit /backoffice/providers/credentials/rotate form |
| Backoffice | POST | `/backoffice/providers/credentials/revoke` | Secure HttpOnly BFF session | providers:admin | revoke provider credential | Submit /backoffice/providers/credentials/revoke form |
| Backoffice | POST | `/backoffice/providers/credentials/preflight` | Secure HttpOnly BFF session | providers:admin | preflight provider credential | Submit /backoffice/providers/credentials/preflight form |
| Backoffice | POST | `/backoffice/providers/capacity` | Secure HttpOnly BFF session | providers:admin | record provider capacity | Submit /backoffice/providers/capacity form |
| Backoffice | POST | `/backoffice/providers/catalog/publish` | Secure HttpOnly BFF session | providers:admin | publish provider catalog | Submit /backoffice/providers/catalog/publish form |
| Backoffice | POST | `/backoffice/providers/pricing/publish` | Secure HttpOnly BFF session | providers:admin | publish provider pricing | Submit /backoffice/providers/pricing/publish form |
| Backoffice | GET | `/backoffice/client-credentials` | Secure HttpOnly BFF session | Authenticated session / route guard | client credentials page | GET /backoffice/client-credentials |
| Backoffice | POST | `/backoffice/client-credentials/issue` | Secure HttpOnly BFF session | Authenticated session / route guard | issue client credential | Submit /backoffice/client-credentials/issue form |
| Backoffice | POST | `/backoffice/client-credentials/rotate` | Secure HttpOnly BFF session | Authenticated session / route guard | rotate client credential | Submit /backoffice/client-credentials/rotate form |
| Backoffice | POST | `/backoffice/client-credentials/revoke` | Secure HttpOnly BFF session | Authenticated session / route guard | revoke client credential | Submit /backoffice/client-credentials/revoke form |
| Backoffice | GET | `/backoffice/intelligence` | Secure HttpOnly BFF session | reports:export, reports:read | intelligence page | GET /backoffice/intelligence |
| Backoffice | POST | `/backoffice/intelligence/export` | Secure HttpOnly BFF session | reports:export | export intelligence | Submit /backoffice/intelligence/export form |
| Backoffice | GET | `/health/live` | None (private process probe) | Authenticated session / route guard | health live | GET /health/live |

## Portal

| Surface | Method | Path | Auth | Role/scope | Purpose | Example |
| --- | --- | --- | --- | --- | --- | --- |
| Portal | GET | `/openapi.json` | Secure HttpOnly BFF session | Authenticated session / route guard | client openapi | GET /openapi.json |
| Portal | GET | `/portal/login` | Login transaction (state/nonce/PKCE) | Authenticated session / route guard | login | GET /portal/login |
| Portal | GET | `/portal/callback` | Login transaction (state/nonce/PKCE) | Authenticated session / route guard | callback | GET /portal/callback |
| Portal | GET | `/portal/select-membership` | Secure HttpOnly BFF session | Authenticated session / route guard | select membership | GET /portal/select-membership |
| Portal | POST | `/portal/select-membership` | Secure HttpOnly BFF session | Authenticated session / route guard | bind membership | Submit /portal/select-membership form |
| Portal | GET | `/portal` | Secure HttpOnly BFF session | portal:read | home | GET /portal |
| Portal | GET | `/portal/sessions` | Secure HttpOnly BFF session | sessions:read, tasks:write | sessions page | GET /portal/sessions |
| Portal | GET | `/portal/sessions/open` | Secure HttpOnly BFF session | sessions:read | open session | GET /portal/sessions/open |
| Portal | POST | `/portal/sessions` | Secure HttpOnly BFF session | tasks:write | create session | Submit /portal/sessions form |
| Portal | GET | `/portal/sessions/{session_id}` | Secure HttpOnly BFF session | sessions:read, tasks:write | session detail | GET /portal/sessions/{session_id} |
| Portal | POST | `/portal/sessions/{session_id}/tasks` | Secure HttpOnly BFF session | tasks:write | create task | Submit /portal/sessions/{session_id}/tasks form |
| Portal | GET | `/portal/tasks` | Secure HttpOnly BFF session | tasks:read | tasks page | GET /portal/tasks |
| Portal | GET | `/portal/tasks/open` | Secure HttpOnly BFF session | tasks:read | open task | GET /portal/tasks/open |
| Portal | GET | `/portal/tasks/{task_id}` | Secure HttpOnly BFF session | tasks:read, tasks:write | task detail | GET /portal/tasks/{task_id} |
| Portal | POST | `/portal/tasks/{task_id}/cancel` | Secure HttpOnly BFF session | tasks:write | cancel task | Submit /portal/tasks/{task_id}/cancel form |
| Portal | GET | `/portal/tasks/{task_id}/result` | Secure HttpOnly BFF session | tasks:read | task result | GET /portal/tasks/{task_id}/result |
| Portal | GET | `/portal/tasks/{task_id}/attempts` | Secure HttpOnly BFF session | tasks:read | task attempts | GET /portal/tasks/{task_id}/attempts |
| Portal | GET | `/portal/tasks/{task_id}/attempts/{attempt_id}/exchanges` | Secure HttpOnly BFF session | tasks:read | attempt exchanges | GET /portal/tasks/{task_id}/attempts/{attempt_id}/exchanges |
| Portal | GET | `/portal/usage` | Secure HttpOnly BFF session | usage:read | usage page | GET /portal/usage |
| Portal | GET | `/portal/estimates` | Secure HttpOnly BFF session | estimates:write | estimates page | GET /portal/estimates |
| Portal | POST | `/portal/estimates` | Secure HttpOnly BFF session | estimates:write, tasks:write | create estimate | Submit /portal/estimates form |
| Portal | GET | `/portal/activity` | Secure HttpOnly BFF session | audit:read | activity page | GET /portal/activity |
| Portal | GET | `/portal/docs` | Secure HttpOnly BFF session | docs:read | documentation page | GET /portal/docs |
| Portal | GET | `/portal/credentials` | Secure HttpOnly BFF session | credentials:read, credentials:write | credentials page | GET /portal/credentials |
| Portal | POST | `/portal/credentials` | Secure HttpOnly BFF session | credentials:write | issue credential | Submit /portal/credentials form |
| Portal | POST | `/portal/credentials/{credential_id}/rotate` | Secure HttpOnly BFF session | credentials:write | rotate credential | Submit /portal/credentials/{credential_id}/rotate form |
| Portal | POST | `/portal/credentials/{credential_id}/revoke` | Secure HttpOnly BFF session | credentials:write | revoke credential | Submit /portal/credentials/{credential_id}/revoke form |
| Portal | GET | `/portal/providers` | Secure HttpOnly BFF session | providers:read | providers page | GET /portal/providers |
| Portal | POST | `/portal/logout` | Secure HttpOnly BFF session | Authenticated session / route guard | logout | Submit /portal/logout form |

## Local IdP

| Surface | Method | Path | Auth | Role/scope | Purpose | Example |
| --- | --- | --- | --- | --- | --- | --- |
| Local IdP | GET | `/dev-idp/authorize` | Signed transaction | LOCAL/TEST | authorize | GET /dev-idp/authorize |
| Local IdP | POST | `/dev-idp/select` | Signed transaction + profile token | LOCAL/TEST | select profile | Submit /dev-idp/select form |
| Local IdP | GET | `/health/live` | None (private process probe) | LOCAL/TEST | health live | GET /health/live |

## Infrastructure

| Surface | Method | Path | Auth | Role/scope | Purpose | Example |
| --- | --- | --- | --- | --- | --- | --- |
| Gateway/API | GET | `/health/live` | none | public loopback | liveness | `Invoke-RestMethod https://localhost:8443/health/live` after CA trust |
| Gateway/API | GET | `/health/ready` | none | public loopback | DB readiness | `Invoke-RestMethod https://localhost:8443/health/ready` |
| Gateway/API | GET | `/openapi.json` | none | public loopback | canonical contract | Open in browser |
| Backoffice/Portal | GET | `/health/ready` | none | private network | process dependency readiness | Compose healthcheck |
| Gateway | GET | `http://127.0.0.1:8080/health/live` | none | container loopback only | Caddy liveness | Compose healthcheck |
| Worker/scheduler | exec | `python -m apps.process_health` | Docker authority | private process | process plus DB probe | Compose healthcheck |

Portal Viewer maps to VIEWER, Operator to DEVELOPER, Admin to OWNER. Viewer cannot mutate. All identities remain constrained to authorized memberships.
