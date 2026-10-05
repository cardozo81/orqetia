# ORQETIA process composition roots

These directories are composition roots only:

- `api/` — future FastAPI process (#103);
- `worker/` — future durable work consumer (#105);
- `scheduler/` — future scheduler/event publisher (#105).

Business/domain code belongs under `src/orqetia/<bounded_context>`.
