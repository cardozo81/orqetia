# Data classification enforcement tests — #61

## Client-facing serialization

- [ ] client task/usage schemas do not contain provider cost/currency/balance;
- [ ] client task/usage schemas do not contain provider_credential_id/fingerprint;
- [ ] client credential metadata may show own safe fingerprint, never clear secret;
- [ ] cross-tenant resource serialization fails before field filtering;
- [ ] hidden UI element is not used as the only field-security control.

## Backoffice RBAC/ABAC

- [ ] provider operator without finance privilege cannot read provider cost/balance;
- [ ] finance role cannot resolve provider secret;
- [ ] security role cannot gain finance/provider-secret access by role confusion;
- [ ] RESTRICTED export/read is audited;
- [ ] client-support access to CLIENT_PRIVATE data is purpose/privilege bound.

## Logs/traces/errors

- [ ] SECRET canary never appears in log/trace/error;
- [ ] raw client prompt/output is absent by default;
- [ ] provider raw error body is sanitized;
- [ ] API error does not reveal SQL/stack/cross-tenant metadata;
- [ ] log-access role is not treated as universal data-reader.

## Queue/events/cache

- [ ] provider secret replaced by SecretRef;
- [ ] event with CLIENT_PRIVATE data preserves tenant/client scope;
- [ ] cache key collision cannot return another tenant's CLIENT_PRIVATE data;
- [ ] stale authorization cache cannot preserve revoked privilege beyond allowed version/TTL;
- [ ] cache failure does not widen authorization.

## Exports

- [ ] export uses explicit field allowlist;
- [ ] client export cannot include RESTRICTED/CONFIDENTIAL provider finance;
- [ ] client export cannot include another tenant/client;
- [ ] administrative sensitive export creates audit event;
- [ ] spreadsheet export neutralizes formula-injection cells;
- [ ] export artifact access expires/is revoked according to class policy.

## GLOBAL_PUBLIC

- [ ] raw CLIENT_PRIVATE data is rejected;
- [ ] pseudonymous tenant/client identifier alone is not accepted as anonymization;
- [ ] sparse cohort fails publication threshold;
- [ ] approved PUBLIC aggregate contains no provider-secret/commercial/client identifiers.

## Development/test

- [ ] production SECRET/CLIENT_PRIVATE dataset is not required by tests;
- [ ] synthetic fixtures cover all data classes safely.
