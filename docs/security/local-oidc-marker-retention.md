# LOCAL/TEST OIDC marker retention: safety specification

Issue: #174 (explicit delta to #165). Parent: #40. Roadmap: #47; gaps: #48.
Lifecycle: **DEVELOPMENT**. Baseline: `ad00eec29874bee66038ae88ec9ce78925c229e5`.
Status: **BLOCKED — restore freshness is not provable with the available state**.

This document evaluates deletion, not an operational cleanup command. It reuses
[local_oidc.py](../../src/orqetia/infrastructure/local_oidc.py), the existing
Backoffice/Portal brokers and [#173's inventory](../operations/volume-recovery-inventory.md).
No change to the canonical orchestration contracts #31/#34 or rewrite under #43
is required. The historical #173/#171 acceptance remains intact.

## 1. Effective validity

Let `n = int(time.time())`, `t` be a signed transaction, `c` its bound signed code,
and `a = c.authenticated_at`. Temporal admission currently requires all of:

```text
n <= t.exp
n <= c.exp
0 <= n - a <= 300
```

Signature, issuer/audience/callback, state, nonce, PKCE and transaction hash must
also validate. For tokens issued by the existing LOCAL/TEST adapter,
`t.exp = t.iat + 300`, `c.exp = c.iat + 300`, and `a = c.iat`.
Thus the last potentially acceptable integer second is
`E = min(t.exp, c.exp, a + 300)`. The exact second E is inclusive; E+1 is expired.
A code issued late does not extend the transaction's original deadline.
This specification does not silently change the current inclusive boundary.

## 2. Minimum retention and invariant

For every consumed transaction x and every later permitted clock/restart/restore
history h, `admit(x, h)` must remain false. Deletion must not turn a previously
consumed transaction into an admissible transaction.

- With a proven nondecreasing verifier clock, the earliest temporal candidate
  for deletion is strictly after E, not at E and not 300 seconds after file mtime.
- A proposed additional retention margin is **60 seconds**: deletion candidates
  satisfy `n > E + 60`. This is an operational margin, not evidence of trusted
  time and not protection against arbitrary rollback. It is not activated.
- With arbitrary wall-clock rollback, **no finite waiting interval is sufficient**
  while the old signed tuple can again pass the verifier. Existing/unknown markers
  therefore have no proven finite safe retention deadline under that model.
- A marker's mtime/ctime, backup capture time, filename hash or empty contents do
  not authenticate E. Age and HMAC authenticity do not establish freshness.

## 3. Candidate durable horizon and its proof boundary

A potential new-format marker would authenticate a version, transaction digest,
audience, transaction deadline and E using the existing signing key with a dedicated domain separator.
It would contain no raw token, cookie, verifier, user identity or new key. Legacy
empty markers must never be reinterpreted as expired records.

Before deleting any such marker, every consumer would need to enforce a shared
rejection horizon H: reject all transactions whose transaction deadline is <= H.
Advancing H must precede deletion, be durable, and be serialized with admission
across both BFF processes. The marker's transaction deadline, not just a shorter
code deadline, must be covered. H may advance only past already expired
transactions; a valid transaction must not be invalidated by a cleanup shortcut.

Under an explicit assumption that H cannot be rolled back or lost, the proof is:
either the marker still exists and O_CREAT | O_EXCL rejects reuse, or its deadline
is <= H and every consumer rejects it before marker creation. Persisting H before
unlink makes crashes conservative: a crash can leave extra markers, never a gap.
Missing, malformed, unverifiable or unwritable H must stop admission and deletion;
it must never be silently initialized from current wall time on an existing store.

This protects **ordinary process restart and clock rollback with preserved H**.
It does not by itself protect restoration of an older, correctly signed H.
Fsync, atomic rename, locks, HMAC and a sequence counter on the same restorable
storage prove neither non-rollback nor freshness. An in-memory maximum or monotonic
clock alone is lost on restart; a monotonic process clock is not a cross-boot epoch.

## 4. Restore ambiguity and implementation gate

Consider a still-authentic tuple with deadline E and an authentic old horizon
H0 < E. After consumption, advance to H1 >= E and remove its marker. A later
restart with restored H0 and `a <= n <= E` observes a valid tuple, a valid H0 and
no marker. Those inputs are indistinguishable from a never-consumed login.
Keeping the old marker would reject it; deleting it introduces the ambiguity.

Therefore a same-store horizon alone cannot prove the required invariant when
security metadata restoration is admitted. This is a scoped limitation of the
available state, not a claim that safe garbage collection is universally impossible.
Unconditional denial after every restart would protect safety but would replace
the existing login contract with a permanently unavailable service.

Before destructive implementation choose and record an explicit security contract:

1. **Durable non-rollback authority:** a rejection horizon outside the restorable
   marker state, with verified monotonicity, ownership, fail-closed startup and
   restore protocol. Merely moving it to another file is insufficient. A database
   or service is not automatically non-rollback and must not be introduced silently.
2. **Explicit restart/restore invalidation protocol:** change issuance/admission
   so restored old transactions are unconditionally invalidated, with a shared
   generation/liveness design for both consumers. Define availability and concurrent
   deployment semantics in an accepted delta/ADR; process-local state alone would
   change callback routing/restart behavior. No key rotation is authorized here.

Until this gate is resolved, preserve runtime and every marker, publish only
specification and isolated evidence, and keep #174 open. Do not weaken the restore
model merely to obtain a passing deletion test. #173's temporal recovery procedure
already requires a trustworthy clock and known issuance cutoff; it is not proof
that unattended GC remains safe under a later rollback.

## 5. Legacy, malformed and restored metadata

Legacy empty markers are retained indefinitely. Their count remains a residual
storage cost, not evidence of an urgent incident. No migration/purge of the 14
operational markers is authorized. Unknown versions, bad MACs, invalid ranges,
oversized records, directories, symlinks, non-regular files and uncertain provenance
must be preserved or quarantined by a separately approved procedure, never removed
by guessing age. File metadata may be arbitrarily old, future or restored.

An existing path must still make atomic creation fail. New-format readers must
not follow symlinks; a trusted fixed ledger root and no-follow descriptor-relative
operations must be specified before a collector is implemented. Do not confuse
file-content authenticity with authorized filesystem access or rollback resistance.

## 6. Concurrency, filesystem failures and restart

Retain O_CREAT | O_EXCL for consumption: exactly one concurrent consumer wins.
A collector must serialize horizon advancement and deletion with all consumers,
including another process; a Python thread lock alone is insufficient. A crash
after marker creation must conservatively leave the marker. Never unlink a
reservation because a later close/write/response failed: authentication may have
progressed or another process may observe it.

I/O errors must not result in authenticated context or retry by discarding state.
The baseline rejects permission errors through the broker and may propagate other
OS errors as server failures; it has no graceful availability guarantee for a full
disk. This analysis does not silently alter that error contract. Ordinary broker
restart currently preserves replay rejection when the marker store is intact.

## 7. Work budget and bounded-retention acceptance

Proposed collector limits (not implemented): at most **64 directory entries**,
**4,096 bytes per regular record** and **16 deletion attempts per invocation**.
Count skipped/invalid/legacy entries against the entry budget. No recursive walk,
sorting/materializing the directory, symlink traversal or unbounded lock wait in
the callback path. A resumable cursor must demonstrate progress despite legacy
entries and concurrent append; repeated process restarts must not starve old
candidates indefinitely. A nominal wall-time timeout cannot bound a blocked syscall.

Prove a workload bound for new-format records with arrival rate r, effective
validity <= 300 seconds, retention margin 60 seconds and guaranteed maximum sweep
lag L: retained new records <= r * (361 + L), plus a documented burst allowance.
Legacy/invalid records are excluded from that claim and remain separately visible.
Without a finite L and validated horizon, **bounded growth is not demonstrated**.
No collector exists in the delivered baseline: login does zero directory enumeration
and no deletion; that is a preservation check, not completion of bounded retention.

## Validation plan

Before changing runtime, exercise both actual public broker classes with synthetic
keys/codes, controlled time and temporary directories. Cover exact/after deadline,
late code issuance, reuse, two synchronized consumers, fresh broker instances,
rollback with retained markers and after simulated deletion, restored/unknown
metadata, malformed paths, filesystem failures and enumeration count.

Counterexamples must be named as counterexamples: a passing test demonstrating
unsafe reuse is evidence to block the candidate, not an anti-replay acceptance.
Test a same-store signed-horizon model separately and label it as a model, never
as a production implementation. Missing/invalid state must deny in that model.

No Docker commands, operational files, real login/provider, new credentials,
trust-store change, release or RASAi mutation are part of this work. Tests with
generated synthetic key bytes do not provision installation credentials.

## Evidence and completion boundary — 2026-10-10

[test_oidc_marker_retention_safety.py](../../tests/devenv/test_oidc_marker_retention_safety.py)
exercises both public broker classes. Its named counterexamples demonstrate the
two failures above; ordinary restart with intact markers continues to reject reuse.
The signed-horizon model rejects reuse after rollback/restart while the advanced
horizon is retained, then admits the same tuple when an older authentic horizon
is restored. This isolates the unresolved restore boundary rather than attributing
the entire problem to ordinary restart. It introduces no production horizon file.

Other cases cover the inclusive deadline and delayed code issuance, synchronized
atomic creation, old/future mtimes, unknown/legacy contents, directory/symlink
collisions, permission/disk/I/O/close failures, zero baseline enumeration/deletion,
and rejection outside LOCAL/TEST. Symlink cases require OS support; a Windows
privilege limitation must be reported as skipped, never as local proof.

Runtime remains unchanged. In particular no new-format marker, horizon authority,
collector, startup-denial mode or record migration has been shipped. Bounded
storage growth, finite sweep lag, cross-process collector locking, no-follow
collector reads and crash durability of a future horizon remain **unproved**.
The synchronized baseline test uses two independent brokers in threads; it does
not claim to test a future inter-process collector lock. There is no operational
update to deploy from this documentation/test-only result.

Run only the three OIDC test files, the affected M11 documentary check, and Ruff /
mypy for the new test file. CI on the final SHA remains required for integration
of this non-destructive evidence. Record results and the exact resume gate on
#174/#47/#48; keep #174 open. The next step is an explicit security/architecture
decision on the two options in section 4, followed by acceptance tests for that
contract before implementing any unlink operation. Preserving markers is the
current safe default; it does not complete the bounded-retention objective.

Local evidence: **63 passed / 2 skipped** across the three OIDC files and M11
documentary check. The two skips are symlinks on Windows without the required
privilege. After test-only typing adjustments the new file independently passed
54 cases with those same two skips; targeted Ruff and strict mypy passed for that
file. GitHub/Linux results and the final commit identity are recorded in #174.
