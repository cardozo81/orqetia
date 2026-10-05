# Local Docker Compose development stack

Issue: #102  
ADR: 0004  
Product state: DEVELOPMENT

## Purpose

`compose.yaml` is the canonical local integration topology:

- api;
- worker;
- scheduler;
- migrate;
- PostgreSQL 18.

There is no Redis/RabbitMQ/Kafka/Celery broker in the baseline. #104 implements
the PostgreSQL-backed messaging transport.

## Current process harness

Until #103/#105, api/worker/scheduler run
`scripts/dev_container_process.py`.

This is intentionally not application behavior. It only:

1. loads/validates #100 typed settings;
2. verifies the configured process role;
3. emits a safe redacted startup record;
4. remains alive;
5. handles SIGTERM/SIGINT and exits cleanly.

#103 replaces the API command with FastAPI/Uvicorn. #105 replaces worker and
scheduler commands with their real process shells.

## Runtime image

The image:

- uses Python 3.14.3;
- installs from committed `uv.lock`;
- copies no `.env` or provider credential;
- runs as uid/gid 10001 rather than root;
- has a read-only root filesystem in Compose;
- uses only `/tmp` tmpfs for transient writes;
- includes Alembic migrations so deployment/local migration remains an explicit
  command rather than app-startup behavior.

## PostgreSQL

PostgreSQL 18 is private to the Compose bridge network.

For developer tooling only, port 5432 is bound to `127.0.0.1`, never
`0.0.0.0`.

The named volume `postgres_data` persists across ordinary
`docker compose down` / `up`. Use `docker compose down -v` only when an
intentional local reset is desired.

## Common commands

Build and start:

    docker compose up --build -d postgres api worker scheduler

Apply migrations explicitly:

    docker compose run --rm migrate

Inspect:

    docker compose ps
    docker compose logs api worker scheduler

Stop while preserving the database volume:

    docker compose down

Destroy local data explicitly:

    docker compose down -v

## Security boundary

All credentials in `compose.yaml` are synthetic local-only values. Real
provider credentials are neither required nor accepted by the baseline Compose
environment.

The rendered Compose contract is validated in CI to reject:

- a public PostgreSQL host binding;
- privileged containers;
- host networking;
- provider credential environment variables;
- missing DB/secret-store settings.

## Cost

The stack is local/container-only and performs no provider calls.
