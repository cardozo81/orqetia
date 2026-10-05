# AGENTS.md

## Mission

Execute ORQETIA work autonomously from a Ready issue through implementation, targeted tests, documentation updates and a validated pull request.

The GitHub repository is the persistent source of truth. Chat/conversation context is never required to resume work.

## Work selection

Use #47 as the canonical execution roadmap.

Do not pick arbitrary backlog items. Select the first Ready issue in the active phase whose dependencies are complete.

Before starting or resuming:
1. read this file;
2. read #47;
3. read the target issue, Parent epic, `Blocked by` and referenced ADRs/contracts;
4. inspect any active branch/PR and the latest `AGENT CHECKPOINT v1`;
5. verify dependencies remain satisfied.

## Hard rules

- Write only to `cardozo81/orqetia`.
- Never mutate the RASAi repository or its GitHub metadata.
- Preserve canonical logic mapped in #31/#34.
- Follow the reuse-first gate in #43.
- If the chosen technical strategy implies near-total canonical rewrite, stop implementation and surface the reasons for human decision.
- Keep tests isolated to the changed boundary by default.
- Do not use paid providers in ordinary tests/CI.
- Do not use GitHub resources that may add financial cost to the owner without explicit human authorization.
- Do not request human smoke for every low-risk delivery.
- Escalate for human decision only at explicit architecture/cost/security gates or functional-package smoke.
- Keep the product state as DEVELOPMENT until #46 changes it.
- Never place secrets, tokens or provider credentials in code, commits, issues, PRs, logs, fixtures or checkpoints.

## Unit of work

Default:
- one Ready issue = one coherent unit of work;
- one branch per issue;
- one PR per coherent unit;
- no opportunistic unrelated refactors;
- scope expansion requires a separate issue/ADR.

## Checkpoint and resume

Follow #42 and `docs/agent-resume-runbook.md`.

Checkpoint before any predictable interruption, context switch, human gate or abandonment of incomplete work.

A checkpoint must contain:
- issue/Parent/roadmap phase;
- state;
- branch;
- base/head SHA;
- PR;
- completed work;
- current boundary;
- pending work;
- tests executed and tests still required;
- docs/OpenAPI/migration status;
- security/privacy/canonical impact;
- risks/blockers;
- next exact action;
- next commands.

The next Agent must be able to resume without reading the prior chat.

## PR completion

A PR should state:
- issue/Parent;
- dependencies satisfied;
- canonical behavior preserved;
- reuse performed;
- tests run and why they are sufficient;
- docs/OpenAPI changes;
- migration/data impact;
- security/privacy impact;
- residual risk;
- whether a human gate is actually required;
- final checkpoint/resume status when relevant.
