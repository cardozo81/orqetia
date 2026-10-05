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
- attempt;
- operation;
- session/task when the operation is task/session-bound;
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
- attempt_id UUIDv7 PK — stable identity created before provider dispatch
- operation typed code NOT NULL
- task_id UUID FK within execution, nullable only when the operation contract permits taskless execution
- session_id UUID FK within execution, nullable only when the operation contract permits sessionless execution
- tenant_id / client_id UUID where client-scoped
- provider_id / model_id typed logical refs
- provider_account_id / provider_credential_id safe logical refs
- cycle / attempt_index
- retry_of_attempt_id / fallback_from_attempt_id optional self refs
- request_fingerprint optional diagnostic/idempotency metadata; never attempt identity
- status / normalized_error_class
- started_at / finished_at / duration
- policy_version_id logical ref
- provenance metadata

provider_exchanges:
- exchange_id UUIDv7 PK
- attempt_id UUID NOT NULL logical owner/reference
- exchange_index integer when one logical attempt has multiple wire exchanges
- endpoint safe metadata
- started_at / finished_at / duration / transport status

provider_exchange_evidence:
- evidence_id UUIDv7 PK
- exchange_id UUID NOT NULL within Execution
- attempt_id UUID NOT NULL
- kind = REQUEST | RESPONSE | PROMPT | SCHEMA | OTHER
- media_type / encoding
- sanitized_body or protected payload reference
- sanitized_sha256 over the persisted sanitized representation
- truncated / persisted_size / original_size when safely known
- created_at
- immutable/revisioned lifecycle under ADR-0019

New ORQETIA exchange rows never infer the attempt by provider/model/timestamp/purpose/fingerprint.

No generic round entity exists in the ORQETIA core. No provider secret belongs in provider_attempts, provider_exchanges or provider_exchange_evidence. Sanitization happens before evidence persistence; presentation does not semantically rewrite persisted evidence.

## Accounting skeleton

usage_facts:
- id UUIDv7 PK
- attempt_id logical ref NOT NULL for provider-attributed usage
- tenant_id / client_id
- provider/model
- input/output/cached/reasoning/total tokens
- native usage reference when separate
- occurred_at

provider_cost_facts:
- id UUIDv7 PK
- attempt_id logical ref NOT NULL
- basis = ESTIMATED | PROVIDER_OBSERVED
- amount numeric
- currency
- pricing catalog/rule/version
- occurred_at

UNPRICED is represented explicitly by pricing/application status/fact semantics, never amount=0.

## Projection rule

readmodel tables may duplicate selected dimensions for query performance, but they are disposable/rebuildable and cannot be used to authorize a mutation without authoritative ownership validation.

## Attempt/operation contract

ADR-0018 (#86/#87) amends this logical ERD:
- `attempt_id` is the primary provider-dispatch identity;
- `operation` is explicit even when no Task exists;
- task/session absence is governed by the operation contract rather than represented by synthetic rows;
- Exchange, Usage, Pricing and Diagnostic facts correlate through the same `attempt_id`.
