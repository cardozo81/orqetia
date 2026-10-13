# First Release Candidate — PR check evidence and enforcement

**Scope:** issue #162; roadmap #47; lifecycle remains **DEVELOPMENT** until #46 explicitly authorizes a release. This document is a technical readiness runbook, not authorization to issue a tag, prerelease or RC.

## Authorized sequencing — 2026-10-13 (supersedes older #164-first gate)

The responsible party explicitly authorized [independent completion of #162](https://github.com/cardozo81/orqetia/issues/162#issuecomment-6144816554).
**#164 is not a prerequisite for applying #162 administrative enforcement**:
the single-agent PR-based integration and all nine substantive GitHub Actions
required checks may be enabled and verified with existing administrator access.
The old operational condition to finish external private vulnerability intake
**before setting repository rules** no longer applies.

This is **not** permission to close #164 or release the product. #164 remains an
independent **mandatory pre-RC gate** requiring real external non-admin reporter
verification and separately accepted security owner/fallback/on-call. #46 is
another independent lifecycle decision; DEVELOPMENT persists and **no RC, tag
or release** is authorized here. A GitHub ruleset read-back or CI pass cannot
substitute for #164's human acceptance. Do not fabricate identities or evidence.

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

## Previously completed representative PR validation — 2026-10-13

The Phase B **technical PR check-evidence exercise is already DONE**: [PR #172](https://github.com/cardozo81/orqetia/pull/172), head `97dac6539669a52f38b6d8126d7673c6da9bcd9c`, emitted 41/41 successful GitHub Actions check-runs on `pull_request`, including all eight named release contexts below and independent `quality`. It was integrated by squash at `cf8c00e6cafcce363dddac3b04fdecc845f9101c`; branch cleanup was verified. Detailed [#162 evidence](https://github.com/cardozo81/orqetia/issues/162#issuecomment-6086075629).

**Do not repeat the demonstration PR as an outstanding task.** The procedure below is reusable if the actual release candidate or future policy change needs fresh check evidence. A new candidate must pass on its own evaluated SHA; historical #172 results do not approve a new RC revision.

The remaining Phase B gate is **not a missing technical test**: obtain explicit authorization to switch release integration to PRs, apply and read back branch rules with the nine exact required contexts, and verify non-destructive enforcement. #164's external security reporter and accepted real owner/fallback, and the #46 lifecycle decision, remain independent human gates. Before enforcement, direct-`main` DEVELOPMENT continues; after verified enforcement, integration uses PRs with the nine checks. No RC/tag/release is authorized.

## Representative PR evidence (technical dry run; reusable procedure)

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

1. Confirm the explicit #162 sequencing/enforcement authorization and nine real PR job contexts, including GitHub Actions app identity. **Do not wait for #164 to administer #162**; independently keep #164 OPEN as a non-waivable pre-RC operational gate. Obtain #46 release-transition authorization separately before any RC/tag/release. No implied RC from green CI.
2. Designate PR-based integration for release candidates, including an explicit handling strategy for the single-agent author/reviewer and emergency changes. Do not require self-review that GitHub cannot accept.
3. In GitHub repository ruleset administration, read back the existing active Phase A ruleset ID `24785675`, targeting only the default branch, with `deletion` and `non_fast_forward` and zero bypass actors. Preserve those restrictions.
4. Once the PR workflow and all nine exact contexts have been verified, enforce requiring a PR and requiring successful status checks for release integration. Bind checks to the GitHub Actions app; never require a path-skipped workflow. Avoid merge queue until its separate integration checks are actually tested.
5. Enable `delete_branch_on_merge=true` for the new PR integration process. Avoid introducing broad admin bypass or approval rules impossible under a single-agent writer. Document ruleset IDs, precise enforcement settings and read-back before marking #162 complete.
6. Demonstrate the required status-check enforcement on a representative **non-destructive** PR or an objectively verifiable read-back/test. Do not validate anti-deletion/force-push by performing destructive operations on `main`.
7. If the new rule blocks expected integration, do **not** force-push or disable protections silently; stop, document why, and apply a bounded corrective rule change approved for this transition.
8. Only after #162 and #164 are independently closed with evidence may the responsible human authorize #46 to consider a first RC. No tag or release is produced by this runbook.

## Single-command administrative execution (issue #162)

The repository includes an **offline-audited, fail-closed** standard-library helper,
[scripts/apply_162_phase_b.py](../../scripts/apply_162_phase_b.py). It invokes
**only the already authenticated GitHub CLI**. No token is generated, printed,
exported, or committed. Its default mode performs read-only checks.

In the Windows checkout, use Python 3.14 from the existing environment and
the **actual** SHA of current `main` (do not reuse an outdated example SHA).
Make sure Codex has reconciled the checkout before starting:

```console
python scripts/apply_162_phase_b.py --expected-head <40-character-main-SHA>
python scripts/apply_162_phase_b.py --expected-head <40-character-main-SHA> --apply
```

The first command performs **no administrative write** and must report
`DRY-RUN`. Only the second command executes an authorized PUT of ruleset
`24785675` followed, after successful read-back, by a PATCH of
`delete_branch_on_merge=true`. The helper checks the single active ruleset,
absence of bypass, Phase-A deletion/force-push protections, administrator
permissions, exact `main` SHA, green CI, no open PR, and the nine real
substantive job checks on merged PR #172 (GitHub Actions app ID `15368`).
Any discrepancy refuses to write. Re-running after success is idempotent.

The script re-reads the rule and branch immediately before PUT, and verifies
the complete approval-free PR rule, nine distinct check contexts, source app,
and auto-delete after change. If a GitHub call fails, stop; do **not** disable
protection, force-push, delete `main`, or claim completion. A partial
ruleset update without auto-delete may be resumed safely after inspecting
the real settings. No branches, PRs, workflows or Docker services are
created by this helper. The new helper's synthetic regression tests are in
[tests/governance/test_apply_162_phase_b.py](../../tests/governance/test_apply_162_phase_b.py).

**Important:** successful read-back is objective configuration evidence, not
proof of a newly merged candidate. Record the sanitized results in #162,
verify a genuine PR integration if later needed, and close #162 only against
its accepted evidence. The separate #164 external-human security acceptance
and #46 release decision remain **mandatory before RC**. DEVELOPMENT
continues; this command does not tag or release.

## Residual operating risks

- GitHub App connectors may be read-only for branch settings even when ordinary repository metadata is readable. Permission failures are **not** evidence that controls are enabled.
- The synthetic PostgreSQL restore workflow is not evidence that a Windows installation recovers after total host loss. #171 closed its isolated-backup verification; off-host protection and full operational recovery remain separately governed in #48.
- PR check identity and repository settings are control-plane decisions; changing workflows or producing documentation alone does not activate enforcement.
