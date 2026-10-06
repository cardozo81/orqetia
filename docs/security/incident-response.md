# ORQETIA Incident Response and Vulnerability Management

Issue: #57
Product state: DEVELOPMENT
Security baseline: #53
Privacy/data lifecycle: #55
Key/secret management: #56
Regulatory reference reviewed: 2026-10-06

This is an engineering and operational policy, not legal advice. The competent
controller and privacy/legal roles remain responsible for final regulatory
determinations.

## Covered incident classes

The process covers at minimum:
- credential compromise;
- cross-tenant/client data leak;
- provider-secret leak;
- database exposure;
- malicious or compromised dependency;
- SSRF/outbound compromise;
- unauthorized administrative action;
- browser session or service-token theft;
- data-integrity corruption.

A vulnerability is not automatically an incident. Confirmed exploitation or a
security event affecting confidentiality, integrity, availability or authenticity
may become an incident.

## Roles

Incident Commander owns severity, coordination, the decision log and recovery
approval. SEV0/SEV1 incidents require an explicitly assigned Incident Commander.

Security/Engineering contains the technical path, preserves evidence, performs
root-cause analysis, patches the defect and validates recovery.

Privacy/Data Protection determines whether personal data is affected, assists the
controller-role assessment and coordinates regulatory/data-subject communication.

Service owners revoke and rotate ORQETIA-owned client/provider credentials through
the canonical credential lifecycle. Plaintext secrets are never copied into the
incident record.

## Severity and internal triage targets

These are internal DEVELOPMENT targets, not a contractual public SLA.

| Severity | Typical criteria | Triage target |
| --- | --- | --- |
| SEV0 | active cross-tenant leak, active provider-secret compromise, uncontrolled privileged compromise, destructive integrity incident | immediate escalation; target acknowledgement <= 1 hour |
| SEV1 | credible high-impact exposure with bounded blast radius, stolen privileged session/token, reachable critical dependency flaw | target acknowledgement <= 4 hours |
| SEV2 | contained incident/vulnerability with meaningful impact but no evidence of broad active compromise | target acknowledgement <= 1 business day |
| SEV3 | low-impact weakness or defense-in-depth issue | target acknowledgement <= 3 business days |

Before RC these targets must be backed by named operational ownership/on-call or
revised to a target the deployment can actually meet.

## Canonical workflow

### Detect

Open a private incident record and capture UTC detection time, source, suspected
components, safe correlation/resource identifiers, initial severity and whether
personal data may be involved.

Do not record secrets, raw tokens, exploitable payloads or unnecessary personal data.

### Classify

Assess tenant/client blast radius, privilege, data classification, CIA/authenticity
impact, provider credential impact, ongoing exploitation likelihood, personal-data
involvement, recoverability and evidence quality. Severity may be raised at any
time; lowering it requires a recorded reason.

### Contain

Prefer reversible containment that stops ongoing harm:
- revoke/disable compromised client credentials;
- revoke/rotate provider credentials;
- revoke browser/admin sessions;
- disable an affected provider target/account;
- isolate a compromised worker/service;
- temporarily disable a vulnerable endpoint/feature;
- freeze destructive administrative mutations when integrity is uncertain.

Containment must not bypass tenant authorization, secret boundaries or TLS.

### Preserve evidence

Preserve safe audit events, correlation IDs, timestamps, deployment/commit/image
identity, sanitized exchange evidence, task/session/attempt/work identifiers,
authentication/session metadata, dependency/version evidence and authorized
database snapshot references.

Record collector/owner and hashes where practical. Raw provider secrets and bearer
tokens are not evidence payloads; record safe identifiers/fingerprints and
rotation/revocation events instead.

### Revoke and rotate

Follow #56 and canonical credential services. Revoke exposed material, issue
replacement through approved lifecycle, verify old material is rejected and
rotate dependent credentials when compromise propagation is plausible.

### Investigate

Determine entry point, first/last known action, affected owners/resources/data,
whether information was accessed/altered/destroyed/unavailable, whether the same
defect exists elsewhere and whether logs/evidence remain trustworthy.

Investigation never delays necessary containment.

### LGPD and ANPD assessment

For personal-data incidents, determine ORQETIA's role for the affected processing
(controller, operator or suboperator) rather than assuming one role globally.

Under the currently applicable Resolução CD/ANPD nº 15, de 24 de abril de 2024,
the controller communicates incidents that may cause relevant risk or damage to
data subjects. Relevant-risk assessment includes significant impact on fundamental
interests/rights together with factors such as sensitive data, children/adolescents/
elderly data, financial data, authentication data, protected data or large-scale
processing.

When communication is required, treat the legal deadline as 3 business days from
knowledge that the incident affected personal data, subject to a shorter/specific
deadline in other applicable legislation. Communication to affected data subjects
follows the applicable ANPD rule.

If complete information is unavailable, use the ANPD preliminary/complementary
communication procedure instead of delaying the initial notification.

The controller must keep the incident record, including incidents not reported to
ANPD/data subjects, for at least 5 years from the record date unless another
applicable obligation requires longer retention.

When ORQETIA is an operator/suboperator, promptly provide the controller with facts
and evidence needed for its decision and deadlines. ORQETIA personnel do not make
a legal notification claim on behalf of a controller without authority.

Official references:
- Resolução CD/ANPD nº 15/2024 - Regulamento de Comunicação de Incidente de Segurança.
- ANPD official Communication of Security Incident procedure.

Revalidate the current official ANPD rule before an actual notification.

### Remediate

Patch the root cause through the narrowest safe code/config/dependency/authorization
or data-repair change. Add a targeted regression/security test when technically
possible.

### Recover

Recovery requires effective containment, revoked/replaced compromised material,
integrity reconciliation, safe queue/retry behavior, recurrence monitoring and
Incident Commander approval for SEV0/SEV1 restoration. Restore tests use synthetic
data and secrets.

### Communicate

External communication distinguishes confirmed facts from hypotheses, avoids
exploit details before remediation, uses clear language and is approved by the
responsible controller/privacy/legal role.

### Postmortem

SEV0/SEV1 require a written postmortem. SEV2 requires one when root cause is
systemic or recurrence risk is material. Record timeline, impact, detection gap,
root cause, containment/recovery, tests added and corrective actions with owners.

## Vulnerability management

### Private intake

Use repository private vulnerability reporting/security advisory when available.
Never ask a reporter to publish exploitable details in a public issue. If the UI
does not expose a private vulnerability channel, establish private contact with
the repository owner/maintainers before transferring sensitive details.

### Triage

1. acknowledge privately according to severity;
2. reproduce with synthetic data where possible;
3. determine reachability and affected versions/components;
4. assign severity and owner;
5. decide immediate mitigation;
6. create a private remediation path;
7. coordinate disclosure after remediation.

### Dependency CVEs

Verify the dependency/version is present and reachable. Patch, upgrade or remove
when exploitable or required by policy. Time-bounded risk acceptance follows #53.
A CVSS score does not replace ORQETIA reachability/impact analysis.

### Emergency patch path

A security hotfix preserves targeted regression/security tests, migration
compatibility, secret-safe logs/errors and a traceable commit/advisory. Emergency
response never bypasses tenant authorization or secret boundaries.

### Disclosure and CVE

Use GitHub Security Advisory/CVE facilities when appropriate for an ORQETIA flaw.
Do not claim ownership of a third-party CVE. Coordinated disclosure occurs after
supported code is remediated or mitigation is available unless overriding
safety/legal obligations require otherwise.

## Audit requirements

Preserve safe evidence for severity/owner changes, credential/session revoke/rotate,
administrative containment, recovery approval, regulatory communication decision,
risk acceptance and closure. Incident audit data must not contain raw secrets.

## Closure criteria

Close an incident only when active compromise is contained, root cause is
understood sufficiently, remediation/mitigation is deployed, integrity/recovery
checks pass, required notifications are completed or a documented decision says
they are not required, follow-ups are tracked and retention is established.

## Periodic readiness

Before RC and periodically after:
- tabletop credential compromise and cross-tenant leak;
- verify private vulnerability intake;
- verify emergency credential/session revocation;
- verify synthetic backup/evidence access;
- verify ANPD procedure and deadlines remain current.
