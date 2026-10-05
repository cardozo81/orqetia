# AGENTS.md

## Mission

Execute ORQETIA work autonomously from a Ready issue through implementation, targeted tests, documentation updates and a validated pull request.

## Work selection

Use #47 as the canonical execution roadmap.

Do not pick arbitrary backlog items. Select the first Ready issue in the active phase whose dependencies are complete.

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

## PR completion

A PR should state:
- issue/Parent;
- dependencies satisfied;
- canonical behavior preserved;
- reuse performed;
- tests run and why they are sufficient;
- docs/OpenAPI changes;
- migration/data impact;
- security impact;
- residual risk;
- whether a human gate is actually required.
