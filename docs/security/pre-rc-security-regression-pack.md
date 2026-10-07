# Pre-RC targeted security regression pack

**Issue:** #160  
**Pack version:** `security-regression-v1`  
**Baseline:** #53 / `docs/security/security-baseline.md`

This is the small deterministic M9 security pack. It intentionally does not run
the full test suite, call real providers or require a paid security service.

## Control map

| Control | Risk boundary | Targeted evidence |
| --- | --- | --- |
| SEC-001 / SEC-011 | cross-tenant BOLA/BFLA | exact customer membership cannot be escaped by browser-selected owner |
| SEC-002 | browser session/CSRF/step-up | cross-origin mutation rejected; credential write requires recent MFA |
| SEC-003 / SEC-006 | bounded untrusted request | body/JSON complexity limits fail closed |
| SEC-004 / SEC-012 | integration secrets | one-time credential replay never reveals secret again |
| SEC-005 | provider outbound / SSRF | provider endpoint metadata is admin-only and unsafe URLs are rejected |
| SEC-006 | rate/backpressure | rate response is bounded and does not leak identity |
| SEC-009 | security audit | audit is append-only and integrity is reconciled |
| SEC-010 | exceptional condition/replay | client cannot mint scopes it does not own; sensitive mutations remain idempotent |
| SEC-010 / SEC-012 | sanitized evidence | secret is sanitized before persistence and client DTO excludes provider financial/secret fields |
| SEC-008 / SEC-013 | supply chain/provenance | provenance verifies SBOM and locked materials |

## Exact test inventory

~~~text
tests/identity/test_customer_authz.py::test_browser_selected_owner_cannot_escape_exact_membership
tests/portal/test_security_shell.py::test_customer_portal_rejects_cross_origin_mutation
tests/identity/test_customer_authz.py::test_credential_write_requires_recent_mfa_but_read_does_not
tests/identity/test_client_credentials.py::test_issue_idempotency_replays_without_revealing_secret_again
tests/control_plane/test_provider_catalog_admin.py::test_endpoint_metadata_is_admin_only_and_rejects_unsafe_urls
tests/api/test_abuse_controls.py::test_body_and_complexity_limits_fail_closed
tests/api/test_abuse_controls.py::test_http_rate_limit_emits_retry_after_without_identity_leak
tests/api/test_client_credentials.py::test_client_cannot_mint_scope_it_does_not_hold
tests/contracts/test_exchange_evidence_reference.py::ExchangeEvidenceCarryoverTests::test_secret_is_sanitized_before_persistence
tests/contracts/test_exchange_evidence_reference.py::ExchangeEvidenceCarryoverTests::test_client_evidence_dto_contains_no_provider_financial_or_secret_fields
tests/audit/test_immutable_audit.py::test_audit_is_append_only_and_integrity_is_reconciled
tests/security/test_supply_chain.py::test_provenance_verifies_sbom_and_locked_materials
~~~

## Pass criteria

- all selected tests pass in one clean locked environment;
- no provider/network credential is required;
- no test fixture contains a real secret;
- failure of any selected control blocks the M9 security gate;
- a new material trust-boundary gap must update this pack and #48 instead of
  being hidden by adding broad retries.

## Non-goals

- external pentest;
- full pytest on every commit;
- real provider smoke;
- commercial SLA or certification claim.

External pentest/review may still be required by a future release decision, but
it is not simulated by this automated pack.
