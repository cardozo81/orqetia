from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "operational-recovery.md"


def test_operational_runbook_covers_required_drills_and_safe_commands() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for drill in (
        "OPS-01",
        "OPS-02",
        "OPS-03",
        "OPS-04",
        "OPS-05",
        "OPS-06",
        "OPS-07",
        "OPS-08",
    ):
        assert drill in text

    for required in (
        "scripts/postgres_restore_drill.sh",
        "test_orphan_dispatch_becomes_ambiguous_without_redispatch",
        "test_rotation_persistence_failure_preserves_old_secret_and_metadata",
        "test_browser_selected_owner_cannot_escape_exact_membership",
        "test_required_property_change_is_breaking",
        "test_incident_response_policy_covers_issue_57_contract",
        "test_worker_drain_stops_new_claims",
        "Retry-After",
        "synthetic_data_only: true",
    ):
        assert required in text

    lowered = text.lower()
    assert "never use a real provider key in ci" in lowered
    assert "never include raw secrets" in lowered
    assert "do not manually ack work" in lowered
