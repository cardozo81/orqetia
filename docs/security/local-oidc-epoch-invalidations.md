# ADR — LOCAL/TEST OIDC restart invalidation (Option B)

**State:** IMPLEMENTED / operational evidence tracked in #174. **Issue:** #174. **Lifecycle:** DEVELOPMENT. **Decision recorded:** 2026-10-10, explicitly authorized by the repository user. **Historic baseline:** `e954ae6bad0391a494a581f59fb2311be75b4929`.

This document authorizes the **security semantics** of restart invalidation, not a destructive cleanup of the existing Docker `oidc_state` volume, a release promotion or a change to production OIDC. The earlier [retention counterexamples](local-oidc-marker-retention.md) remain valid for any scheme that re-accepts restorable state.

## Decision and rationale

Adopt a **fresh, process-owned, cryptographically random authentication generation** (epoch) for each running LOCAL/TEST Backoffice or Portal verifier. Each login transaction carries this generation within the **signed** public and private transaction material. The authorization code remains bound to the public transaction hash, state and nonce. A verifier rejects a callback unless the signed generation matches its own **currently live** generation. Do not store the generation in PostgreSQL, Docker volumes, backup archives, long-lived cookies, configuration or logs.

A restarted/replaced verifier always creates a new generation and refuses every old-generation transaction, even when valid signing keys, wall clock and disk snapshots have been restored. A pending browser login must restart from the login entry point after verifier restart. The fate of an **already established** browser session remains governed by the existing session store; it is not invalidated merely to implement this ADR.

The current deployment has one Backoffice and one Portal verifier process, using distinct OIDC audiences. **One active process per audience** is the accepted reference topology. Multiple replicas of one audience require explicitly characterized sticky callback routing or a different shared admission authority; silently sharing an epoch across replicas is prohibited. A broker object inherited across a fork must not silently reuse its parent's generation.

## Replacement of indefinitely growing new-marker storage

Prefer per-generation, **bounded transient admission state** rather than a persistent marker collector:

1. On transaction issuance, record the signed transaction digest with a **process-monotonic** issuance deadline and a PENDING flag in a capacity-bounded per-verifier map. Do not write new persistent OIDC marker files.
2. On callback, first validate signature, issuer, audience, callback, state/nonce/PKCE, transaction binding, expiry and generation; then check that the digest is an outstanding **issued-by-this-verifier** record and that its monotonic deadline remains valid. Atomically transition PENDING to CONSUMED before returning an authenticated context. A duplicate, missing, expired or consumed transaction always fails closed.
3. Use a monotonic deadline no earlier than the current inclusive wall-clock deadline: the original 300-second TTL allows the exact integer expiry second, while monotonic admission can deny earlier only if a documented conservative timing bound has elapsed. **After monotonic expiry the transaction must never become acceptable again**, regardless of wall-clock rollback.
4. Bound both issued and consumed records, e.g. a reviewed fixed maximum active capacity with bounded per-operation expiry cleanup. When capacity is full and safe progress is not possible, deny new login admission; do not evict possibly valid consumed records or perform unbounded work during callback. Avoid unlimited memory scans or iteration that can starve old keys.
5. Protect issuance and consumption with a shared thread-safe critical section for that verifier instance. The cryptographic generation boundary prevents another process from accepting that instance's transaction; this does **not** prove arbitrary multi-replica callback availability.
6. Keep **all existing legacy on-disk markers** untouched. Old-generation login transactions are invalidated by the new code's mandatory generation check; no file must be removed during migration. This ADR does not approve mtime-based legacy deletion or alteration of historical backups.

This design avoids both an external time authority and deleting replay markers from a restorable backup. **It changes the old durable-replay boundary deliberately:** correctness now depends on the ephemeral verifier generation plus a monotonic, bounded in-process consumed-token map. A marker may outlive the transaction in the legacy volume; that residual cost is not automatically cleaned.

## Recovery/rollback and failure boundaries

- An **actual restore involving authentication state** must first stop/drain all original verifiers and IdP issuance/callback ingress, then start clean verifiers whose generations are newly created. A verifier remaining alive while its state is rolled back has not undergone generation invalidation; such a restore is explicitly unsafe and prohibited.
- Never persist or restore a prior generation. Process restart, deploy recreation and process fork must not silently keep a generation that no longer belongs to a live verifier.
- Fail closed if secure entropy, process-generation setup or monotonic-time admission state is unavailable. A capacity/lock error must not result in an authenticated context. No fallback to unbounded persistent files or process-global reusable generation.
- Wall-clock rollback can make a signed tuple appear unexpired again, but it **must not** recover a CONSUMED or monotonic-expired issuance record. Once any record is removed, every attempt to use that transaction digest must be rejected as **missing**, not accepted as new.
- Existing browser security, token signatures, CSRF, ownership checks, OIDC binding and the separation of Backoffice vs Portal do not change.
- LOCAL/TEST only. An actual production IdP requires its independently approved OIDC/session/revocation contract; this synthetic broker remains unavailable outside LOCAL/TEST.

## Minimum directed verification (synthetic)

Before changing any running Docker installation, test the actual brokers:

1. A pending transaction completes normally in the issuing verifier, then any replay is rejected, including under two synchronized concurrent callbacks.
2. A new verifier with the same signing key and a **restored old snapshot** rejects all old-generation tokens at the original inclusive expiry second and after a wall-clock rollback. A restored signed cookie alone cannot authorize a login.
3. Another running process/audience with a different generation rejects the original transaction; attempts after fork are fail-closed or demonstrably reinitialize without retaining the parent's outstanding tokens.
4. The issued digest is denied after monotonic expiry even if the wall clock goes back into the signed-valid window; deleting an expired in-memory entry can never cause reuse.
5. Time boundary, late-issued code, signature, audience, nonce, PKCE, state and client-side cookie/header behavior remain negative-tested as appropriate.
6. Generate more login starts than the bounded capacity; prove no unbounded growth and fail-closed overload/restart behavior. Verify expiry work budget and no directory enumeration/new marker writes.
7. Marker files from the real installation and the previous #171 archives are not accessed or altered during these tests. Local Windows symlink restrictions are not disguised as successful coverage.
8. When code is integrated, confirm targeted Ruff/mypy/tests, all required GitHub CI checks on final `main` HEAD, and record in #174/#47/#48. Runtime update is a **separate** operational decision with backup point and narrow HTTPS login/RBAC checks.

## Broker integration — 2026-10-13

Both `LocalBackofficeOidcBroker` and `LocalCustomerPortalOidcBroker` now construct
one `LocalOidcEpochLedger` at verifier initialization. The existing application
entry points construct one broker per process. The signed public/private
transaction contains `generation`; the IdP's existing transaction-hash binding
covers it without a second authentication mechanism. Only a digest issued by
that same live verifier may atomically transition to consumed. Missing/legacy
generation, another verifier, fork, unknown digest and replay all deny.

The primitive is reused unchanged: at most **4,096 pending plus consumed entries**
and 4,096 heap items per verifier; at most **64 expired entries removed per
successful issue/consume operation**, each with O(log 4096) heap work. No disk
scan or persistent write occurs during issuance/consumption. At capacity the
public broker maps denial to its existing login exception (HTTP 503 on start),
without evicting valid reservations. Failed initialization/clock operations never
fall back to persistent admission. Existing legacy files are neither read nor
removed. An idle process may keep expired entries until another successful
operation or exit, but cannot exceed the fixed capacity.

Signed wall-clock expiry remains inclusive at `int(time.time()) == exp`.
Monotonic deadline is sampled on issuance as `monotonic() + 301`; equality at
that fence is accepted if all other checks pass, strictly later is denied.
The extra second conservatively covers the signed integer-second boundary.
Civil-clock rollback cannot reverse monotonic expiry or consumption. Once swept,
a digest is rejected as unknown even if the signed tuple appears valid again.
This assumes the OS process-monotonic clock contract; restoring live process
memory/VM snapshots is outside the approved recovery protocol.

Directed evidence:
- `test_local_oidc_broker_epoch.py`: both real public brokers, exact/late expiry,
  replay, concurrent callbacks, restart/restored files, civil rollback before
  and after bounded expiry, 4,096-entry capacity, fork, entropy/clock faults,
  signature-bound field validation and zero legacy filesystem I/O.
- `test_local_oidc_http_epoch.py`: real IdP and BFF routes, valid callback,
  replay with original transaction cookie, pending callback after replacement,
  and established session accepted by a fresh verifier with the same independent
  session store. `test_local_oidc.py` also exercises both audiences and origin/PKCE.
- `test_oidc_state_recovery.py` records the approved delta to #173; the earlier
  filesystem/restore counterexamples remain explicitly injected **historical
  admission models** in `test_oidc_marker_retention_safety.py`, not runtime code.
- Primitive tests retain concurrency, bounded sweep and process ownership checks.

## Authorized Windows runtime validation

The 2026-10-13 user instruction explicitly authorizes operational rollout **after
code review, directed tests and green final-HEAD CI**. Review the delta against
installed code; compare aggregate credentials/CA/legacy hashes, volume identity,
mounts and original local backups before/after. Rebuild the shared image and
recreate only affected long-running application services without removing volumes.
Preserve successful existing migrate/bootstrap jobs when their code/schema did
not change; verify exit 0. Never rerun bootstrap as a credential recovery shortcut.
Verify all eight services, HTTPS login/callback/replay for both audiences and
preservation of established sessions across an actual verifier restart. Record
image/content provenance and sanitized results on #174 before closure.

No deletion or migration of historical markers is part of rollout. An old-image
rollback reintroduces persistent writes and the old recovery contract; drain all
verifiers/IdP ingress first and do not mix generations/policies as an availability
workaround. A failed security smoke blocks runtime acceptance and issue closure.

## Release and ownership gates

The approved B contract supersedes the earlier restore-freshness implementation gate. **#174 is CLOSED, with the broker integration, negative tests, green CI and authorized Windows Docker acceptance recorded in the [final acceptance checkpoint](https://github.com/cardozo81/orqetia/issues/174#issuecomment-6143500792).** The rollout instructions above are historical evidence boundaries, not authorization for a new deployment. No operational deletion, credential/key rotation, CA/trust-store change, database migration, Docker reset, new service/cost or RASAi mutation is authorized.

**#162 is CLOSED:** PR enforcement and nine required GitHub Actions checks on `main` are verified ([checkpoint](https://github.com/cardozo81/orqetia/issues/162#issuecomment-6150369672)). **#164 remains OPEN** for genuine external security intake and responsible owner/fallback acceptance. The off-host recovery decision is also outstanding. **DEVELOPMENT**.
