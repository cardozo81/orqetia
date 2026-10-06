#!/usr/bin/env bash
set -euo pipefail

SOURCE_URL="${ORQETIA_RESTORE_SOURCE_URL:-postgresql://postgres@127.0.0.1:5432/orqetia_test}"
ADMIN_URL="${ORQETIA_RESTORE_ADMIN_URL:-postgresql://postgres@127.0.0.1:5432/postgres}"
RESTORE_DB="${ORQETIA_RESTORE_DB:-orqetia_restore_drill}"
POSTGRES_IMAGE="${ORQETIA_POSTGRES_IMAGE:-postgres:18}"
ARTIFACT_ID="00000000-0000-7000-8000-000000000148"
DUMP_FILE="${ORQETIA_RESTORE_DUMP_FILE:-.orqetia-restore-drill.dump}"

if [[ ! "${RESTORE_DB}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "restore database name must contain only letters, digits and underscore" >&2
  exit 2
fi

RESTORE_URL="${ADMIN_URL%/*}/${RESTORE_DB}"

psql_container() {
  docker run --rm --network host "${POSTGRES_IMAGE}" \
    psql "$1" -v ON_ERROR_STOP=1 "${@:2}"
}

cleanup() {
  psql_container "${SOURCE_URL}" -c \
    "DELETE FROM execution.client_artifacts WHERE artifact_id = '${ARTIFACT_ID}'" \
    >/dev/null 2>&1 || true
  psql_container "${ADMIN_URL}" -c \
    "DROP DATABASE IF EXISTS ${RESTORE_DB} WITH (FORCE)" \
    >/dev/null 2>&1 || true
  rm -f "${DUMP_FILE}"
}
trap cleanup EXIT

psql_container "${ADMIN_URL}" -c \
  "DROP DATABASE IF EXISTS ${RESTORE_DB} WITH (FORCE)" >/dev/null

docker run --rm --network host -i "${POSTGRES_IMAGE}" \
  psql "${SOURCE_URL}" -v ON_ERROR_STOP=1 <<SQL
DELETE FROM execution.client_artifacts
WHERE artifact_id = '${ARTIFACT_ID}';

INSERT INTO execution.client_artifacts (
  artifact_id, tenant_id, client_id, task_id, attempt_id, kind, media_type,
  content_json, content_text, output_kind, sha256, byte_size, created_at
) VALUES (
  '${ARTIFACT_ID}',
  '00000000-0000-7000-8000-000000000001',
  '00000000-0000-7000-8000-000000000002',
  NULL, NULL, 'REQUEST', 'application/json',
  '{"restore_drill":"synthetic"}'::jsonb,
  NULL, NULL,
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  29, now()
);
SQL

SOURCE_VERSION="$(psql_container "${SOURCE_URL}" -At -c \
  "SELECT version_num FROM alembic_version LIMIT 1")"

docker run --rm --network host "${POSTGRES_IMAGE}" \
  pg_dump "${SOURCE_URL}" --format=custom --no-owner --no-privileges > "${DUMP_FILE}"

if [[ ! -s "${DUMP_FILE}" ]]; then
  echo "pg_dump produced an empty backup" >&2
  exit 1
fi

psql_container "${ADMIN_URL}" -c "CREATE DATABASE ${RESTORE_DB}" >/dev/null

docker run --rm --network host -i "${POSTGRES_IMAGE}" \
  pg_restore --dbname="${RESTORE_URL}" --no-owner --no-privileges --exit-on-error < "${DUMP_FILE}"

RESTORED_VERSION="$(psql_container "${RESTORE_URL}" -At -c \
  "SELECT version_num FROM alembic_version LIMIT 1")"
if [[ "${SOURCE_VERSION}" != "${RESTORED_VERSION}" ]]; then
  echo "alembic version mismatch after restore" >&2
  exit 1
fi

RESTORED_MARKER="$(psql_container "${RESTORE_URL}" -At -c \
  "SELECT content_json->>'restore_drill' FROM execution.client_artifacts WHERE artifact_id = '${ARTIFACT_ID}'")"
if [[ "${RESTORED_MARKER}" != "synthetic" ]]; then
  echo "client-private artifact missing after restore" >&2
  exit 1
fi

RESTORED_SCHEMAS="$(psql_container "${RESTORE_URL}" -At -c \
  "SELECT count(DISTINCT table_schema) FROM information_schema.tables WHERE table_schema IN ('execution','accounting','control','identity','audit','reporting','messaging')")"
if [[ "${RESTORED_SCHEMAS}" -lt 5 ]]; then
  echo "expected ORQETIA schemas were not restored" >&2
  exit 1
fi

echo "restore_drill status=success alembic=${RESTORED_VERSION} schemas=${RESTORED_SCHEMAS}"
