from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECURITY = ROOT / "SECURITY.md"
POLICY = ROOT / "docs" / "security" / "incident-response.md"
RUNBOOK = ROOT / "docs" / "runbooks" / "security-incident.md"


def test_incident_response_policy_covers_issue_57_contract() -> None:
    security = SECURITY.read_text(encoding="utf-8").lower()
    policy = POLICY.read_text(encoding="utf-8").lower()
    runbook = RUNBOOK.read_text(encoding="utf-8").lower()

    assert "do **not** open a public issue" in security
    assert "private vulnerability" in security

    for scenario in (
        "credential compromise",
        "cross-tenant",
        "provider-secret",
        "database exposure",
        "malicious or compromised dependency",
        "ssrf",
        "unauthorized administrative action",
        "session or service-token theft",
        "data-integrity corruption",
    ):
        assert scenario in policy

    for phase in (
        "detect",
        "classify",
        "contain",
        "preserve evidence",
        "revoke and rotate",
        "investigate",
        "remediate",
        "recover",
        "postmortem",
    ):
        assert phase in policy

    assert "3 business days" in policy
    assert "5 years" in policy
    assert "resolução cd/anpd nº 15" in policy
    assert "preliminary" in policy
    assert "complementary" in policy

    for item in (
        "private",
        "sev0",
        "credential",
        "cross-tenant",
        "evidence",
        "3 business days",
        "5 years",
        "postmortem",
    ):
        assert item in runbook


def test_vulnerability_management_has_bounded_private_process() -> None:
    policy = POLICY.read_text(encoding="utf-8").lower()

    for item in (
        "vulnerability management",
        "dependency cves",
        "emergency patch path",
        "coordinated disclosure",
        "github security advisory",
        "time-bounded risk acceptance",
    ):
        assert item in policy

    assert "public issue" in policy
    assert "raw secrets" in policy
