from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    ROOT / "docs/readiness/m11-operational-readiness.md",
    ROOT / "docs/security/private-intake-operational-acceptance.md",
    ROOT / "docs/operations/volume-recovery-inventory.md",
    ROOT / "docs/operations/host-loss-recovery-plan.md",
)


def test_readiness_references_resolve_and_preserve_lifecycle() -> None:
    for document in DOCUMENTS:
        text = document.read_text(encoding="utf-8")
        assert "DEVELOPMENT" in text
        assert "#47" in text
        assert "#48" in text
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if "://" not in target:
                assert (document.parent / target).is_file(), (document.name, target)


def test_recovery_matrix_covers_exact_compose_volumes_and_mounts() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    declarations = compose.split("\nvolumes:\n", 1)[1].split("\nnetworks:", 1)[0]
    volumes = set(re.findall(r"^  ([a-z_]+):", declarations, re.MULTILINE))
    inventory = DOCUMENTS[2].read_text(encoding="utf-8")
    matrix = inventory.split("## Recovery matrix", 1)[1].split("\nThe mount topology", 1)[0]
    rows = re.findall(r"^\| `([a-z_]+)` \| (.+)$", matrix, re.MULTILINE)
    assert len(rows) == len(volumes) == 5
    assert {name for name, _ in rows} == volumes
    for name, row in rows:
        mounts = set(re.findall(rf"- {name}:([^\s:]+)", compose))
        assert mounts
        assert all(f"`{mount}`" in row for mount in mounts)


def test_human_acceptance_and_recovery_evidence_are_separate() -> None:
    intake = " ".join(DOCUMENTS[1].read_text(encoding="utf-8").split())
    recovery = " ".join(DOCUMENTS[3].read_text(encoding="utf-8").split())
    inventory = " ".join(DOCUMENTS[2].read_text(encoding="utf-8").split())
    for criterion in (
        "external non-admin", "without submitting a fictitious report",
        "explicitly accept", "fallback/on-call", "#162", "#46",
    ):
        assert criterion in intake
    for criterion in (
        "independent destination/failure domain", "separate recovery-key custody",
        "not measurements", "after authorization", "#171 stays closed",
    ):
        assert criterion in recovery
    assert "300-second" in inventory
    assert "equality is still accepted" in inventory
    assert "No operational purge is authorized" in inventory
    assert "14 were OIDC markers" in inventory
