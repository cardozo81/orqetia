# ORQETIA operational recovery runbook

**Version:** `operational-runbook-v1`  
**Issue:** #153  
**State:** DEVELOPMENT / pre-RC technical procedure

This runbook consolidates existing recovery mechanisms. It does not replace the
canonical security incident, DR, availability, retention or worker recovery
contracts.

## Safety rules

Before any recovery action:

1. preserve correlation IDs, timestamps, deployment SHA and sanitized evidence;
2. never paste provider/client secrets, bearer tokens or raw private payloads into
   tickets, chat or CI logs;
3. stop or drain writers before destructive database/rollback procedures;
4. use only synthetic credentials/data for drills;
5. prefer idempotent/replay-safe recovery paths already implemented by ORQETIA;
6. record UTC start/end, command, commit SHA, result and operator/automation identity.

A failed drill is evidence of an unresolved operational gap and must not be hidden
by broad retries.

## Drill matrix

| ID | Scenario | Canonical mechanism | Success criterion |
| --- | --- | --- | --- |
| OPS-01 | startup/readiness degradation | `availability-v1`, health probe | provider outage => DEGRADED; DB outage => UNAVAILABLE with sanitized response |
| OPS-02 | queue crash/reclaim ambiguity | durable lease + attempt journal | reclaimed orphan dispatch becomes AMBIGUOUS without a second provider invocation |
| OPS-03 | provider credential rotation failure | provider credential lifecycle | failed persistence leaves old credential usable and discards replacement secret |
| OPS-04 | backup/restore | #148 PostgreSQL drill | restored DB has matching Alembic revision, schemas and synthetic client-private marker |
| OPS-05 | tenant isolation | customer membership authorization | browser-selected tenant/client cannot escape exact server-side membership |
| OPS-06 | schema/rollback safety | compatibility gate + Alembic | incompatible schema change is detected before rollout; current/head revisions are inspectable |
| OPS-07 | security incident handoff | #57 incident runbook | policy contains private intake, containment, evidence and LGPD escalation |
| OPS-08 | planned maintenance/drain | `availability-v1` | mutations receive 503/Retry-After and workers stop new claims |

## OPS-01 — startup/readiness degradation

Diagnostic order:

1. `GET /health/live` — process only;
2. `GET /health/ready` — readiness/dependency state;
3. inspect PostgreSQL connectivity and deployment configuration;
4. do not call an AI provider from a readiness probe.

Synthetic drill:

~~~bash
uv run --no-sync --python 3.14 python -m unittest   tests.ops.test_availability_policy.AvailabilityPolicyTests.test_provider_outage_is_degraded_and_database_outage_not_ready   -v
~~~

Recovery: restore the critical dependency, verify readiness, then reopen admission.
Do not override authentication/secret failures with guessed values.

## OPS-02 — queue backlog, poison and reclaim

For backlog:

1. inspect durable queue depth and DEAD/LEASED/READY distribution;
2. verify worker readiness before increasing concurrency;
3. do not manually ACK work to make a graph look healthy;
4. unsupported operations use the canonical dead-letter path;
5. expired leases recover through existing bounded reclaim rules.

Ambiguous provider dispatches must not be automatically redispatched unless verified
upstream idempotency explicitly permits it.

Synthetic drill:

~~~bash
uv run --no-sync --python 3.14 python -m unittest   tests.processes.test_provider_attempt_recovery.ProviderAttemptRecoveryIntegrationTests.test_orphan_dispatch_becomes_ambiguous_without_redispatch   -v
~~~

Success means one logical attempt is not turned into two provider calls.

## OPS-03 — provider outage and credential rotation

Provider outage alone degrades execution; the Client API may remain available and
durable tasks wait/retry according to canonical orchestration policy.

For credential rotation:

1. prepare replacement secret outside logs;
2. persist/activate new reference before retiring the old secret;
3. verify preflight using only in-memory secret material;
4. revoke old reference after successful commit;
5. on persistence failure, preserve old known-good credential and discard the new
   uncommitted secret.

Synthetic drill:

~~~bash
uv run --no-sync --python 3.14 pytest -q   tests/control_plane/test_provider_credentials.py::test_rotation_persistence_failure_preserves_old_secret_and_metadata
~~~

Never use a real provider key in CI.

## OPS-04 — database failure, backup/restore and DR

Reference objectives and full procedure are in
`docs/operations/backup-restore-dr.md`.

Safe drill:

~~~bash
uv run --no-sync --python 3.14 alembic upgrade head
bash scripts/postgres_restore_drill.sh
~~~

During an actual database outage, keep liveness distinct from readiness, stop
conflicting writes, select a verified restore point, restore into a clean target and
reconcile queues/read models before reopening traffic.

## OPS-05 — tenant isolation and security handoff

Cross-tenant suspicion is a security incident. Stop the affected path if necessary,
preserve sanitized evidence and hand off to
`docs/runbooks/security-incident.md`.

Synthetic authorization drill:

~~~bash
uv run --no-sync --python 3.14 pytest -q   tests/identity/test_customer_authz.py::test_browser_selected_owner_cannot_escape_exact_membership
~~~

A negative authorization result is part of the recovery acceptance criterion.

## OPS-06 — rollback and migration recovery

Before rollout:

~~~bash
uv run --no-sync --python 3.14 alembic current
uv run --no-sync --python 3.14 alembic heads
uv run --no-sync --python 3.14 pytest -q   tests/contracts/test_schema_compatibility.py::test_required_property_change_is_breaking
~~~

Do not downgrade a production-like database merely to satisfy a failing application
without verifying the migration's downgrade contract and data-loss implications.
For disaster recovery prefer the clean-target restore procedure from #148.

Rollback success requires application/schema compatibility, readiness green or
explicitly degraded, and no abandoned active leases/writers.

## OPS-07 — security incident

Canonical procedure: `docs/runbooks/security-incident.md`.

Synthetic documentation gate:

~~~bash
uv run --no-sync --python 3.14 pytest -q   tests/security/test_incident_response_docs.py::test_incident_response_policy_covers_issue_57_contract
~~~

Security incident evidence is private by default. Provider/client secrets and raw
private payloads do not belong in the incident log.

## OPS-08 — planned maintenance and queue drain

Set `ORQETIA_MAINTENANCE_MODE=draining` before planned maintenance. API mutations
return 503 with `Retry-After`; workers/schedulers stop new claims. Allow current
leases to finish within shutdown grace, then switch to `maintenance`.

Return to `normal` only after the recovery criteria in
`docs/runtime/availability-maintenance-policy.md` pass.

Synthetic drill:

~~~bash
uv run --no-sync --python 3.14 python -m unittest   tests.ops.test_availability_policy.AvailabilityPolicyTests.test_worker_drain_stops_new_claims   -v
~~~

## Evidence template

Record:

~~~text
drill_id:
started_at_utc:
finished_at_utc:
git_sha:
environment:
synthetic_data_only: true
command:
result: PASS|FAIL
correlation_or_resource_ids:
notes:
~~~

Never include raw secrets or unnecessary personal data.

## Escalation

- Security/privacy: follow #57 immediately.
- Restore/RPO/RTO: follow #148.
- Payload/result retention: follow #149/#55.
- Audit integrity/immutability: follow #150.
- Availability/maintenance: follow #159.
- Unknown/new failure mode: create/update #48 before inventing a new recovery
  mechanism.
