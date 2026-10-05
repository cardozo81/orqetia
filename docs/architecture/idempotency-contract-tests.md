# Idempotency and crash-recovery contract tests — #14

## HTTP/API

- [ ] same key + same task request concurrently creates exactly one task;
- [ ] both callers resolve to the same logical resource;
- [ ] same key + different payload returns 409;
- [ ] same literal key under another operation does not collide;
- [ ] same key under another tenant/client does not collide or leak;
- [ ] unauthorized principal cannot replay/read result using only the key;
- [ ] raw idempotency key is not logged.

## Transaction crash points

- [ ] crash before idempotency insert => safe clean retry;
- [ ] crash after idempotency insert but before resource create => retry resumes one logical operation;
- [ ] crash after resource create before response => retry returns same resource;
- [ ] crash after transaction commit before HTTP response => retry returns same resource;
- [ ] duplicate outbox publication does not duplicate consumer effect.

## Worker/lease

- [ ] duplicate queue delivery claims one logical work item;
- [ ] stale worker cannot commit after lease/version is lost;
- [ ] worker crash after committed state resumes without repeating committed effect;
- [ ] scheduler duplicate wakeup does not create another provider attempt.

## Provider call

- [ ] provider attempt row exists before network dispatch;
- [ ] provider with request-idempotency support reuses same upstream idempotency token;
- [ ] unsupported provider crash after dispatch becomes AMBIGUOUS_EXTERNAL_OUTCOME;
- [ ] AMBIGUOUS_EXTERNAL_OUTCOME is not automatically re-dispatched as the same attempt;
- [ ] unknown outcome does not fabricate zero usage/cost.

## Accounting/quota

- [ ] repeated usage event creates one usage fact;
- [ ] repeated cost event creates one cost fact per defined basis/source;
- [ ] duplicate quota reserve returns same reservation;
- [ ] duplicate quota commit/reconcile does not double debit;
- [ ] event replay does not double-count dashboard/read-model totals after reconciliation.

## Cancellation

- [ ] repeated cancellation is harmless/idempotent;
- [ ] cancellation/completion race produces one valid terminal state;
- [ ] cancelled task cannot be resurrected by stale retry.

## Retention

- [ ] API key mapping expires according to policy without deleting internal task/attempt/accounting dedupe identity;
- [ ] cleanup is idempotent and cannot remove active in-progress records.
