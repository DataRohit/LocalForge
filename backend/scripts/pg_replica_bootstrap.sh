#!/usr/bin/env bash
set -euo pipefail

: "${POSTGRES_REPLICATION_ALIAS:?primary alias on the data zone is required}"
: "${POSTGRES_PORT:?primary port is required}"
: "${POSTGRES_REPLICATION_USER:?replication role name is required}"
: "${POSTGRES_REPLICATION_PASSWORD:?replication role password is required}"
: "${POSTGRES_REPLICATION_SLOT:?replication slot name is required}"

PGDATA="${PGDATA:-/var/lib/postgresql/18/docker}"
WAIT_SECONDS="${REPLICA_WAIT_SECONDS:-120}"

EXIT_PRIMARY_UNREACHABLE=1
EXIT_DATA_DIRECTORY_UNUSABLE=2

log() {
  printf '%s pg_replica_bootstrap: %s\n' "$(date --iso-8601=seconds)" "$1" >&2
}

wait_for_primary() {
  local waited=0
  until pg_isready --host "${POSTGRES_REPLICATION_ALIAS}" --port "${POSTGRES_PORT}" --quiet; do
    if [ "${waited}" -ge "${WAIT_SECONDS}" ]; then
      log "primary ${POSTGRES_REPLICATION_ALIAS}:${POSTGRES_PORT} unreachable after ${WAIT_SECONDS}s"
      exit "${EXIT_PRIMARY_UNREACHABLE}"
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

already_seeded() {
  [ -n "$(ls -A "${PGDATA}" 2>/dev/null)" ]
}

valid_standby() {
  [ -f "${PGDATA}/PG_VERSION" ] && [ -f "${PGDATA}/standby.signal" ]
}

seed_from_primary() {
  log "seeding a new standby from ${POSTGRES_REPLICATION_ALIAS}:${POSTGRES_PORT}"
  mkdir -p "${PGDATA}"
  PGPASSWORD="${POSTGRES_REPLICATION_PASSWORD}" pg_basebackup \
    --host "${POSTGRES_REPLICATION_ALIAS}" \
    --port "${POSTGRES_PORT}" \
    --username "${POSTGRES_REPLICATION_USER}" \
    --pgdata "${PGDATA}" \
    --wal-method stream \
    --slot "${POSTGRES_REPLICATION_SLOT}" \
    --write-recovery-conf \
    --checkpoint fast \
    --progress \
    --verbose
  chmod 0700 "${PGDATA}"
  log "base backup complete"
}

wait_for_primary

if already_seeded; then
  if valid_standby; then
    log "reusing the existing standby in ${PGDATA}"
  else
    log "${PGDATA} is not empty and is not a valid standby; refusing to overwrite it"
    exit "${EXIT_DATA_DIRECTORY_UNUSABLE}"
  fi
else
  seed_from_primary
fi

exec docker-entrypoint.sh "$@"
