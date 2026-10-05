# PostgreSQL messaging contract tests — #52

Future implementation must run against PostgreSQL.

## Work claiming

- [ ] two workers using SKIP LOCKED never concurrently own the same active lease;
- [ ] batch claim respects queue_name, due available_at and bounded priority;
- [ ] stale worker cannot complete after lease transfer;
- [ ] lease expiry allows safe reclaim;
- [ ] handler executes outside the claim transaction.

## Delayed work

- [ ] future available_at is not claimed early;
- [ ] newly inserted earlier-due work wakes listener or is found by poll fallback;
- [ ] missed LISTEN/NOTIFY does not lose work;
- [ ] wake-up payload contains no sensitive task data.

## Idempotency/provider safety

- [ ] duplicate work delivery resolves through stable #14 work identity;
- [ ] infrastructure retry does not alter canonical provider max cycles/retry policy;
- [ ] ambiguous provider outcome is not blindly re-dispatched.

## Priority/isolation

- [ ] execution queue has independent worker/concurrency budget from maintenance;
- [ ] high priority within queue is preferred;
- [ ] lower priority eventually progresses under sustained high-priority load;
- [ ] client cannot set arbitrary internal priority/queue.

## Event deliveries

- [ ] outbox publication creates one delivery per registered consumer;
- [ ] UNIQUE(event_id, consumer_name) prevents duplicate transport row;
- [ ] duplicate delivery still reaches consumer inbox safely if redelivered by lease recovery;
- [ ] unsupported version goes DEAD after bounded deterministic handling;
- [ ] replay is audited and preserves original event identity.

## Payload/security

- [ ] inline payload >64 KiB rejected/requires reference;
- [ ] SECRET classified payload rejected;
- [ ] CLIENT_PRIVATE message requires tenant/client scope;
- [ ] queue/log error does not dump payload by default.

## Failure/recovery

- [ ] process crash while LEASED eventually reclaims;
- [ ] PostgreSQL reconnect resumes polling;
- [ ] DEAD replay can resume after operator action;
- [ ] cleanup cannot delete READY/LEASED or required dedupe state.

## Load/reporting

Synthetic benchmark records:
- claim p50/p95/p99;
- completed work/sec;
- oldest backlog age;
- lease-expiry rate;
- DB CPU/IO/locks where available;
- table/index size and dead tuples.
