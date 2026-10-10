# Private intake operational acceptance

Status: **DEVELOPMENT**. Issue: #164. Parent: #57. Roadmap: #47; gaps: #48.

The private-reporting feature was enabled and read back on 2026-10-09 according
to #164. Public button visibility alone does not prove external access or human
triage readiness. The gate remains OPEN until both criteria below are satisfied.

## External access acceptance

An authorized coordinator invites a real external non-admin reporter to sign in
normally and follow **Report a vulnerability** for `cardozo81/orqetia`. Confirm
the legitimate private form is accessible without submitting a fictitious report.
Do not create a public advisory, exploit a vulnerability, invent an identity or
send secrets merely to test intake. A robots.txt block is inconclusive.

Record UTC time, repository, non-admin access class, accessible/inaccessible result
and a sanitized evidence reference in #164. Keep personal identities, private
screenshots and report contents out of public issues. A feature-enabled API
response and an administrator's access cannot substitute for this evidence.

## Explicit operational acceptance

Record a real primary owner and fallback/on-call who each explicitly accept
intake and triage responsibility. Record the approved private escalation route,
notification/monitoring arrangement, actual availability and acceptance time.
Do not infer ownership from Git authorship or administrator permissions. Store
private contact details privately and reference the accepted roles in #164.

Review the existing internal SEV0..SEV3 targets in
[incident-response.md](incident-response.md): acknowledgement <= 1 hour,
<= 4 hours, <= 1 business day and <= 3 business days respectively. These are
DEVELOPMENT targets, not a public SLA. If actual coverage cannot meet them, use
an explicit delta/revalidation under #84 before changing the closed #57 contract.

## Intake and closure

Use [SECURITY.md](../../SECURITY.md), the canonical incident policy and
[security incident runbook](../runbooks/security-incident.md) for private triage,
severity/impact assessment, sanitized evidence, targeted remediation and
coordinated disclosure. Personal-data decisions belong to the responsible
privacy/legal roles; this pack adds no legal determination.

Close #164 only after legitimate external verification and both responsibility
acceptances are recorded. Until then it blocks the first RC. #162 administrative
enforcement and explicit lifecycle #46 promotion remain independent gates.
This document assigns nobody and sends no messages or vulnerability reports.
