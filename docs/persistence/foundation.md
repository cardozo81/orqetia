# PostgreSQL persistence foundation

Issue: #101  
ADRs: 0005 / 0006 / 0007 / 0016

## Physical schemas

Authoritative bounded-context schemas:

- identity
- control
- execution
- accounting
- estimation
- audit
- readmodel

Infrastructure-only schema:

- messaging

`messaging` is not a business source of truth. Its tables are introduced by
#104.

## SQLAlchemy boundary

Concrete SQLAlchemy infrastructure lives under
`orqetia.infrastructure.persistence`.

Domain/application packages do not import SQLAlchemy or concrete repositories
from another context. Apps compose infrastructure inward through ports as
implementation proceeds.

`metadata_for_schema()` returns isolated metadata per schema instead of one
global metadata registry. This makes cross-context relationships an explicit
exception rather than an accidental default.

## Transactions

`transaction_scope()` owns one local READ COMMITTED transaction.

External provider calls must occur outside the transaction. The helper commits
on successful exit and rolls back before propagating an exception.

## Database roles

Alembic does not create login roles or passwords.

Deployment supplies separate identities for migrations, API, worker, scheduler,
and Backoffice. Runtime roles must not use superuser/BYPASSRLS. Exact table
GRANTs/RLS policies are introduced with their owning tables, where negative
tenant-isolation tests can prove them.

## Migration policy

- one Alembic graph;
- explicit deployment migration step;
- no API/worker auto-migrate;
- PostgreSQL 18 integration test;
- no SQLite migration/locking parity claim;
- cross-context FKs prohibited by default;
- schema/table ownership remains extractable.

## Secrets

The database DSN is loaded through #100 as a `SecretStr`. No provider secret,
client credential plaintext, KEK, or real fixture is introduced by the
persistence foundation.
