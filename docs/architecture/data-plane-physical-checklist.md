# Data-plane physical design checklist

## Hot-path rules

- [ ] ownership columns are typed, NOT NULL where applicable and indexed for query shape;
- [ ] no authorization dimension exists only in JSONB;
- [ ] monetary columns use numeric + currency;
- [ ] token/native counters use integer/bigint;
- [ ] no secret column enters factual/rollup tables;
- [ ] no dashboard aggregation runs synchronously on insert;
- [ ] every extra index has a documented query/constraint.

## Projection metadata

Each projection/rollup table includes or is associated with:
- projection version;
- source watermark/as_of;
- build timestamp;
- rebuild status;
- source range;
- processor version when semantics can change.

## Required benchmark queries

1. tenant/client task history by time;
2. task attempt drill-down;
3. client token usage by day;
4. provider/model error rate by time;
5. provider credential failure rate by time;
6. quota utilization;
7. Backoffice cost by provider/model/currency;
8. Backoffice cost by client;
9. CLIENT_ONLY estimate bucket lookup;
10. GLOBAL_PUBLIC safe benchmark lookup.

## Explain-plan gate

Before a read-model/report query is accepted at scale:
- execute EXPLAIN (ANALYZE in safe synthetic environment);
- record dataset shape;
- reject accidental unbounded sequential scan on factual tables for ordinary UI/API queries;
- confirm tenant/client predicate is present where required.
