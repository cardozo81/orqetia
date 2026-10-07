# M10 technical integration readiness evidence

**Pack:** `m10-readiness-v1`  
**Issue:** #163  
**Product lifecycle:** DEVELOPMENT  
**Release-governance human gate:** #162

This pack records technical integration evidence only. It does **not** declare RC,
GA, production readiness or authorize a release.

## Evidence map

| M10 boundary | Canonical delivery evidence | HEAD targeted check |
| --- | --- | --- |
| API/OpenAPI | #4 / `0873958913a4473909f78c54f5bf62d66bb61c6a` | async/idempotent task contract |
| AuthN/AuthZ + client isolation | #12/#142 | browser-selected owner cannot escape exact membership |
| sessions/tasks/workers | #6/#7/#145 | PostgreSQL task orchestration worker completes AUTO escalation |
| canonical orchestration | #8 / `a9b4b86b3b8eb8775f5c823f3b40ff75efe22984` | EXPLICIT_TARGET remains fixed |
| provider registry | #9 / `a43f4290e7c79ed5f023d749f1edeea754fb5f5a` | public metadata is authorization-scoped and safe |
| usage/accounting | #16 / `55f0f94a7e532e0b4b06bfb2ad14a438564dd6dd` | client projection contains no financial fields |
| estimates | #5 / `ef51216e5263ad045e7e56333260286c0616c92c` | AUTO estimate uses canonical rank without provider call |
| quotas | #17/#146 | hard limit reservation rejects over-limit atomically |
| provider control plane | #59 | credential selection honors account status/priority/expiry |
| Backoffice | #22 | OIDC creates secure server-side session |
| Client Portal | #23 | security shell/navigation remains client-safe |
| security | #160 / `057d61f9384134c5c501617b9ff124eb1ac628a1` | targeted security pack contract remains mapped |
| observability | #154 / `67862ba477b607680fc005ee1ec2b14f33138e41` | versioned retention/cardinality policy |
| developer docs/runbooks | #27/#153 | quickstarts match HTTP model + operational runbook contract |

Provider parity for the synchronous baseline remains defined by the completed Phase
4 adapter set (#124–#134) plus Perplexity/Manus boundaries (#29/#30). New providers
require their own issue/gate; M10 does not equate parity with an arbitrary provider
count.

## Targeted execution

The dedicated workflow runs only the specific cases mapped above. One PostgreSQL
service is used for the durable task-orchestration case; all provider behavior is
synthetic/deterministic. No paid provider or external identity service is used.

## Technical pass criteria

- every selected check passes on one HEAD;
- migrations apply cleanly to the synthetic PostgreSQL service;
- the durable worker test completes without a real provider call;
- client-facing checks preserve no-financial/no-secret boundaries;
- security/observability/docs policies remain importable and contract-consistent;
- no new gap is discovered.

## Residual gates deliberately not closed by this pack

- #162 — repository branch protection/settings require administration access not
  available to the current connector and still block first RC/M10 final release
  governance;
- #28 — visual brand approval is human-gated and non-blocking for technical
  integration;
- #41 and #135 remain FUTURE/outside the first delivery;
- #46 remains authoritative for any future RC/tag/release decision.

A green `m10-readiness-v1` means the implementable technical integration pack is
complete, not that ORQETIA has been promoted from DEVELOPMENT.
