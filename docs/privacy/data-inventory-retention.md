# Data inventory and retention matrix — #55

This is the M0 engineering inventory. #61 adds the final data-classification/access matrix.

| Category | Personal-data potential | Default persistence | Retention rule owner | Recipients / notes |
|---|---|---|---|---|
| Human identity: IdP subject, name, email, membership | yes | required for active account/membership | Identity/privacy policy | IdP + ORQETIA identity; minimize duplicate profile fields |
| IP / user-agent / login security metadata | yes | security-minimized | Security/privacy policy | security/audit only; avoid indefinite raw retention |
| Tenant/client business metadata | possible | required | Account/product policy | may include human contact data |
| Client credential metadata/fingerprint | possible association to human | required while credential/history needed | Identity/security policy | clear secret excluded |
| Provider secret | not necessarily personal; SECRET | encrypted/secret-store only | Security/provider lifecycle | never privacy export as clear value |
| Prompt/input/context | high; may include sensitive data | only when product function/config requires | Tenant/controller retention policy | may be sent to approved provider |
| Output/result | high/derived | only when product function/config requires | Tenant/controller retention policy | inherits sensitivity from input/use case |
| Session/task metadata | linked to tenant/human/service client | required operationally | Product/privacy policy | subject to minimization |
| Provider attempt/technical usage | may be indirectly personal through actor/task | factual ledger | Accounting/security/privacy policy | raw content excluded |
| Provider cost/internal finance | generally business/internal | factual accounting | Finance/legal policy | Backoffice-only |
| Application logs/traces | often personal identifiers/IP | minimized | Security/operations retention | no raw prompt/output/secret by default |
| Security/admin audit events | personal actor data | required for accountability/security | Security/legal retention | access restricted |
| Privacy request case | yes | required to prove response | Privacy/legal retention | exported dataset not copied into audit |
| Security incident record | yes, potentially sensitive | required where incident exists | regulatory/security | ANPD rule: at least 5 years when applicable |
| CLIENT_ONLY aggregates | can remain personal/tenant-confidential | versioned aggregates | Statistical/product policy | tenant isolated |
| GLOBAL_PUBLIC aggregates | must be non-reidentifying/cohort-safe | versioned published aggregate | Statistical/privacy policy | no raw client data or small single-client buckets |
| Backups | may contain any persisted personal data | bounded immutable backup cycle | DR/privacy policy | restore must reapply deletion/tombstone |

## Retention implementation fields

Each configurable category/purpose must resolve:
- retention_policy_id/version;
- trigger event;
- duration or legal rule;
- hold/exception policy;
- deletion/anonymization method;
- downstream processor handling;
- backup behavior;
- last review date.

"NULL means forever" is prohibited.

## Privacy request state machine

Suggested states:
- RECEIVED;
- IDENTITY_VERIFICATION;
- SCOPING;
- IN_PROGRESS;
- WAITING_CONTROLLER when ORQETIA is operator;
- WAITING_SUBPROCESSOR;
- PARTIALLY_FULFILLED;
- FULFILLED;
- DENIED_WITH_BASIS;
- CANCELLED.

Every terminal state preserves safe evidence and reason category.

## International-transfer record

Per processing/provider route:
- source activity;
- exporter/importer;
- country/region;
- data categories;
- purpose;
- role;
- processing legal-basis reference;
- transfer-mechanism type/reference;
- effective dates;
- contract/DPA reference;
- privacy review status.

No privacy approval -> route cannot be selected for personal-data workloads that require it.
