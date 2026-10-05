# ADR — Manus v2 agentic execution boundary

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #30

## Context

Manus v2 is not a synchronous LLM inference surface equivalent to the canonical
ORQETIA ProviderAdapter family. The current API creates asynchronous agent tasks,
exposes a durable task lifecycle, can enter waiting states, can continue background
jobs after the main agent reports stopped, supports stop/resume semantics, and
accounts usage in native credits.

Forcing that lifecycle into the current single-attempt request/response adapter would
erase important external-effect and recovery states and would create unsafe retry,
cancellation, tool and accounting behavior.

## Decision

Manus is **adherent as a future native agentic execution provider family**, but:

- it is not a canonical synchronous LLM provider adapter;
- it is not added to the current LLM Provider Registry candidate pool;
- it does not participate in the current AUTO / EXPLICIT_TARGET LLM selection;
- no Manus execution adapter is implemented in the current Provider Framework phase.

A future ORQETIA Agentic Provider contract must model create, status/event ingestion,
result extraction, stop, reconciliation and native usage as durable operations.

Future orchestration may expose a separate agentic candidate pool only for operations
that explicitly require agentic capability. Agentic targets must never be silently
treated as interchangeable with ordinary LLM generation targets.

## Authentication boundary

The ORQETIA baseline uses a Backoffice-owned Manus API key via
`x-manus-api-key`.

Client OAuth/Open App credentials are outside the baseline because ORQETIA owns
provider subscriptions and secrets. A future OAuth mode, if ever introduced, requires
a separate product/security decision.

Provider credentials, account metadata and credit balances remain Backoffice-only.

## Capability isolation gate

Manus can access capabilities outside plain inference, including connectors, skills,
task references, projects, browser interactions and confirmation-driven actions.

The current v2 contract has an important fail-closed constraint:

- omitted connectors can inherit project/user defaults;
- omitted **or empty** `enable_skills` loads skills enabled in the account;
- `force_skills` can make skills available;
- task references allow reading other tasks;
- projects can carry default connectors/instructions.

Therefore an empty list is **not** a reliable deny-all boundary for skills.

A Manus transport is ineligible until ORQETIA can prove one of these conditions:

1. the provider exposes an explicit deny-all capability contract that ORQETIA can
   enforce per task; or
2. a dedicated Backoffice-owned Manus account/profile is demonstrably isolated with
   no default skills/connectors/projects/browser capability available, and the
   control plane continuously validates that isolation before eligibility.

Until that proof exists, implementation must fail closed rather than assume that
omission or empty arrays remove capabilities.

The baseline also forbids:

- `project_id`;
- task references;
- forced skills;
- external connectors;
- My Browser;
- automatic action confirmations;
- cross-task context inheritance;
- public/team task sharing.

`share_visibility` must remain private and interactive behavior must not introduce
unbounded human/action loops without an explicit ORQETIA policy.

## Durable lifecycle

A future agentic execution must persist the external Manus `task_id` as soon as
creation is known to have succeeded.

Canonical lifecycle requirements include:

- CREATED / DISPATCHING;
- RUNNING;
- WAITING;
- STOP_REQUESTED;
- STOPPED_WITH_BACKGROUND_WORK;
- RECONCILING;
- SUCCEEDED;
- FAILED;
- COMPLETION_UNKNOWN.

A main-agent `stopped` state is not sufficient for completion. ORQETIA must also
observe `has_running_background_jobs=false`. If that field is true or omitted,
completion remains non-terminal until the application deadline or later
reconciliation.

## Stop and cancellation semantics

`task.stop` stops a running task, but a stopped task can be resumed through a later
message. It is therefore a provider stop request, not an irreversible cancellation.

ORQETIA must not map a successful stop request directly to terminal CANCELLED.
Terminalization requires the local policy plus confirmation that background work is
no longer active/unknown.

## Ambiguous task creation and idempotency

The current official `task.create` contract returns a new external task ID after a
successful create. No idempotency-key contract was observed in the current
`task.create` documentation reviewed for this decision.

Accordingly, loss of the create response after dispatch is an ambiguous external
effect. ORQETIA must **not silently re-dispatch task.create**.

The future Agentic Provider contract requires a reconciliation state and explicit
recovery policy. It must not assume deduplication that the provider has not
documented.

## Waiting states and external effects

Agent waiting states can require a user message, configuration/OAuth action, browser
selection or another confirmation.

The baseline must never auto-confirm an unrecognized or externally effective action.
A successful confirmation submission also does not prove that the external action
completed.

A future implementation therefore requires a HumanGate/policy boundary for action
confirmation and must keep the external effect state separate from confirmation
submission.

## Structured output

A task may be created with `structured_output_schema`. The provider performs the
agent run and exposes the extracted structured result after the execution stops.

ORQETIA must still validate that result locally against the durable canonical schema
before accepting/persisting it as successful structured output.

## Event delivery and reconciliation

A future implementation may combine webhooks with polling:

- webhook deliveries require signature verification and replay-window checks;
- webhook/event IDs require durable deduplication;
- polling remains a recovery/fallback mechanism;
- polling/backoff belongs to orchestration/recovery, not to a single transport call;
- event ingestion must tolerate reordering and duplicate delivery.

A webhook indicating a stopping point does not override the background-job completion
rule.

## Native usage and accounting

Manus usage is credit-based, not canonical token usage.

The future adapter/accounting bridge may record observed native usage as
`MANUS_CREDIT`, using provider-reported task credit usage and credit-history records.
Cost/refund/grant events must remain distinguishable.

ORQETIA must not fabricate token counts or convert Manus credits to client-visible
monetary cost without a separately governed accounting rule.

Provider monetary cost and credit balance remain internal Backoffice data.

## Rate limits

Manus v2 enforces endpoint-specific, per-user rate limits. Retry/backoff/poll cadence
therefore belongs to the future agentic orchestration layer. The transport must not
embed autonomous retry loops that could duplicate agentic work.

## Implementation gate

The current Provider Framework **does not implement Manus execution**.

Implementation is permitted only after all of these are true:

1. canonical Agentic Provider lifecycle contract exists;
2. durable external-task reconciliation and ambiguity semantics exist;
3. tool/skill/connector isolation can be proven fail-closed;
4. webhook/poll event deduplication and signature validation are defined;
5. action confirmation has an explicit policy/HumanGate;
6. native `MANUS_CREDIT` accounting is modeled without exposing internal cost;
7. ordinary CI can validate all behavior offline with deterministic doubles.

## Follow-up work

This ADR creates future work units for:

- canonical Agentic Provider contract and durable lifecycle;
- Manus v2 isolated transport/account capability gate;
- Manus webhook/poll reconciliation, structured result and stop/resume semantics;
- native `MANUS_CREDIT` usage/accounting reconciliation.

These follow-ups are not blockers for the current synchronous Provider Framework
baseline.

## Characterization sources

This decision was revalidated on 2026-10-05 against current official Manus API v2
documentation for task creation, lifecycle, task detail, task stop, structured output,
authentication/Open App scopes, connectors/skills, rate limits, webhooks/security,
action confirmation and usage/credits.

RASAi historical #2 remains read-only requirements input. RASAi is not modified and
is not a runtime/build dependency.
