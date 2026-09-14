#!/usr/bin/env bash
set -euo pipefail

: "${PGBACKREST_STANZA:?stanza name is required}"
: "${LOCALFORGE_BACKUP_FULL_SCHEDULE:?full backup schedule is required}"
: "${LOCALFORGE_BACKUP_DIFF_SCHEDULE:?differential backup schedule is required}"
: "${POSTGRES_HOST:?primary host is required}"
: "${POSTGRES_PORT:?primary port is required}"

EXIT_STANZA_FAILED=1
EXIT_BACKUP_FAILED=2

WAIT_SECONDS="${LOCALFORGE_BACKUP_WAIT_SECONDS:-180}"
last_run=""
child=""

log() {
  printf '%s pgbackrest_entrypoint: %s\n' "$(date --iso-8601=seconds)" "$1" >&2
}

shut_down() {
  log "shutting down"
  if [ -n "${child}" ]; then
    kill -TERM "${child}" 2>/dev/null || true
    wait "${child}" 2>/dev/null || true
  fi
  exit 0
}

trap shut_down TERM INT

wait_for_primary() {
  local waited=0
  until pg_isready --host "${POSTGRES_HOST}" --port "${POSTGRES_PORT}" --quiet; do
    if [ "${waited}" -ge "${WAIT_SECONDS}" ]; then
      log "primary ${POSTGRES_HOST}:${POSTGRES_PORT} never became ready"
      exit "${EXIT_STANZA_FAILED}"
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

ensure_stanza() {
  log "ensuring stanza ${PGBACKREST_STANZA} exists"
  if ! pgbackrest --stanza="${PGBACKREST_STANZA}" stanza-create; then
    log "stanza creation failed"
    exit "${EXIT_STANZA_FAILED}"
  fi
}

verify_configuration() {
  log "checking that the primary and the agent agree on the data directory"
  if ! pgbackrest --stanza="${PGBACKREST_STANZA}" check; then
    log "configuration check failed"
    exit "${EXIT_STANZA_FAILED}"
  fi
}

has_full_backup() {
  pgbackrest --stanza="${PGBACKREST_STANZA}" info --output=json 2>/dev/null \
    | grep -q '"type":"full"'
}

matches() {
  local pattern="$1" value="$2" part low high
  [ "${pattern}" = '*' ] && return 0

  for part in ${pattern//,/ }; do
    case "${part}" in
      */*)
        [ $((10#${value} % ${part##*/})) -eq 0 ] && return 0
        ;;
      *-*)
        low="${part%%-*}"
        high="${part##*-}"
        if [ "$((10#${value}))" -ge "$((10#${low}))" ] \
          && [ "$((10#${value}))" -le "$((10#${high}))" ]; then
          return 0
        fi
        ;;
      *)
        [ "$((10#${value}))" -eq "$((10#${part}))" ] && return 0
        ;;
    esac
  done

  return 1
}

due() {
  local schedule="$1" stamp="$2"
  local minute hour day month weekday
  local s_minute s_hour s_day s_month s_weekday
  read -r s_minute s_hour s_day s_month s_weekday <<<"${schedule}"
  read -r minute hour day month weekday <<<"${stamp}"

  matches "${s_minute}" "${minute}" \
    && matches "${s_hour}" "${hour}" \
    && matches "${s_day}" "${day}" \
    && matches "${s_month}" "${month}" \
    && matches "${s_weekday}" "${weekday}"
}

run_backup() {
  local kind="$1"
  log "starting a ${kind} backup"
  pgbackrest --stanza="${PGBACKREST_STANZA}" --type="${kind}" backup &
  child=$!
  if ! wait "${child}"; then
    child=""
    log "${kind} backup failed"
    exit "${EXIT_BACKUP_FAILED}"
  fi
  child=""
  log "${kind} backup complete"
}

wait_for_primary
ensure_stanza
verify_configuration

if ! has_full_backup; then
  log "the repository holds no full backup yet"
  run_backup full
fi

log "entering the schedule loop"
while true; do
  now="$(date -u +'%-M %-H %-d %-m %w %Y-%m-%dT%H:%M')"
  stamp="${now% *}"
  minute_key="${now##* }"

  if [ "${minute_key}" != "${last_run}" ]; then
    if due "${LOCALFORGE_BACKUP_FULL_SCHEDULE}" "${stamp}"; then
      last_run="${minute_key}"
      run_backup full
    elif due "${LOCALFORGE_BACKUP_DIFF_SCHEDULE}" "${stamp}"; then
      last_run="${minute_key}"
      run_backup diff
    fi
  fi

  sleep 20 &
  child=$!
  wait "${child}" 2>/dev/null || true
  child=""
done
