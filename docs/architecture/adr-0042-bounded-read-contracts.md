# Bounded read contracts

Issue: #161  
Contract version: 1  
State: DEVELOPMENT

## Client Usage

- page size remains 1..100 at the HTTP contract;
- cursor is query-fingerprint-bound to tenant/client + time filters;
- source rollup scan is capped at 5,000 rows;
- 5,001+ matching source rows fail closed with HTTP 413
  `READ_LIMIT_EXCEEDED` instead of materializing an unbounded result;
- invalid cursor/filter syntax remains a sanitized 400.

## Attempts

Attempt cursors carry a version, offset and SHA-256 query fingerprint derived from
tenant_id + client_id + task_id. A cursor from another task/owner is rejected as
an invalid request and never becomes authority.

## Backoffice reporting

- interactive page size is bounded by the provider-cost surface policy
  (currently 200);
- financial export requires both period_from and period_to;
- any explicit report time range is capped at 366 days;
- export row cap remains the versioned surface policy maximum;
- tenant-restricted access still requires an authorized tenant filter.

## Safety

Cursor contents never grant ownership. Authorization is resolved before data
return. Export/read limits are enforced below presentation layers and do not
create client-facing financial surfaces.
