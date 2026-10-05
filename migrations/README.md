# ORQETIA migrations

Alembic owns one repository-controlled migration graph.

Rules from ADR-0005/0006:

- every migration names its owning bounded context in its header/name;
- cross-context foreign keys are prohibited by default;
- migrations are controlled deployment operations, never API/worker startup side effects;
- destructive changes require retention/recovery analysis;
- expand/migrate/contract is preferred for incompatible production changes;
- PostgreSQL is the migration test database; SQLite does not claim parity.

The initial revision creates only logical schemas. Database login roles and
passwords are deployment concerns and are not created by application migrations.

Role separation expected by deployment:

- migration/maintenance identity: schema evolution only;
- API runtime identity: least-privilege client-facing data access, no BYPASSRLS;
- worker runtime identity: scoped runtime mutation, no BYPASSRLS;
- scheduler runtime identity: only required scheduling/messaging privileges;
- Backoffice runtime identity: separate cross-tenant path, still application-authorized/audited.

Concrete GRANTs are added with the tables that require them; no blanket
cross-schema SELECT is granted by this foundation.
