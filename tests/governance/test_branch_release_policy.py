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


def test_release_required_check_ids_are_unique_and_always_emitted() -> None:
    workflows = ROOT / ".github" / "workflows"
    for filename, job_id in (
        ("availability-policy.yml", "availability-contract"),
        ("observability.yml", "observability-contract"),
    ):
        text = (workflows / filename).read_text(encoding="utf-8")
        assert f"\n  {job_id}:\n" in text
        assert "\n    paths:" not in text
        assert "\n    paths-ignore:" not in text
        assert "pull_request:" in text
        assert "push:" in text


def test_branch_release_policy_stages_integrity_and_required_checks() -> None:
    text = POLICY.read_text(encoding="utf-8")
    assert "Phase A — integrity protection" in text
    assert "Restrict deletions" in text
    assert "Block force pushes" in text
    assert "Phase B — first RC" in text
    for job in ("quality", "baseline", "availability-contract", "observability-contract"):
        assert f"`{job}`" in text
    assert "does not\nclose #162 or authorize RC" in text

