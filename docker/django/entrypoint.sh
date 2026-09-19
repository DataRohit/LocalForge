#!/usr/bin/env bash
set -euo pipefail

: "${DJANGO_SETTINGS_MODULE:?settings module is required}"

WAIT_SERVICES="${LOCALFORGE_WAIT_SERVICES:-postgres}"
WAIT_TIMEOUT="${LOCALFORGE_WAIT_TIMEOUT:-120}"
WORKERS="${UVICORN_WORKERS:-2}"
WEBSOCKET_MAX_SIZE="${UVICORN_WEBSOCKET_MAX_SIZE_BYTES:?WebSocket transport limit is required}"
child=""
metrics_child=""

log() {
  printf '%s django_entrypoint: %s\n' "$(date --iso-8601=seconds)" "$1" >&2
}

forward_signal() {
  log "stopping"
  if [ -n "${child}" ]; then
    kill -TERM "${child}" 2>/dev/null || true
  fi
  if [ -n "${metrics_child}" ]; then
    kill -TERM "${metrics_child}" 2>/dev/null || true
  fi
  wait "${child}" "${metrics_child}" 2>/dev/null || true
  exit 0
}

trap forward_signal TERM INT

supervise() {
  "$@" &
  child=$!
  wait "${child}"
  child=""
}

log "waiting for dependencies: ${WAIT_SERVICES}"
read -r -a wait_targets <<<"${WAIT_SERVICES}"
supervise python /app/scripts/wait_for_services.py "${wait_targets[@]}" --timeout "${WAIT_TIMEOUT}"

if [ "$#" -gt 0 ]; then
  log "handing over to: $*"
  exec "$@"
fi

log "ensuring the media bucket exists"
supervise python /app/scripts/seed_storage.py --process-environment

log "applying migrations"
supervise python /app/src/manage.py migrate --noinput

log "collecting static files"
supervise python /app/src/manage.py collectstatic --noinput --clear

METRICS_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/localforge-prometheus}"
mkdir -p "${METRICS_DIR}"
find "${METRICS_DIR}" -mindepth 1 -maxdepth 1 -type f -delete
export PROMETHEUS_MULTIPROC_DIR="${METRICS_DIR}"
METRICS_HOST="$(
  python -c 'import socket; print(socket.gethostbyname("django-metrics-nb4xt"))'
)"

log "serving on port 8000 with ${WORKERS} worker(s)"
uvicorn config.metrics_asgi:application \
  --host "${METRICS_HOST}" \
  --port 8001 \
  --workers 1 \
  --log-level warning \
  --no-access-log &
metrics_child=$!

uvicorn config.asgi:application \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${WORKERS}" \
  --log-level warning \
  --no-access-log \
  --ws websockets-sansio \
  --ws-max-size "${WEBSOCKET_MAX_SIZE}" &
child=$!

set +e
wait -n "${child}" "${metrics_child}"
status=$?
set -e

kill -TERM "${child}" "${metrics_child}" 2>/dev/null || true
wait "${child}" "${metrics_child}" 2>/dev/null || true

exit "${status}"
