# Initial logical ERD — ORQETIA

This document is the logical inventory from ADR-0005. It does not freeze every physical column; #21, #44, #50, #55, #59 and #61 refine specific tables.

## Context relationships

    identity.tenant
      |
      +-- identity.service_client
      |      |
      |      +-- execution.execution_session
      |              |
      |              +-- execution.task
      |                     |
      |                     +-- execution.provider_attempt
      |
      +-- control.client_policy_assignment
      +-- control.client_provider_permission
      +-- control.quota_assignment

    control.provider
      +-- control.provider_model
      +-- control.provider_account
             +-- control.provider_credential_metadata

    execution.provider_attempt
      -> accounting.usage_fact
      -> accounting.provider_cost_fact
      -> accounting.native_usage_fact

    authoritative facts/events
      -> estimation.rollups/snapshots
      -> readmodel projections
      -> audit events

Arrows across contexts describe logical references/contracts, **not cross-schema foreign keys**.

## Minimum ownership columns

Any client-owned session/task/credential/usage projection must preserve tenant_id and client_id as required by its authorization boundary.

Any provider attempt/accounting fact must preserve stable references needed to reconstruct:
- session;
- task;
- attempt;
- provider/model;
- provider account/credential safe ID where applicable;
- policy/pricing version;
- tenant/client;
- timestamps.

## Execution durability skeleton

execution_sessions:
- id UUIDv7 PK
- tenant_id UUID
- client_id UUID
- effective_policy_version_id UUID
- status
- version
- created_at / updated_at / expires_at

tasks:
- id UUIDv7 PK
- session_id UUID FK within execution
- tenant_id UUID
- client_id UUID
- status
- requirements metadata/reference
- accepted/missing metadata/reference
- cancellation metadata
- version
- created_at / started_at / terminal_at / updated_at

provider_attempts:
- id UUIDv7 PK
- task_id UUID FK within execution
- session_id UUID FK within execution
- tenant_id / client_id UUID
- provider_id / model_id logical refs
- provider_account_id / provider_credential_id safe logical refs
- cycle / attempt_index
- status / normalized_error_class
- started_at / finished_at / duration
- policy_version_id logical ref
- provenance metadata

No provider secret belongs in provider_attempts.

## Accounting skeleton

usage_facts:
- id UUIDv7 PK
- attempt_id logical ref
- tenant_id / client_id
- provider/model
- input/output/cached/reasoning/total tokens
- native usage reference when separate
- occurred_at

provider_cost_facts:
- id UUIDv7 PK
- attempt_id logical ref
- basis = ESTIMATED | PROVIDER_OBSERVED
- amount numeric
- currency
- pricing catalog/rule/version
- occurred_at

UNPRICED is represented explicitly by pricing/application status/fact semantics, never amount=0.

## Projection rule

readmodel tables may duplicate selected dimensions for query performance, but they are disposable/rebuildable and cannot be used to authorize a mutation without authoritative ownership validation.
