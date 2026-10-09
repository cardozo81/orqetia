# First Release Candidate — PR check evidence and enforcement

**Scope:** issue #162; roadmap #47; lifecycle remains **DEVELOPMENT** until #46 explicitly authorizes a release. This document is a technical readiness runbook, not authorization to issue a tag, prerelease or RC.

## Contexts that must succeed

The following are **GitHub Actions job/check-run names**, not workflow display names. All eight must run their original substantive steps on every PR targeting `main`, and the separate quality gate must also succeed.

| Gate | GitHub Actions workflow | Exact check-run/job context |
| --- | --- | --- |
| Security baseline | `.github/workflows/security-baseline.yml` | `baseline` |
| Pre-RC security pack | `.github/workflows/pre-rc-security-pack.yml` | `pre-rc-security` |
| Schema compatibility | `.github/workflows/schema-compatibility.yml` | `schema-compatibility` |
| Supply chain | `.github/workflows/supply-chain.yml` | `supply-chain` |
| PostgreSQL restore drill | `.github/workflows/postgres-restore-drill.yml` | `postgres-restore-drill` |
| Availability policy | `.github/workflows/availability-policy.yml` | `availability-contract` |
| Observability contract | `.github/workflows/observability.yml` | `observability-contract` |
| Operational runbook drills | `.github/workflows/operational-runbooks.yml` | `operational-runbooks` |

Additional mandatory general check: `quality` from Phase 1 quality gate. Do not select generic `targeted` or `contract` in branch rules: they can be ambiguous. A successful aggregate/proxy cannot replace substantive release jobs.

## Representative PR evidence (technical dry run)

1. Use a legitimate, bounded #162 documentation/governance change on one short-lived branch. Do **not** make an empty/no-op PR merely to obtain green checks. Inspect the diff; do not change the application, providers, deployment or secrets to run this dry run.
2. Open PR into `main`; wait for the PR-triggered workflows. Do not activate required status checks in repository rules while DEVELOPMENT still uses direct writes to `main`.
3. Read PR metadata, head commit and the relevant check-runs/statuses, including the synthetic merge revision if Actions runs against `refs/pull/<PR>/merge`. Record which ref/SHA was actually tested; do not claim that a check on a synthetic merge commit ran on a different SHA.
4. Confirm that all eight exact contexts **and** `quality` have `status=completed`, `conclusion=success`, source app `github-actions`, and substantive run steps. Uniqueness must be verified for the check source and evaluated revision. Failure, skipped, neutral, stale, absent, cancelled or ambiguous check cannot be counted as passing.
5. Keep CI on the release candidate's actual revision; any candidate revision change requires revalidation. Capture non-secret links to runs, exact SHA(s), date, reporter identity and all nine conclusions in #162.
6. Merge the genuine documentation change only after the applicable PR CI is green. Preserve the normal single-agent DEVELOPMENT workflow. Delete its temporary branch with SHA/merge verification; if automated deletion is unavailable, record a precise cleanup action rather than altering GitHub Actions credentials.

Use existing administrator credentials via `gh` only when administration is actually authorized. For read-only evidence:

```bash
gh pr view <PR> --repo cardozo81/orqetia --json number,baseRefName,headRefName,headRefOid,mergeCommit,statusCheckRollup
gh api "repos/cardozo81/orqetia/pulls/<PR>"
gh api "repos/cardozo81/orqetia/commits/<CHECKED_SHA>/check-runs?per_page=100"
```

Cross-check PR-triggered workflow run event, revision, job ID and app; GitHub may report a PR test run against its temporary merge ref. Never print tokens or private PR data.

## Administrative enforcement, only when RC integration is explicitly authorized

1. Confirm #164's private reporter access, real accepted security owner and on-call fallback; obtain #46 release-transition authorization separately. No implied RC from green CI.
2. Designate PR-based integration for release candidates, including an explicit handling strategy for the single-agent author/reviewer and emergency changes. Do not require self-review that GitHub cannot accept.
3. In GitHub repository ruleset administration, read back the existing active Phase A ruleset ID `24785675`, targeting only the default branch, with `deletion` and `non_fast_forward` and zero bypass actors. Preserve those restrictions.
4. Once the PR workflow and all nine exact contexts have been verified, enforce requiring a PR and requiring successful status checks for release integration. Bind checks to the GitHub Actions app; never require a path-skipped workflow. Avoid merge queue until its separate integration checks are actually tested.
5. Enable `delete_branch_on_merge=true` for the new PR integration process. Avoid introducing broad admin bypass or approval rules impossible under a single-agent writer. Document ruleset IDs, precise enforcement settings and read-back before marking #162 complete.
6. Demonstrate the required status-check enforcement on a representative **non-destructive** PR or an objectively verifiable read-back/test. Do not validate anti-deletion/force-push by performing destructive operations on `main`.
7. If the new rule blocks expected integration, do **not** force-push or disable protections silently; stop, document why, and apply a bounded corrective rule change approved for this transition.
8. Only after #162 and #164 are independently closed with evidence may the responsible human authorize #46 to consider a first RC. No tag or release is produced by this runbook.

## Residual operating risks

- GitHub App connectors may be read-only for branch settings even when ordinary repository metadata is readable. Permission failures are **not** evidence that controls are enabled.
- The synthetic PostgreSQL restore workflow is not evidence that a Windows installation recovers after total host loss. #171 closed its isolated-backup verification; off-host protection and full operational recovery remain separately governed in #48.
- PR check identity and repository settings are control-plane decisions; changing workflows or producing documentation alone does not activate enforcement.
