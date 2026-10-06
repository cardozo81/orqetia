# Runbook — Security Incident

Use this checklist for an active ORQETIA security incident. Detailed policy:
docs/security/incident-response.md.

## First actions

1. create or use a private incident record; do not publish exploit details;
2. record UTC detection time and assign SEV0..SEV3;
3. assign an Incident Commander for SEV0/SEV1;
4. identify affected tenant/client/provider/security boundary;
5. contain ongoing harm before broad cleanup.

## Containment by scenario

| Scenario | Immediate safe actions |
| --- | --- |
| client credential/token theft | revoke credential/session; identify affected owner/resources; issue replacement only through canonical lifecycle |
| provider secret compromise | disable/revoke affected provider credential; rotate through #56/#59; verify old secret rejection |
| cross-tenant leak | stop affected endpoint/worker/read path; preserve ownership/correlation evidence; negative-test another owner before recovery |
| database exposure | isolate access path; preserve DB/audit evidence; rotate reachable credentials/keys according to blast radius |
| malicious dependency | stop vulnerable deployment path if reachable; pin/remove/upgrade; preserve version/provenance evidence |
| SSRF/outbound compromise | disable affected outbound path/adapter; restrict endpoint/network boundary; review credential exposure |
| unauthorized admin action | revoke admin session/binding as authorized; preserve audit trail; reconcile mutated state |
| session theft | revoke server-side session; require reauthentication/step-up; investigate identity/membership effects |
| integrity corruption | stop writes/replay that amplify corruption; preserve snapshot; reconcile from authoritative records/backups |

## Evidence checklist

Preserve safe identifiers:
- correlation IDs;
- task/session/attempt/work IDs;
- credential UUID/fingerprint/version, never raw secret;
- identity/membership/admin IDs;
- timestamps;
- commit/image/deployment version;
- sanitized exchange evidence;
- audit events;
- backup/snapshot reference.

Do not paste raw tokens, provider keys, passwords or unnecessary personal data into
the incident record.

## LGPD checkpoint

If personal data may be affected:
1. determine ORQETIA/controller/operator role for the processing;
2. record when the controller learned personal data was affected;
3. assess relevant risk/damage;
4. escalate to privacy/legal/controller owner;
5. where communication is required, treat the ANPD/data-subject deadline as
   3 business days under the currently applicable Resolução CD/ANPD nº 15/2024,
   unless another specific legal deadline overrides it;
6. use preliminary/complementary communication if facts are incomplete;
7. retain the incident record for at least 5 years, subject to longer obligations.

Revalidate the current rule against the official ANPD source before notification.

## Recovery checklist

- root cause patched or mitigated;
- compromised credentials/sessions revoked;
- old secret/token rejected;
- tenant/client negative authorization check passes when relevant;
- data integrity reconciled;
- queues/retries cannot replay harmful side effects;
- monitoring can detect recurrence;
- SEV0/SEV1 recovery approved by Incident Commander.

## After recovery

- complete required communication;
- write postmortem for SEV0/SEV1;
- add targeted regression/security test;
- update threat model/baseline/runbook when needed;
- create follow-up issues with owners;
- coordinate public disclosure only after it is safe.
