# Build plan

Authoritative for: the ordered steps, and the pass/fail criterion for each.

This describes work that has **not** been done. Read [../adr/README.md](../adr/README.md),
[../platform/conventions.md](../platform/conventions.md),
[../platform/service-inventory.md](../platform/service-inventory.md),
[../platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md), and
[prerequisites.md](./prerequisites.md) before starting.

## 1. Repository starting state

Observed 2026-09-13. Re-verify; the tree may have moved on.

| Fact | Value |
|---|---|
| Django project root | `src/` |
| Settings | `src/config/settings.py` — a **single module**, to become a package |
| ASGI entry point | `src/config/asgi.py`, plain `get_asgi_application()` |
| Database | SQLite at `BASE_DIR / "db.sqlite3"` |
| Dependencies | `uv` with `[dependency-groups]`. **No `requirements.txt` exists and none is created** |
| Task runner | `poethepoet`; `uv run poe check` is the full gate |
| Python | `requires-python = ">=3.14"`, `.python-version` `3.14.6` |
| Django | `>=6.0,<6.1` |
| Quality gate | Django checks, Ruff format + lint with `select = ["ALL"]`, mypy strict, ty, pytest at **100% branch coverage** |

Two constraints that are easy to trip over:

1. Ruff runs `select = ["ALL"]` and mypy runs `strict = true`. Everything written here must satisfy both, including
   `scripts/`.
2. Coverage must stay at 100% with `--cov-fail-under=100`. New modules need tests or must be genuinely
   import-only. Do not lower the threshold; do not scatter `# pragma: no cover`.

## 2. Files this plan creates

Listed so scope creep is recognisable. None exists.

| Path | Purpose |
|---|---|
| `compose.yaml` | Shared services and the project name |
| `compose.development.yaml`, `compose.testing.yaml` | Per-environment overlays, each owning its own networks and volumes because the two registries are disjoint |
| `docker/django/Dockerfile` | Multi-stage app image with a `test` stage |
| `docker/django/entrypoint.sh` | Wait for dependencies, migrate, collectstatic, exec the server |
| `docker/pgbackrest/Dockerfile` | pgBackRest image; no first-party image exists upstream |
| `docker/pgbackrest/pgbackrest.conf` | Stanza and repository paths |
| `docs/runbooks/restore-drill.md` | The restore drill ticket 05 requires be documented and performed |
| `docker/postgres/primary/` | `postgresql.conf` fragments, init SQL for the replication role and slot |
| `docker/traefik/traefik.yaml` | Static Traefik configuration |
| `docker/prometheus/prometheus.yml` | Scrape configuration |
| `docker/grafana/provisioning/` | Datasource and dashboard provisioning |
| `docker/loki/loki.yaml` | Loki single-binary configuration |
| `docker/alloy/config.alloy` | Log discovery and shipping |
| `docker/pgadmin/servers.json` | Pre-registered PostgreSQL servers |
| `docker/seaweedfs/s3.json` | S3 identities and keys |
| `docker/postgres-exporter/auth_modules.yaml` | Probe credentials for the standby, rendered at start from the environment |
| `scripts/*.py`, `scripts/*.sh` | The nine scripts in [../platform/conventions.md](../platform/conventions.md) Section 4 |
| `.env.example` | Committed variable manifest, placeholders only |
| `.env.development.sops`, `.env.testing.sops` | Committed encrypted env files |
| `.sops.yaml` | age recipient configuration |
| `src/config/settings/` | `__init__.py`, `base.py`, `development.py`, `testing.py` |
| `src/config/routing.py` | Channels routing, empty router |
| `src/config/celery.py` | Celery application object |
| `src/config/db_router.py` | Primary/replica router |
| `src/config/api.py` | Schema, Swagger UI, ReDoc views only |
| `tests/integration/test_*.py` | One test per service integration |
| `tests/unit/scripts/test_*.py` | Unit tests for the scripts above, required by their tickets and by the 100% coverage gate |

## 3. Phases

Nine phases. **A phase may not begin until the previous gate passes.** If a gate fails, stop and fix it. Never
weaken a gate to make it pass.

### Phase 1 — Read

Read every file in `docs/` and `AGENTS.md` end to end.

Gate: without re-reading, state the ID of every service, the two reversed defaults (MinIO, Promtail), and the list
of services excluded from testing.

### Phase 2 — Plan

Turn Section 2 into a concrete task list.

Gate: it covers every file in Section 2 and creates nothing outside it. Anything else needed is a documentation
change first.

### Phase 3 — Prerequisites

```console
uv run python scripts/preflight.py
```

Pass: exit `0`; items 1–9 of [prerequisites.md](./prerequisites.md) green. SOPS and age may be absent this early —
they are needed only before committing an encrypted env file.

### Phase 4 — Django scaffolding, wired to nothing

No container runs here.

**4a.** Convert `src/config/settings.py` into a package: `base.py` holding today's content with values read through
`django-environ`, plus `development.py` and `testing.py`.

Three configuration references point at the old path and **must** move in the same change, or the repository fails
its own gate:

| File | Key | From | To |
|---|---|---|---|
| `pyproject.toml` | `[tool.pytest.ini_options] DJANGO_SETTINGS_MODULE` | `config.settings` | `config.settings.testing` |
| `pyproject.toml` | `[tool.django-stubs] django_settings_module` | `config.settings` | `config.settings.base` |
| `pyproject.toml` | `[tool.ruff.lint.per-file-ignores]` | `"src/config/settings.py"` | `"src/config/settings/*.py"` |

**4b.** Add dependencies with `uv add` into the right groups. Do not hand-edit dependency lists; do not create a
`requirements.txt`. Pin `asgiref>=3.9.1` — Django 6.0 raised its floor from 3.8.1, and Channels 4.3.2 only requires
`>=3.9.0`, so the lockfile must carry the higher bound.

**4c.** Rewrite `src/config/asgi.py` as a `ProtocolTypeRouter` with an HTTP branch and an empty WebSocket branch.

**4d.** Add `src/config/api.py` with the schema, Swagger UI, and ReDoc routes. Configure `drf-spectacular-sidecar`
(`INSTALLED_APPS` entry plus the three `'SIDECAR'` keys) or both UIs render blank offline. The application surface
those UIs document is built later, in ticket phase 4; at this stage the schema is near-empty and that is correct.

**4e.** Add `db_router.py`, `celery.py`, `routing.py`.

Gate:

```console
uv run poe check
```

Pass: exit `0`. A coverage drop below 100% means new modules need tests, not a lower threshold.

### Phase 5 — Development infrastructure

Write the Compose files using the exact names, ports, networks, and volumes from
[../platform/conventions.md](../platform/conventions.md) and
[../platform/service-inventory.md](../platform/service-inventory.md).

Before pinning RabbitMQ, check whether 4.3.x is still within community support — it ends **2026-11-30**. If a newer
community-supported series is current, pin that and update the inventory.

```console
uv run python scripts/gen_secrets.py --environment all
docker compose --env-file .env.development -f compose.yaml -f compose.development.yaml up -d --build
```

**Gate 5a — everything healthy.**

```console
docker compose --env-file .env.development -f compose.yaml -f compose.development.yaml ps
```

Pass: 22 rows, every `State` `running`, every health-checked service `healthy`, nothing `restarting`.

**Gate 5a-i — the media bucket exists.** Object storage starts empty, so the bucket the application uploads into
is created once the gateway is healthy. The step is idempotent, so it is safe on every bring-up. It runs from the
host, so it is pointed at the published port rather than the container name the environment file carries.

```console
uv run python scripts/seed_storage.py --environment development --endpoint http://127.0.0.1:8333
```

Pass: exit `0`, reporting the bucket `created` on a fresh volume or `already present` afterwards. Exit `1` means
the gateway is unreachable or nothing is configured; exit `2` means it refused the configured keys.

**Gate 5b — every host port answers.**

```console
foreach ($p in 8080,8081,8000,5432,5433,5050,6379,6380,5672,15672,5555,1025,8025,9333,8082,8888,8333,9090,3000,3100,12345,8090,9187,9121,9122) {
  $r = Test-NetConnection -ComputerName 127.0.0.1 -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue
  "{0,-6} {1}" -f $p, $(if ($r) { "OPEN" } else { "CLOSED" })
}
```

Pass: all 25 report `OPEN`. A `CLOSED` port is most often a collision with software already on this machine —
cross-check [../platform/service-inventory.md](../platform/service-inventory.md) Section 1.2.

**Gate 5c — replication is streaming.**

```console
docker exec postgres-pg3ka psql -U localforge_app -d localforge -tAc "SELECT client_addr, state, sync_state FROM pg_stat_replication;"
docker exec postgres-replica-pg6vy psql -U localforge_app -d localforge -tAc "SELECT pg_is_in_recovery();"
```

Pass: exactly one row with `state = streaming`, and `t`. Zero rows means the standby never connected — check the
replication slot and credentials before re-seeding.

### Phase 6 — Integrate Django with each service

One at a time, each with its own test in `tests/integration/`. A batched failure is far harder to attribute.

| Step | Integration | Verification | Pass |
|---|---|---|---|
| 6a | PostgreSQL primary | `docker exec django-uv5n2 python manage.py migrate --check`, then `showmigrations` | exit `0`, nothing unapplied |
| 6b | Replica routing | A test asserting reads use `replica` and writes use `default` | routing correct; `allow_migrate` returns `True` only for `default` |
| 6c | Channels over Valkey | Two `WebsocketCommunicator` instances in one group | a message sent by one arrives at the other |
| 6d | Celery | `docker exec celery-worker-cw8rt celery -A config inspect ping`, then a round-trip task | `pong`, and the task completes |
| 6e | Cache | Write and read through `django.core.cache`; `valkey-cli -n 0 DBSIZE` | value round-trips; DB 0 non-empty and DB 1 untouched |
| 6f | Object storage | Upload through Django's storage API, fetch back from the S3 endpoint | byte-identical |
| 6g | Email | `send_mail`, then `GET http://localhost:8025/api/v1/messages` | Mailpit reports one message with the expected subject |
| 6h | Backup | `pgbackrest --stanza=localforge check`, then `backup --type=full`, then `info` | `check` exits `0`; `info` lists one full backup, status `ok` |
| 6i | Metrics | `curl http://localhost:8000/metrics`; then Prometheus targets | `django_http_requests_total` present; every target at `/targets` is `UP` |
| 6j | Logging | Emit a log line, query Loki through Grafana Explore | retrievable within 30 seconds |
| 6k | Reverse proxy | `curl -H "Host: localforge.localhost" http://localhost:8080/health/` | `200` through Traefik, route visible in the dashboard |
| 6l | Health aggregate | `curl http://localhost:8000/health/?format=json` | every `django-health-check` backend reports `working` |

Three of these — 6c, 6d, 6f — exercise dependencies carrying **release lag**. They are end-to-end on purpose: an
import check would pass while the behaviour is broken. If one fails, apply the escape in
[../adr/0016-accept-release-lag.md](../adr/0016-accept-release-lag.md) for that dependency only, and record the
commit and CI run in that file.

Gate: all twelve pass and `uv run poe check` is still green.

### Phase 7 — Testing environment

**7a.** Write `compose.testing.yaml` with the seven services from
[../platform/service-inventory.md](../platform/service-inventory.md) Section 4 and **no dashboard or UI service**.

```console
docker compose --env-file .env.testing -f compose.yaml -f compose.testing.yaml up -d
uv run python scripts/seed_storage.py --environment testing
docker compose --env-file .env.testing -f compose.yaml -f compose.testing.yaml run --rm django-test-dt5qx
uv run pytest
```

Gate:

- Pass: both runs exit `0` at 100% branch coverage and report **the same number of collected tests**; the stack
  contains nothing from the exclusion list.
- Fail: differing test counts mean environment-dependent skipping, which hides real failures. A host-only failure
  is almost always a `*_HOST` variable in `.env.testing.host` still naming a container instead of `127.0.0.1`.

### Phase 8 — Convention audit

```console
uv run python scripts/audit_naming.py --environment development
uv run python scripts/audit_naming.py --environment testing
```

Then run the five audits in [../platform/service-inventory.md](../platform/service-inventory.md) Section 6 by hand
and compare. **All of them filter by Compose project label** — this machine runs unrelated containers.

Pass: both exit `0`; no anonymous volumes in the project; no `_default` network; every name matches
`^[a-z][a-z-]*-[a-z2-9]{5}$`; every published port matches the inventory.

Fail: fix the Compose file, recreate the affected service, re-run the **full** audit.

### Phase 9 — Hand over to the ticket set

The platform is now running, integrated, and audited. **Stop building infrastructure here** and continue from
[.scratch/README.md](../../.scratch/README.md), where ticket phases 4 through 7 deliver the application surface —
the REST API, WebSockets, the async services, and the full test and audit passes.

Do not start a route, model, or feature outside the fixed surface listed in [AGENTS.md](../../AGENTS.md).

Report before handing over: every file created, grouped by Section 2; how each release-lag gate resolved; any
pinned version that had moved since 2026-09-13, with its new release date; the phase 8 audit output; and anything
in `docs/` that turned out to be wrong.

## 4. Rollback

```console
docker compose --env-file .env.development -f compose.yaml -f compose.development.yaml down --volumes --remove-orphans
```

Deletes named volumes, so database contents and Grafana state are lost. Env files live outside Docker and are
untouched, so `gen_secrets.py` need not run again and credential-derived state stays consistent.

**Never run `docker system prune -a` on this machine.** It is shared, and that command would remove images and
volumes belonging to other work.
