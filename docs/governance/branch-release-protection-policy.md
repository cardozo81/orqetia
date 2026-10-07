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
