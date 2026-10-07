from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs" / "governance" / "branch-release-protection-policy.md"


def test_branch_release_policy_records_real_settings_and_rc_gate() -> None:
    text = POLICY.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    for required in (
        "branch-release-v1",
        "#162",
        "delete_branch_on_merge=false",
        "allow_auto_merge=false",
        "visible repository rulesets: none",
        "HTTP 403",
        "Pre-RC security pack",
        "Schema compatibility",
        "Supply chain hardening",
        "PostgreSQL restore drill",
        "Availability policy",
        "Observability contract",
        "Operational runbook drills",
    ):
        assert required in text

    assert "not verified" in normalized
    assert "must never force-push or delete `main`" in normalized
    assert "direct commits to `main` are permitted" in normalized
    assert "no prerelease, RC tag, release or GA publication" in normalized
    assert "#162 remains the human/admin gate" in normalized
