# ADR — LOCAL/TEST OIDC restart invalidation (Option B)

**State:** APPROVED ARCHITECTURE / IMPLEMENTATION PENDING. **Issue:** #174. **Lifecycle:** DEVELOPMENT. **Decision recorded:** 2026-10-10, explicitly authorized by the repository user. **Historic baseline:** `e954ae6bad0391a494a581f59fb2311be75b4929`.

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

## Implementation checkpoint — security primitive only

- `src/orqetia/infrastructure/local_oidc_epoch.py` supplies an ephemeral process-generation registry with bounded capacity (4,096), bounded heap sweep (64), monotonic 301-second expiry, atomic consume-once and PID/fork fail-closed.
- `tests/devenv/test_local_oidc_epoch.py` exercises the primitive's restart generation, rollback, concurrent consume, bounded capacity and fork denial with generated synthetic inputs.
- **The existing `LocalBackofficeOidcBroker` and `LocalCustomerPortalOidcBroker` are NOT wired to the new primitive.** Their existing file-backed replay policy is still operational, and the application continues to create file markers until that targeted integration is completed.
- **No legacy marker has been deleted; no collector was added.** Do not interpret the existence of the primitive or its tests as a complete #174 fix or approval of a Docker rollout. Before wiring, adapt the previously accepted #173/#174 tests to the explicitly changed restart contract, retaining the old rollback counterexample as historical synthetic evidence. Run a real broker/IdP/callback regression and review CI before declaring completed.
- The primitive's `ttl_seconds + 1` monotonic fence is deliberately conservative with respect to the existing wall-clock integer expiry, inclusive at the exact last second. Once its record is removed, an old token remains rejected as missing.
- Security boundary remains: restore must stop ALL old verifiers first, and multi-replica/same-audience callback affinity is not implemented. No production OIDC guarantees are implied.

## Release and ownership gates

This architectural approval changes #174 from **BLOCKED_HUMAN** to **READY_FOR_SAFE_IMPLEMENTATION**; it does **not** close it. No code or garbage collector is considered delivered by the ADR alone. No operational deletion, credential/key rotation, CA/trust-store change, database migration, Docker reset, new service/cost or RASAi mutation is authorized.

Independent pre-RC gates #162 and #164 and the off-host recovery decision remain open. **DEVELOPMENT**.
