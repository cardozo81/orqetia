# Branch and release protection policy

**Policy version:** `branch-release-v1`  
**Issue:** #156  
**External human gate:** #162  
**Product state:** DEVELOPMENT

This policy is compatible with the current single-agent/direct-`main` development
model. It does not claim that GitHub branch protection is enabled when the current
integration cannot verify or write that setting.

## Verified repository snapshot — 2026-10-07

Observed through the available GitHub integration:

- default branch: `main`;
- `delete_branch_on_merge=false`;
- `allow_auto_merge=false`;
- visible repository rulesets: none (`[]`);
- branch protection read for `main`: integration receives HTTP 403
  (`Resource not accessible by integration`);
- the connector exposes no write action for repository settings, rulesets or
  branch protection.

Therefore force-push/delete protection must be treated as **not verified**, not as
enabled.


## Read-only reconciliation — 2026-10-08

At `main` HEAD `29d09c99498573fdc3be662ee59de67984fc4a81`:

- GitHub branch metadata reports `protected=false`. No repository rulesets are visible (`[]`).
- The detailed `main/protection` endpoint still returns HTTP 403 to this integration.
- All 37 GitHub Actions check-runs completed successfully on that HEAD.
- The two retained `codex/165-*` branch tips are ancestors of `main`; both corresponding PRs have been merged. Their deletion is not available through the connected GitHub write actions.

The branch metadata is positive evidence that protection is **not enabled**;
lack of permission to read detailed settings must never be represented as
a successful protection audit. Repository settings remain a human/admin gate.

### Required-check configuration hazard

The titles listed below identify **workflows**, not necessarily the exact
status-check contexts to select in repository branch protection. GitHub checks
are emitted by **jobs** (for example, `baseline` by Security baseline and
`targeted` by Pre-RC security pack). The generic job name `contract`
appears in multiple workflows, so selecting that bare context is ambiguous.

Some of the listed workflows are path-filtered (including Pre-RC security pack,
Schema compatibility, PostgreSQL restore drill and Operational runbook drills).
A path-skipped workflow does not reliably provide a check-run for an unrelated PR.
Do **not** mark a path-filtered/ambiguous job as globally required before
resolving that behavior: it can permanently block normal PR merges.

Before activating repository enforcement, the administrator/development agent
must map required checks to **unique, reliably emitted** job contexts on both
relevant PRs and merge candidates, or first implement an always-running
aggregate verification check without enabling paid execution. Preserve evidence
for the eight release-critical workflow gates below separately when a
particular change does not trigger them. Do not weaken the release criteria to
work around the status-check configuration problem.


## DEVELOPMENT policy

While the project remains DEVELOPMENT and one autonomous agent is the only writer:

- direct commits to `main` are permitted by #47;
- the agent must use non-force ref updates with an expected current SHA;
- the agent must never force-push or delete `main`;
- mandatory human PR review is not required for ordinary DEVELOPMENT commits;
- every implementation unit must have a targeted gate appropriate to its boundary;
- temporary branches are allowed only for an objective technical reason and must
  be deleted after integration;
- because `delete_branch_on_merge=false`, auto-delete cannot currently be relied on.

This is an operational workflow rule, not a substitute for GitHub-enforced branch
protection.

## Minimum checks before first RC

Before #46 may transition from DEVELOPMENT to RC, repository administration should
require the applicable package gates, at minimum:

1. `Security baseline`;
2. `Pre-RC security pack`;
3. `Schema compatibility`;
4. `Supply chain hardening`;
5. `PostgreSQL restore drill`;
6. `Availability policy`;
7. `Observability contract`;
8. `Operational runbook drills`.

Additional boundary-specific checks may remain required when they protect code
touched by the release candidate. Required checks are not permission to run paid
providers.

## Main branch protection target

Before first RC, #162 must be completed using repository-administration access:

- protect `main` against force-push;
- protect `main` against deletion;
- configure required checks from this policy;
- verify any bypass actor is explicit and minimal;
- enable `delete_branch_on_merge=true` if temporary PR branches are part of the
  release workflow;
- record the resulting settings in #162.

A review requirement may be introduced for RC/release approval, but it must not
silently make the established single-agent DEVELOPMENT workflow impossible before
that lifecycle transition.

## Tags and releases

#46 is authoritative:

- commits and merges do not create a public version;
- no prerelease, RC tag, release or GA publication is created automatically;
- a release/tag requires an explicit release issue/decision under #46;
- this policy does not promote the product from DEVELOPMENT.

## Exceptions

An exception to a required check or protection must be explicit, bounded to one
release/change, and record reason, owner, affected control and expiry. Disabling a
gate because it is inconvenient is not an acceptable permanent exception.

## Verification responsibility

The agent can verify repository metadata and visible rulesets but cannot currently
read `main` protection or write repository settings with the available connector.
#162 remains the human/admin gate and blocks first RC/M10 final readiness until the
settings are applied and verified.

## Staged GitHub administration plan — verified 2026-10-09

This is a **configuration plan**, not evidence that enforcement has been
activated. As of this audit `main` remains `protected=false`, rulesets `[]`
and the admin settings write operation is unavailable to the connected agent.
Following completion of #165, the only remote branch is `main`; both
`codex/165-*` branches were removed after their merge into `main`.
The branch HEAD for this inspection was `fe074ee6f8095d0204ca5c33d91c30cd9ac4315d`
with 36 successful checks and no open PRs.

### Phase A — integrity protection without disrupting single-agent DEVELOPMENT

Using an authorized repository administrator in GitHub:
`Settings → Rules → Rulesets → New ruleset → New branch ruleset`

1. Set ruleset name `orqetia-main-integrity-development`; enforcement
   **Active**; target only the default branch (`main`).
2. Enable **Restrict deletions** and **Block force pushes**.
3. Do **not** enable **Restrict updates**, **Require a pull request before
   merging**, **Require status checks**, **Require merge queue**, or a mandatory
   human review yet. These restrictions may prevent the current single-agent
   direct-`main` fast-forward workflow.
4. Do not configure broad bypass actors. If an emergency bypass is objectively
   necessary, name the actor and record its scope, reason and expiry in #162.
5. Save; read back the **active** ruleset and branch metadata, and confirm that
   ordinary non-force direct commits remain permitted. Never test enforcement
   by attempting a destructive force-push or deleting `main`.

This reduces accidental branch destruction risk during DEVELOPMENT, but **does not
close #162 or authorize RC** because release required-check enforcement remains
pending.

### Phase B — first RC requires changing the integration workflow

Before enforcing required checks, migrate the release-candidate integration path
to a short-lived branch + PR. This is a **deliberate exception** to the normal
single-agent direct-`main` policy, justified by the RC gate. Required checks may
block direct pushes because the new commit has not run CI yet.

Current mapping of the eight release-critical **workflow** names to job contexts:

| Release-critical workflow | Status check job context | Present on every ordinary PR to `main`? |
| --- | --- | --- |
| Security baseline | `baseline` | Yes |
| Pre-RC security pack | `targeted` (also used elsewhere) | **No**; path-filtered and ambiguous |
| Schema compatibility | `compatibility` | **No**; path-filtered |
| Supply chain | `artifacts` | **No**; path-filtered |
| PostgreSQL restore drill | `restore` | **No**; path-filtered |
| Availability policy | `availability-contract` | Yes, with unique job ID after the #162 CI update |
| Observability contract | `observability-contract` | Yes, with unique job ID after the #162 CI update |
| Operational runbook drills | `drills` | **No**; path-filtered |

`Phase 1 quality gate` emits the always-on, unambiguous `quality` job as
an additional general-purpose CI gate. Confirm each job name, GitHub Actions
source and successful checks on a **representative PR** after the workflow
renaming before selecting it as required in GitHub settings.

Do **not** globally require `targeted`, `compatibility`, `artifacts`,
`restore` or `drills` until each is always emitted, uniquely identified
and validated for the intended PR/release scope. Options for later implementation:
make each critical workflow unfiltered on PRs (higher CI cost), or implement an
auditable always-running release gate that executes/evaluates these contracts.
Merely adding a lightweight green aggregate without actually checking the
critical controls is **not** acceptable. All eight substantive evidence gates
remain mandatory for any authorized first RC on its candidate revision.

During Phase B configure required checks **only after** the single-agent
release-PR workflow and the complete eight-gate enforcement are proven working.
Keep the minimal release bypass surface explicit. `delete_branch_on_merge=true`
may be enabled when PR branches are actually used. Record read-back evidence,
PR behavior and final checked SHA in #162 and #47, then evaluate #164 and #46.

