#!/usr/bin/env bash
set -euo pipefail

: "${POSTGRES_REPLICATION_USER:?replication role name is required}"
: "${POSTGRES_REPLICATION_PASSWORD:?replication role password is required}"
: "${POSTGRES_REPLICATION_SLOT:?replication slot name is required}"
: "${POSTGRES_REPLICATION_CIDR:?replication source range is required}"

for identifier in "${POSTGRES_REPLICATION_USER}" "${POSTGRES_REPLICATION_SLOT}"; do
  if ! printf '%s' "${identifier}" | grep -Eq '^[a-z_][a-z0-9_]{0,62}$'; then
    echo "refusing an identifier that is not a bare lowercase name: ${identifier}" >&2
    exit 1
  fi
done

if [ "${POSTGRES_REPLICATION_USER}" = "${POSTGRES_USER}" ]; then
  echo "the replication role must differ from the application role" >&2
  exit 1
fi

if ! printf '%s' "${POSTGRES_REPLICATION_CIDR}" | grep -Eq '^[0-9./]+$'; then
  echo "refusing a replication source range that is not a numeric CIDR" >&2
  exit 1
fi

if ! grep -qF "include_dir = '/etc/postgresql/conf.d'" "${PGDATA}/postgresql.conf"; then
  printf "\ninclude_dir = '/etc/postgresql/conf.d'\n" >>"${PGDATA}/postgresql.conf"
fi

hba_rule="$(printf 'host\treplication\t%s\t%s\tscram-sha-256' \
  "${POSTGRES_REPLICATION_USER}" "${POSTGRES_REPLICATION_CIDR}")"

if ! grep -qF "${hba_rule}" "${PGDATA}/pg_hba.conf"; then
  printf '%s\n' "${hba_rule}" >>"${PGDATA}/pg_hba.conf"
fi

psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --set ON_ERROR_STOP=1 \
  --set replication_user="${POSTGRES_REPLICATION_USER}" \
  --set replication_password="${POSTGRES_REPLICATION_PASSWORD}" \
  --set replication_slot="${POSTGRES_REPLICATION_SLOT}" <<'SQL'
SELECT format(
  'CREATE ROLE %I WITH REPLICATION LOGIN PASSWORD %L',
  :'replication_user',
  :'replication_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'replication_user')
\gexec

SELECT pg_create_physical_replication_slot(:'replication_slot')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_replication_slots WHERE slot_name = :'replication_slot'
);
SQL
