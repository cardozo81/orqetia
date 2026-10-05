# API authorization matrix — baseline

| Resource/action | Service client | Customer human | Backoffice |
|---|---|---|---|
| POST /sessions | OWN + sessions:write | OWN + permitted role | explicit operational privilege only |
| GET /sessions/{id} | OWN + sessions:read | OWN + role | privileged/audited |
| POST /sessions/{id}/tasks | OWN + tasks:write | OWN + role | explicit operational privilege only |
| GET /tasks/{id} | OWN + tasks:read | OWN + role | privileged/audited |
| GET /tasks/{id}/result | OWN + tasks:read | OWN + role | privileged/audited |
| POST /tasks/{id}/cancel | OWN + tasks:cancel | OWN + role | privileged |
| POST /estimates | OWN + estimates:write | OWN + role | privileged if needed |
| GET /usage | OWN + usage:read | OWN + role | privileged |
| GET /providers | providers:read + public policy | role + public policy | privileged |
| GET /models | models:read + public policy | role + public policy | privileged |
| Manage own client credentials | DENY baseline S2S | administered OWN client + credential privilege | explicit admin/support privilege |
| Assign policy/quota/provider permission | DENY | DENY | fine-grained admin privilege |
| Read provider cost/credit/contract | DENY | DENY | finance/provider privilege |
| Read clear provider secret | DENY | DENY | DENY via ordinary API/UI |

OWN always means authoritative tenant/client ownership, not request-supplied identity.
