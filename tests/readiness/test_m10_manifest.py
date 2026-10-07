from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "readiness" / "m10-technical-readiness.md"


def test_m10_manifest_covers_integration_boundaries_and_residual_gates() -> None:
    text = MANIFEST.read_text(encoding="utf-8")

    for boundary in (
        "API/OpenAPI",
        "AuthN/AuthZ + client isolation",
        "sessions/tasks/workers",
        "canonical orchestration",
        "provider registry",
        "usage/accounting",
        "estimates",
        "quotas",
        "provider control plane",
        "Backoffice",
        "Client Portal",
        "security",
        "observability",
        "developer docs/runbooks",
    ):
        assert boundary in text

    for residual in ("#162", "#28", "#41", "#135", "#46"):
        assert residual in text

    normalized = " ".join(text.replace("**", "").split())
    assert "does not declare RC, GA, production readiness" in normalized
    assert "No paid provider or external identity service is used" in normalized
    assert "not that ORQETIA has been promoted from DEVELOPMENT" in normalized
