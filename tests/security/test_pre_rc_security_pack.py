from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs" / "security" / "pre-rc-security-regression-pack.md"


def test_security_pack_maps_material_m9_controls_to_targeted_tests() -> None:
    text = PACK.read_text(encoding="utf-8")

    for control in (
        "SEC-001",
        "SEC-002",
        "SEC-003",
        "SEC-004",
        "SEC-005",
        "SEC-006",
        "SEC-008",
        "SEC-009",
        "SEC-010",
        "SEC-011",
        "SEC-012",
        "SEC-013",
    ):
        assert control in text

    for case in (
        "test_browser_selected_owner_cannot_escape_exact_membership",
        "test_customer_portal_rejects_cross_origin_mutation",
        "test_credential_write_requires_recent_mfa_but_read_does_not",
        "test_issue_idempotency_replays_without_revealing_secret_again",
        "test_endpoint_metadata_is_admin_only_and_rejects_unsafe_urls",
        "test_body_and_complexity_limits_fail_closed",
        "test_http_rate_limit_emits_retry_after_without_identity_leak",
        "test_client_cannot_mint_scope_it_does_not_hold",
        "test_secret_is_sanitized_before_persistence",
        "test_client_evidence_dto_contains_no_provider_financial_or_secret_fields",
        "test_audit_is_append_only_and_integrity_is_reconciled",
        "test_provenance_verifies_sbom_and_locked_materials",
    ):
        assert case in text

    assert "does not run the full test suite" in text
    assert "no provider/network credential is required" in text
