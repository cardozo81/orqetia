# M11 Operational Readiness

Status: **DEVELOPMENT**. Roadmap: #47. Parent: #40 / #1. Gap register: #48.
Prepared on 2026-10-10 from #47, #48, #162, #164 and #173.

This pack prepares operational decisions; it does not promote lifecycle #46 or
rename the completed Local Docker Operational Homologation phase of #47.

## Evidence and remaining gates

| Boundary | Evidence | Remaining work |
| --- | --- | --- |
| Repository integrity | #162 Phase A; PR #172 integrated at `cf8c00e6cafcce363dddac3b04fdecc845f9101c`, 41/41 PR checks and 36/36 main checks | Phase B integration decision and administrative enforcement remain open; do not enable required checks by inference |
| Local installation | #170 runtime qualification on 2026-10-09 | Documentation changes alone require no rebuild, restart or migration |
| Concrete backup restore | #171: isolated PostgreSQL restore and extraction of two TARs | Does not establish whole-installation or host-loss recovery; #171 stays closed |
| Five-volume coverage | #173; [volume inventory](../operations/volume-recovery-inventory.md): observed mounts/metadata, OIDC expiry tests and isolated Caddy reconstruction | #174 remains open: destructive retention blocked by restore-freshness proof; [safety specification](../security/local-oidc-marker-retention.md), no collector or operational purge |
| Private security intake | #164: reporting enabled; public button observed | External non-admin verification and explicit owner/fallback acceptance remain pending |
| Independent recovery domain | #48; [host-loss plan](../operations/host-loss-recovery-plan.md) | Destination, custody, retention, costs and isolated drill require a separate decision |

## Execution boundary

Reuse the existing [backup contract](../operations/backup-restore-dr.md),
[incident response](../security/incident-response.md),
[private intake acceptance](../security/private-intake-operational-acceptance.md)
and [resume runbook](../agent-resume-runbook.md). No canonical logic from #31/#34
changes; the #43 reuse-first gate requires no rewrite here. OpenAPI, persistence
schemas and migrations remain unchanged.

Single-agent integration uses main directly as authorized by #47. Verify clean
checkout, remote SHA, dependencies/revalidation issues and current CI before
fast-forward synchronization. Do not force-push or reset unknown work.

Local validation for this pack is limited to:

```text
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/governance/test_m11_operational_readiness_docs.py
```

Existing CI executes its configured checks on push. Confirm every emitted check
belongs to the new HEAD and has completed successfully before calling integration
complete. Do not equate a green CI run with operational host recovery.

The initial documentary commit `36f08e544a0d2f646ec787bd13a2516669f3d87e`
passed 35/35 GitHub checks. Subsequent #173 evidence adds only the observed volume
classification and two isolated OIDC recovery cases; final SHA/check results are
recorded on #173. The Caddy test used disposable tmpfs with trust installation
disabled and no operational mounts. Existing services and original backups were
preserved. Host-loss and #162/#164 gates remain open.

## Completion and handoff

Update #47/#48/#173 with final SHA, check results, the five-volume matrix,
sanitized evidence, residual risks and exact next action under AGENT CHECKPOINT v1.
Close #173 only when its classification/preservation criteria are evidenced.
Any objectively demonstrated functional gap gets a separate issue before code
changes. Keep #162 and #164 open until their own gates are met.

No paid providers, external backup transfer, operational volume modification,
credential/user/certificate changes, trust-store changes, ruleset changes,
`docker compose down -v`, RC, tag, release or RASAi mutation are authorized.
