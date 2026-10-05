# Durable Task State Machine — #7

## Canonical status contract

The authoritative task states are the values already published by the v1 OpenAPI:

| State | Terminal | Meaning |
|---|---:|---|
| `CREATED` | no | Task metadata is committed but work is not yet admitted to the durable queue. |
| `QUEUED` | no | Task is ready or scheduled for durable execution/re-execution. |
| `RUNNING` | no | Logical execution is active. |
| `CANCELLING` | no | Cancellation won the state race while work was active and awaits safe finalization. |
| `COMPLETE` | yes | All task requirements are accepted. |
| `PARTIAL` | yes | Execution ended with both accepted and missing requirements. |
| `UNAVAILABLE` | yes | No eligible/available execution path remains. |
| `FAILED` | yes | Execution ended on an unrecoverable logical/internal failure. |
| `CANCELLED` | yes | Cancellation was durably finalized. |

`PENDING` and `BLOCKED` are deliberately not separate task states. Durable wait, delay,
Retry-After and recoverable scheduling conditions use `QUEUED` plus reason/scheduler metadata.
This prevents an internal enum from diverging from the canonical client API.

## Transition table

| From | Allowed destinations |
|---|---|
| `CREATED` | `QUEUED`, `CANCELLED` |
| `QUEUED` | `RUNNING`, `CANCELLED`, `FAILED` |
| `RUNNING` | `QUEUED`, `COMPLETE`, `PARTIAL`, `UNAVAILABLE`, `FAILED`, `CANCELLING` |
| `CANCELLING` | `CANCELLED` |
| terminal states | none |

Returning `RUNNING -> QUEUED` represents durable rescheduling; it does not itself decide
provider retry policy. #15 owns worker/scheduler mechanics and #8 owns logical provider
retry/cycle/fallback rules.

## Concurrency and cancellation

Every task has a monotonic `version`. State mutations use compare-and-set predicates containing
the expected version and current status.

For completion versus cancellation:

1. both writers may observe the same `RUNNING` version;
2. only one conditional UPDATE can advance that version;
3. a completion winner terminalizes the task;
4. a cancellation winner moves it to `CANCELLING`, after which only `CANCELLED` is valid;
5. the stale writer receives a failed CAS and cannot resurrect or overwrite the winner.

An append-only `execution.task_transitions` row is written in the same transaction as each
successful state transition. Initial creation is recorded as `NULL -> CREATED` at version 1.

## Ownership

`execution.tasks` is owned by the Execution bounded context. Every row carries
`tenant_id` and `client_id`.

The task has a composite same-context foreign key
`(session_id, tenant_id, client_id)` to `execution.execution_sessions`. Repository reads and
mutations always require the full tenant/client ownership scope.

Creation also verifies:

- the parent session exists for the same owner;
- the session is non-terminal;
- the task effective policy version equals the session effective policy snapshot.

No task lookup authorizes access by UUID alone.

## Execution selection

The task snapshots the request contract without implementing routing:

### AUTO

- `requested_execution_mode = AUTO`;
- `requested_target = null`;
- `effective_target = null`;
- actual provider/model/reasoning is recorded per future provider attempt.

The task therefore does not pretend that one target is globally effective across AUTO
cost-escalation or cycles.

### EXPLICIT_TARGET

- the sanitized requested target is stored;
- the fully resolved authorized effective target is stored before dispatch;
- the state-machine adapter exposes no mutation path for either target;
- each recorded cycle rejects candidate-order entries that differ from the frozen target.

This persists the #21/#80 invariant without implementing provider selection.

## Requirements, result and safe references

The task persists a generic requirement partition:

- `requirements`;
- `accepted_requirements`;
- `missing_requirements`.

Accepted and missing sets are disjoint and together must equal the original requirements.
`COMPLETE` requires no missing requirements. `PARTIAL` requires both accepted and missing
requirements.

Raw prompt/input/context/schema/result bodies are **not** embedded in the task row. #48 still
tracks the unresolved physical storage choice for large payloads. #7 stores:

- input reference + SHA-256 fingerprint;
- optional context/schema references;
- immutable result reference for `COMPLETE`/`PARTIAL`;
- stable attempt IDs;
- usage fact IDs;
- internal cost fact IDs;
- provenance/evidence IDs.

This keeps the state machine storage-agnostic and avoids making a privacy/capacity decision by
accident.

## Reason and error envelope

Current task state and transition history may contain only bounded safe metadata:

- `reason_code`;
- `error_class`;
- `error_reference_id`.

Raw provider payloads, stack traces, secrets, provider credentials and financial amounts do not
belong in these fields.

## Cycle-decision ledger

`execution.task_cycle_decisions` is append-only by `(task_id, cycle_index)`. It records facts
provided later by the orchestration engine:

- sequential cycle index;
- candidate order;
- escalation reason code when applicable;
- accepted/missing snapshots;
- task version;
- timestamp.

Recording a cycle requires `RUNNING`, the next exact cycle index and a successful task-version
CAS. It does **not** rank candidates, calculate provider cost, retry providers or dispatch calls.

For `EXPLICIT_TARGET`, every candidate supplied to a cycle record must equal the frozen
effective target. This gives future characterization tests a durable proof that no cross-target
fallback occurred.

## Timestamps and terminal immutability

The task tracks creation/update plus queue/start/terminal timestamps. Terminal states require
`terminal_at`; non-terminal states prohibit it.

Successful terminal states require a result reference. Terminal states have no outgoing
transition, so the repository cannot replace a successful result after terminalization.

## HTTP boundary

The v1 routes remain unchanged. This issue does not wire `POST /v1/sessions/{session_id}/tasks`
to an incomplete creation path.

HTTP task creation requires composition of:

- #14 idempotency transaction;
- authenticated tenant/client ownership;
- Control Plane policy/target authorization;
- payload reference/storage boundary;
- durable task create + enqueue/outbox effect.

Until that application transaction exists, the Phase-1 HTTP shell remains fail-closed instead
of returning a fake success.

## Non-goals

- provider attempts/network calls;
- candidate ranking/cost escalation;
- retry/fallback/cycle policy;
- worker leases/recovery implementation;
- provider simulator;
- financial accounting truth;
- raw payload storage decision;
- RASAi mutation.
