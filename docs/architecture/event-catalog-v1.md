# ORQETIA event catalog v1 — M0 candidate contract

This catalog defines ownership and minimum payload intent. Machine-readable schemas are created during Phase 1.

## Execution

### task.created v1

Owner: Execution
Classification: CLIENT_PRIVATE
Minimum payload:
- task_id;
- session_id;
- effective_policy_version_id;
- created_at.

Consumers may:
- build read model;
- create accounting/statistical correlation state.

Must not contain:
- raw prompt/context;
- provider secret;
- provider cost.

### task.queued v1
### task.started v1
### task.completed v1
### task.partial v1
### task.unavailable v1
### task.failed v1
### task.cancelled v1

Owner: Execution
Classification: CLIENT_PRIVATE
Common minimum:
- task_id;
- session_id;
- status;
- terminal/result reference when applicable;
- occurred_at.

### provider.attempt.completed v1
### provider.attempt.failed v1

Owner: Execution
Classification: CLIENT_PRIVATE
Minimum:
- attempt_id;
- task_id/session_id;
- provider/model safe IDs;
- cycle/attempt index;
- status/normalized error;
- usage-source availability/reference;
- occurred_at.

No provider secret or provider monetary amount required in the execution event.

### provider.quarantined v1

Owner: Execution
Classification: INTERNAL or CONFIDENTIAL depending metadata
Minimum:
- session_id;
- provider safe ID;
- normalized terminal reason;
- occurred_at.

## Usage & Accounting

### usage.recorded v1

Owner: Usage & Accounting
Classification: CLIENT_PRIVATE
Minimum:
- usage_fact_id;
- attempt_id;
- provider/model safe IDs;
- technical usage dimensions;
- occurred_at.

No provider credential fingerprint required for client-facing consumers.

### provider_cost.estimated v1

Owner: Usage & Accounting
Classification: CONFIDENTIAL
Minimum:
- cost_fact_id;
- attempt_id;
- provider/model;
- amount;
- currency;
- pricing rule/version;
- occurred_at.

Restricted to internal/Backoffice consumers.

### provider_cost.observed v1

Owner: Usage & Accounting
Classification: RESTRICTED
Minimum:
- cost_fact_id;
- attempt/provider account safe refs;
- amount;
- currency;
- source/basis;
- occurred_at.

### quota.reserved v1
### quota.reconciled v1
### quota.rejected v1

Owner: Usage & Accounting
Classification: CLIENT_PRIVATE / INTERNAL according to payload
Minimum:
- reservation_id;
- tenant/client;
- quota policy/version reference;
- quantity/category/status;
- occurred_at.

## Control Plane

### execution_policy.published v1

Owner: Control Plane
Classification: INTERNAL
Minimum:
- policy_version_id;
- effective metadata;
- occurred_at.

Payload need not expose all policy internals if consumers can resolve immutable snapshot.

### pricing.catalog.published v1

Owner: Control Plane
Classification: CONFIDENTIAL
Minimum:
- catalog_version;
- checksum;
- effective metadata;
- occurred_at.

### credential.revoked v1

Owner: Identity or Control Plane according to credential type
Classification:
- client credential metadata: CLIENT_PRIVATE;
- provider credential metadata: CONFIDENTIAL.

Minimum:
- safe credential ID;
- credential type;
- revoked_at;
- security/version marker.

Never includes the credential secret.

## Estimation

### benchmark.snapshot.published v1

Owner: Statistical Estimation
Classification:
- CLIENT_ONLY snapshot: CLIENT_PRIVATE;
- approved global snapshot: PUBLIC or INTERNAL until publication.

Minimum:
- snapshot_id/version;
- cohort/scope safe metadata;
- as_of/watermark;
- quality/calibration reference.

## Audit/Privacy candidates

Security/privacy events are added only when a concrete consumer requires asynchronous propagation. Authoritative audit itself does not require publishing every sensitive event to a general broker.

## Contract rule

Before implementation of each event:
1. create JSON Schema;
2. identify owner;
3. identify consumers;
4. confirm classification;
5. confirm need for async propagation;
6. add producer/consumer contract tests;
7. define replay/ordering behavior.
