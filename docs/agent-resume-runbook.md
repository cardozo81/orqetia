# Agent Checkpoint & Resume Runbook

## Purpose

Make every ORQETIA implementation unit resumable after an Agent interruption, context reset or handoff.

GitHub state — issue, branch, commits, PR, CI and checkpoint — is authoritative. Conversation history is not.

## Operational states

- `READY`: dependencies complete; work may start.
- `IN_PROGRESS`: branch exists and implementation is active.
- `CHECKPOINTED`: work is persisted and safe to resume.
- `BLOCKED_HUMAN`: a real human gate is documented.
- `PR_OPEN`: implementation is complete enough for validation/review.
- `DONE`: acceptance criteria are satisfied and the result is integrated/recorded.

## Start procedure

1. Read `AGENTS.md`.
2. Read roadmap #47 and determine the active phase.
3. Read the target issue, Parent, blockers and architectural/security references.
4. Confirm all blockers are closed or otherwise explicitly satisfied.
5. Search for an existing branch/PR/checkpoint for the issue.
6. If none exists, create a dedicated branch.
7. Work only inside the issue boundary.

## Checkpoint format

Post the following in the issue or PR whenever work must stop:

```text
AGENT CHECKPOINT v1

Issue: #N
Parent: #N
Roadmap phase: ...
State: IN_PROGRESS | CHECKPOINTED | BLOCKED_HUMAN | PR_OPEN
Branch: ...
Base SHA: ...
Head SHA: ...
PR: ... | none

Completed:
- ...

Current boundary:
- ...

Pending:
- ...

Tests executed:
- command => result

Tests still required:
- ...

Docs/OpenAPI/migrations:
- ...

Security/privacy/canonical impact:
- ...

Known risks/blockers:
- ...

Next exact action:
- ...

Next commands:
- ...
```

## Checkpoint rules

- Record facts, not narrative.
- Commit coherent work before a predictable interruption.
- Intermediate commits do not imply issue completion.
- Explicitly list tests not executed.
- Never include secrets.
- Always identify the exact next action.
- Use verifiable branch/SHA/PR identifiers.

## Resume procedure

1. Read `AGENTS.md`, #47, Parent and target issue.
2. Read the latest checkpoint.
3. Verify branch, base SHA, head SHA and PR.
4. Compare branch against its current base.
5. Review existing commits and diff.
6. Re-check blocker state and any architectural decision updated since checkpoint.
7. Re-run the smallest deterministic test that proves the resume point is still valid.
8. Continue from `Next exact action`.

If branch/diff/checkpoint disagree materially, do not infer intent. Reconstruct the state from Git history, record a corrected checkpoint, then continue.

## Human gate

A human gate is valid only for:
- unresolved material architecture decision;
- contradictory requirements;
- high risk not mitigable by deterministic/simulated testing;
- unavoidable real credential/environment;
- potential financial cost;
- explicit functional-package/RC homologation.

Before stopping at a human gate, persist a checkpoint with the decision required, alternatives, impact, recommendation and safe resume state.

## Completion

Before declaring DONE:
- acceptance criteria are satisfied;
- targeted tests pass;
- docs/OpenAPI/migrations are synchronized;
- security/privacy/canonical impact is reviewed;
- residual risk is recorded;
- no secret entered repository history;
- PR/result is traceable from the issue.
