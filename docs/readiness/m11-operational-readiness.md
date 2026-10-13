# M11 Operational Readiness

Status: **DEVELOPMENT**. Roadmap: #47. Parent: #40 / #1. Gap register: #48.
Prepared on 2026-10-10 from #47, #48, #162, #164 and #173.
**Current-state reconciliation:** 2026-10-13, after completion of #162/#174 and integration of [PR #179](https://github.com/cardozo81/orqetia/pull/179). Earlier readiness checkpoints below remain historical unless updated explicitly.

This pack prepares operational decisions; it does not promote lifecycle #46 or
rename the completed Local Docker Operational Homologation phase of #47.

## Evidence and remaining gates

| Boundary | Evidence | Remaining work |
| --- | --- | --- |
| Repository integrity | #162 CLOSED; ruleset `24785675` requires PRs and nine GitHub Actions checks (app ID `15368`), with no bypass; [verified execution](https://github.com/cardozo81/orqetia/issues/162#issuecomment-6150369672). PR #179 integrated at `ac4f406a895277a39b316c54729b17afe0040f91`, 40/40 PR checks and 35/35 `main` checks. | Enforcement complete; preserve required checks on each new PR. Separate gate #164 and explicit lifecycle #46 remain before RC. |
| Local installation | #170 runtime qualification on 2026-10-09 | Documentation changes alone require no rebuild, restart or migration |
| Concrete backup restore | #171: isolated PostgreSQL restore and extraction of two TARs | Does not establish whole-installation or host-loss recovery; #171 stays closed |
| Five-volume coverage | #173 CLOSED; [volume inventory](../operations/volume-recovery-inventory.md): observed mounts/metadata, OIDC expiry tests and isolated Caddy reconstruction. #174 Option B is CLOSED, with CI and Windows Docker acceptance [recorded separately](https://github.com/cardozo81/orqetia/issues/174#issuecomment-6143500792). | Historical markers preserved; no collector or purge. This does not prove independent off-host recovery or measured RPO/RTO. |
| Private security intake | #164: reporting enabled; public button observed | External non-admin verification and explicit owner/fallback acceptance remain pending |
| Independent recovery domain | #48; [host-loss plan](../operations/host-loss-recovery-plan.md) | Destination, custody, retention, costs and isolated drill require a separate decision |

## Execution boundary

Reuse the existing [backup contract](../operations/backup-restore-dr.md),
[incident response](../security/incident-response.md),
[private intake acceptance](../security/private-intake-operational-acceptance.md)
and [resume runbook](../agent-resume-runbook.md). No canonical logic from #31/#34
changes; the #43 reuse-first gate requires no rewrite here. OpenAPI, persistence
schemas and migrations remain unchanged.

Single-agent development now integrates through short-lived PRs into protected `main`.
Verify clean checkout, remote SHA, dependencies/revalidation issues, nine required
checks on the evaluated PR revision, and CI on the resulting `main` HEAD. Update
the local checkout only by safe fast-forward when appropriate. Never force-push,
bypass required checks, disable the ruleset or discard unknown local work.

Local validation for this pack is limited to:

```text
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/governance/test_m11_operational_readiness_docs.py
```

The repository runs required checks on PRs and configured checks on push.
Confirm every required PR check is from GitHub Actions on the evaluated revision,
and every emitted final-HEAD check completed successfully before calling the
integration complete. Do not equate a green CI run with operational host recovery.

The initial documentary commit `36f08e544a0d2f646ec787bd13a2516669f3d87e`
passed 35/35 GitHub checks. Subsequent #173 evidence adds only the observed volume
classification and two isolated OIDC recovery cases; final SHA/check results are
recorded on #173. The Caddy test used disposable tmpfs with trust installation
disabled and no operational mounts. Existing services and original backups were
preserved. **Historical note:** host-loss and #164 remain open; #162 is now CLOSED.

## Completion and handoff

The original #173 handoff was completed and the issue is CLOSED. Subsequent
readiness/documentation reconciliations belong to the live #47/#48 registers,
with precise PR and check evidence under AGENT CHECKPOINT v1. Any objectively
demonstrated functional gap requires its own issue before code changes.
#162 is CLOSED; #164 remains OPEN until real external/human acceptance is proved.

The previously approved #174 Option B runtime update was completed and
homologated in Docker under its own checkpoint. This documentary reconciliation
does not authorize another runtime update or fresh access to the local host.
No paid providers, external backup transfer, destructive operational volume modification,
credential/user/certificate changes, trust-store changes, ruleset changes,
`docker compose down -v`, RC, tag, release or RASAi mutation are authorized.
