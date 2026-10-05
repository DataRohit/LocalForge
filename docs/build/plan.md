# Build plan

Authoritative for: the ordered steps, and the pass/fail criterion for each.

This records the ordered build phases and their gates. Read [../adr/README.md](../adr/README.md),
[../platform/conventions.md](../platform/conventions.md),
[../platform/service-inventory.md](../platform/service-inventory.md),
[../platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md), and
[prerequisites.md](./prerequisites.md) before starting.

## 1. Repository starting state

Observed 2026-10-04 before Phase 10. Re-verify during Ticket 71; the tree may move during the migration.
The unqualified paths in this historical snapshot describe the pre-move tree; all current repository paths and
commands below use `backend/` or the root wrappers.

| Fact | Value |
| --- | --- |
| Django project root | `backend/src/` (the pre-move snapshot used `src/`) |
| Settings | `backend/src/config/settings.py` — a **single module**, to become a package |
| ASGI entry point | `backend/src/config/asgi.py`, plain `get_asgi_application()` |
| Database | SQLite at `BASE_DIR / "db.sqlite3"` |
| Dependencies | `uv` with `[dependency-groups]` in `backend/pyproject.toml`; target metadata location is `backend/` |
| Task runner | `poethepoet`; `./localforge.sh check` is the full gate |
| Python | `requires-python = ">=3.14"`, `backend/.python-version` `3.14.6` |
| Django | `>=6.0,<6.1` |
| Quality gate | Django checks, Ruff format + lint with `select = ["ALL"]`, mypy strict, ty, pytest at **100% branch coverage** |

Two constraints that are easy to trip over:

1. Ruff runs `select = ["ALL"]` and mypy runs `strict = true`. Everything written here must satisfy both, including
   `backend/scripts/`.
2. Coverage must stay at 100% with `--cov-fail-under=100`. New modules need tests or must be genuinely
   import-only. Do not lower the threshold; do not scatter `# pragma: no cover`.

## 2. Files this plan creates

Listed so scope creep is recognisable. Rows from the completed build now exist; Phase 10 adds only the monorepo
planning documents and handover named below.

| Path | Purpose |
| --- | --- |
| `compose.yaml` | Shared services and the project name |
| `compose.development.yaml`, `compose.testing.yaml` | Per-environment overlays, each owning its own networks and volumes because the two registries are disjoint |
| `compose.proxy-only.yaml` | Development override that removes the direct Django host port |
| `docker/django/Dockerfile` | Multi-stage app image with a `test` stage |
| `docker/django/entrypoint.sh` | Wait for dependencies, migrate, collectstatic, exec the server |
| `docker/pgbackrest/Dockerfile` | pgBackRest image; no first-party image exists upstream |
| `docker/pgbackrest/pgbackrest.conf` | Stanza and repository paths |
| `docs/runbooks/restore-drill.md` | The restore drill ticket 05 requires be documented and performed |
| `docs/api/openapi-v1.yaml` | Deterministic OpenAPI 3.1 artifact for the fixed versioned REST and health contract, committed so review exposes contract drift |
| `docs/api/websocket-v1.md` | Versioned planned WebSocket message and close-code contract, kept separate until Phase 5 can verify it at runtime |
| `docker/postgres/primary/` | `postgresql.conf` fragments, init SQL for the replication role and slot |
| `docker/traefik/traefik.yaml` | Static Traefik configuration |
| `docker/prometheus/prometheus.yml` | Scrape configuration |
| `docker/grafana/provisioning/` | Datasource and dashboard provisioning |
| `docker/loki/loki.yaml` | Loki single-binary configuration |
| `docker/alloy/config.alloy` | Log discovery and shipping |
| `docker/cadvisor/machine-id` | Stable non-secret local machine identity mounted read-only so cAdvisor does not emit recurring missing-UUID warnings |
| `docker/pgadmin/servers.json` | Pre-registered PostgreSQL servers |
| `docker/seaweedfs/s3.json` | S3 identities and keys |
| `docker/postgres-exporter/auth_modules.yaml` | Probe credentials for the standby, rendered at start from the environment |
| `backend/scripts/*.py`, `backend/scripts/*.sh` | The scripts in [../platform/conventions.md](../platform/conventions.md) Section 4 |
| `backend/scripts/manage_platform.py` | Cross-platform operator command adapter exposed through Poe; centralizes safe and destructive Compose workflows |
| `docs/security/security-audit.md`, `docs/security/image-vulnerability-policy.json` | Dated Phase 7 findings, accepted risks, review dates, and the exact machine-enforced image scan snapshot |
| `docs/handover/phase-7.md` | Final clean-checkout, quality, contract, runtime, risk, review-date, and deferred-scope evidence for Phase 7 |
| `docs/architecture/solid-audit-plan.md` | Phase 8 interpretation, scope, evidence rules, and gates for the SOLID architecture audit |
| `docs/architecture/solid-findings.md` | Phase 8 module inventory and evidence-backed finding ledger, completed by ticket 53 |
| `docs/handover/phase-8.md` | Final SOLID findings, changes, verification, runtime evidence, and deferred work, created by ticket 63 |
| `docs/adr/0022-public-edge-and-resend.md` | Accepted public deployment and Resend boundary for Phase 9 |
| `docs/architecture/phase-9-public-edge-email.md` | Phase 9 scope, security boundary, email contract, and gate |
| `docs/architecture/phase-9-public-edge-email-spec.md` | Phase 9 problem statement, user stories, decisions, and test contract |
| `docs/runbooks/phase-9-public-edge-email.md` | Phase 9 DNS, Tunnel, email, and rollback runbook |
| `docs/handover/phase-9.md` | Phase 9 public deployment evidence, secret ownership, rollback procedure, and manual closeout state |
| `docs/adr/0023-monorepo-layout.md` | Accepted backend-first monorepo ownership decision |
| `docs/architecture/phase-10-monorepo.md` | Phase 10 scope, ownership boundary, and gate |
| `docs/architecture/phase-10-monorepo-spec.md` | Phase 10 requirements and acceptance evidence |
| `docs/architecture/phase-10-monorepo-inventory.md` | Pre-move file ownership and path-sensitive surface inventory |
| `docs/handover/phase-10.md` | Phase 10 final migration evidence and rollback record |
| `localforge.ps1`, `localforge.sh` | Root command entry points; select the backend project when it exists and run Poe from its project directory |
| `docs/platform/documentation-standard.md` | The worked reference for the docstring standard the checker enforces |
| `.env.example` | Committed variable manifest, placeholders only |
| `.env.development.sops`, `.env.testing.sops` | Committed encrypted env files |
| `.sops.yaml` | age recipient configuration |
| `.gitleaks.toml` | Pinned Gitleaks false-positive policy for documented test credentials and protocol examples |
| `backend/src/config/settings/` | `__init__.py`, `base.py`, `development.py`, `testing.py` |
| `backend/src/config/logs.py` | Structured log formatter the logging configuration names |
| `backend/src/config/openapi.py` | OpenAPI post-processing authority separated from the runtime HTTP boundary |
| `backend/src/config/routing.py` | Channels routing, empty router |
| `backend/src/config/channels.py` | Channel layer whose subscribe waits for the instance to register it |
| `backend/src/config/celery.py` | Celery application object |
| `backend/src/config/tasks.py` | Operational worker probes and scheduler-owned maintenance tasks |
| `backend/src/config/db_router.py` | Primary/replica router |
| `backend/src/config/email.py` | Multipart application email rendering and failure-safe delivery |
| `backend/src/config/templates/email/` | Plain-text and HTML application email templates |
| `backend/src/config/templates/drf_spectacular/redoc.html` | Offline ReDoc shell without remote font requests |
| `backend/src/config/health.py` | Health view executor that preserves request correlation |
| `backend/src/config/metrics_asgi.py`, `backend/src/config/metrics_urls.py` | Observability-only metrics listener |
| `backend/src/config/api.py`, `backend/src/config/api_errors.py` | Versioned API routing and the shared error boundary and vocabulary |
| `backend/src/config/security.py` | Browser security headers and exact-origin credentialed CORS response boundary |
| `backend/src/config/cache.py` | Resilient general caching |
| `backend/src/accounts/login_throttle.py` | Authoritative primary-database login admission |
| `backend/src/accounts/credentials.py` | Account credential verification, profile classification, and locked revalidation policy |
| `backend/src/accounts/request_validation.py` | Shared strict account-request and normalized-email validation policy |
| `backend/src/accounts/response_timing.py` | Shared monotonic public-response timing floor for enumeration-resistant workflows |
| `backend/src/accounts/` | The user model, manager, admin, migrations, and first-party account authentication endpoints |
| `backend/src/notifications/` | Authenticated WebSocket protocol, notification consumer, and user-targeted publisher |
| `backend/tests/integration/<package>/test_*.py` | One test per service integration, mirroring the package it covers |
| `backend/tests/conftest.py` | Per-worker namespace every externally allocated name is built from |
| `backend/tests/factories.py` | Valid-by-default account and credential-state factories shared by unit and integration tests |
| `backend/tests/websocket.py` | Daphne-free in-process ASGI WebSocket communicator shared by integration tests |
| `backend/tests/unit/conftest.py` | Guard refusing network access from the unit layer |
| `backend/tests/integration/conftest.py` | Guard requiring each integration test to declare its services |
| `backend/tests/integration/config/runtime_probe.py` | Host/container probe for Mailpit recreation and genuinely stopped dependency readiness |
| `backend/tests/unit/test_architecture.py` | Enforces runtime dependency direction, public cross-module imports, and executable cycle freedom |
| `backend/tests/unit/test_dependencies.py` | Asserts the dependency baseline is declared, installed, and importable |
| `backend/tests/unit/test_harness.py` | Asserts the suite's own guards and namespacing behave |
| `backend/tests/unit/<package>/test_*.py` | Unit tests mirroring the application packages |
| `backend/tests/unit/scripts/test_*.py` | Unit tests for the scripts above, required by their tickets and by the 100% coverage gate |

## 3. Phases

Ten phases. **A phase may not begin until the previous gate passes.** If a gate fails, stop and fix it. Never
weaken a gate to make it pass.

Every phase gate that starts or changes a runtime service also applies the
[runtime truth rule](../../AGENTS.md#rules): verify the affected environment health, run the project-scoped Docker
audit, exercise the changed deployed seam, and inspect bounded affected-container logs. Unexplained warning-or-higher
records, restarts, unhealthy state, or success logged as failure make the gate fail even when tests pass.

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
uv run --project backend --directory backend python scripts/preflight.py
```

Pass: exit `0`; items 1–9 of [prerequisites.md](./prerequisites.md) green. SOPS and age may be absent this early —
they are needed only before committing an encrypted env file.

### Phase 4 — Django scaffolding, wired to nothing

No application container is built or run in this phase. It is **not** free of infrastructure: once 4a lands, the
phase gate depends on two things the earlier phases produce, because the settings package reads every value from
the environment and the `default` alias is PostgreSQL.

| Prerequisite | Why |
| --- | --- |
| `uv run --project backend --directory backend python scripts/gen_secrets.py --environment all` has been run | `manage.py` under `config.settings.development` reads `.env.development`, and the suite and the type stub plugin read `.env.testing.host`. Without them the gate fails on the first required variable |
| The testing database node is up | `uv run --project backend --directory backend pytest` builds a test database on `postgres-tp8vn`, and the migration check connects to it |

Neither was needed before 4a, when the `default` alias was SQLite and no value was required. Run the generation
step from phase 5 first, and start `postgres-tp8vn` from phase 7's stack; nothing else from either phase is needed.

**Starting the application container applies Django's own migrations, including `auth`.** Swapping
`AUTH_USER_MODEL` afterwards is refused by Django — `admin.0001_initial` would be applied before the accounts
migration it then depends on. Either introduce the custom user model before the application container first runs,
or recreate `postgres-pg3ka-data` and run the suite with `--create-db` once it exists. Measured 2026-09-14, when
ticket 17 ran before ticket 18.

**4a.** Convert `backend/src/config/settings.py` into a package: `base.py` holding today's content with values read through
`django-environ`, plus `development.py` and `testing.py`. The `default` database alias moves to PostgreSQL here
rather than staying on SQLite, because the application container's entrypoint migrates before it binds its port and
its application directory is not writable; the `replica` alias, the router, pooling, and connection health checks
still belong to ticket 19.

Three configuration references point at the old path and **must** move in the same change, or the repository fails
its own gate:

| File | Key | From | To |
| --- | --- | --- | --- |
| `backend/pyproject.toml` | `[tool.pytest.ini_options] DJANGO_SETTINGS_MODULE` | `config.settings` | `config.settings.testing` |
| `backend/pyproject.toml` | `[tool.django-stubs] django_settings_module` | `config.settings` | `config.settings.testing` |
| `backend/pyproject.toml` | `[tool.ruff.lint.per-file-ignores]` | `"src/config/settings.py"` | `"src/config/settings/*.py"` |

Two corrections to that table, made 2026-09-14 when the split was performed:

+ The type stub plugin **imports** the module it is given, so it needs one that resolves its environment. `base.py`
  reads required variables and has no environment file of its own, while `testing.py` names `.env.testing.host` —
  the file host-mode runs already require. It therefore points at `config.settings.testing`.
+ The per-file ignore no longer carries `S105`. That ignore existed because the old module held a literal signing
  key; none remains, and ticket 14 requires the hardcoded-password rules **apply** to the package. The entry now
  ignores only the star import each environment module makes from the base.

**4b.** Add dependencies with `uv add` into the right groups. Do not hand-edit dependency lists; do not create a
`requirements.txt`. Pin `asgiref>=3.9.1` — Django 6.0 raised its floor from 3.8.1, and Channels 4.3.2 only requires
`>=3.9.0`, so the lockfile must carry the higher bound.

**4c.** Rewrite `backend/src/config/asgi.py` as a `ProtocolTypeRouter` with an HTTP branch and an empty WebSocket branch.

**4d.** Add `backend/src/config/api.py` with the schema, Swagger UI, and ReDoc routes. Configure `drf-spectacular-sidecar`
(`INSTALLED_APPS` entry plus the three `'SIDECAR'` keys) or both UIs render blank offline. Remove the remote font
links from the bundled ReDoc HTML shell, and gate all three routes behind the environment-controlled documentation
flag: enabled in development and disabled by default in headless testing. The deployed ASGI entry point serves the
local sidecar finder assets only under that same flag, outside API accounting, and the schema finalizer excludes the
three infrastructure paths from direct generation. The application surface those UIs document is built later, in
ticket phase 4; at this stage the schema is near-empty and that is correct.

**4e.** Add `db_router.py`, `celery.py`, `routing.py`.

Gate:

```console
./localforge.sh check
```

Pass: exit `0`. A coverage drop below 100% means new modules need tests, not a lower threshold.

### Phase 5 — Development infrastructure

Write the Compose files using the exact names, ports, networks, and volumes from
[../platform/conventions.md](../platform/conventions.md) and
[../platform/service-inventory.md](../platform/service-inventory.md).

Before pinning RabbitMQ, check whether 4.3.x is still within community support — it ends **2026-11-30**. If a newer
community-supported series is current, pin that and update the inventory.

```console
./localforge.sh setup
./localforge.sh development-build
./localforge.sh development-up
./localforge.sh development-health
```

The command adapter retains the original idempotent storage gate:
`uv run --project backend --directory backend python scripts/seed_storage.py --environment development --endpoint http://127.0.0.1:8333`.

For a deliberate clean-room verification, remove only LocalForge resources and rebuild local images without cache.
This destroys development data and is not the ordinary restart path.

```console
./localforge.sh development-reset
```

Ordinary source rebuilds preserve named volumes:

```console
./localforge.sh development-rebuild
```

**Gate 5a — everything healthy.**

```console
docker compose --env-file .env.development -f compose.yaml -f compose.development.yaml ps
```

Pass: 22 rows, every `State` `running`, every health-checked service `healthy`, nothing `restarting`.

**Gate 5a-i — the media bucket exists.** Object storage starts empty, so the Django entrypoint creates the bucket
after the gateway is healthy and before migrations or serving. The step is idempotent and reads the container's
process environment. The host command below independently verifies the same contract through the published port.

```console
uv run --project backend --directory backend python scripts/seed_storage.py --environment development --endpoint http://127.0.0.1:8333
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

One at a time, each with its own test in `backend/tests/integration/`. A batched failure is far harder to attribute.

| Step | Integration | Verification | Pass |
| --- | --- | --- | --- |
| 6a | PostgreSQL primary | `docker exec django-uv5n2 python manage.py migrate --check`, then `showmigrations` | exit `0`, nothing unapplied |
| 6b | Replica routing | A test asserting reads use `replica` and writes use `default` | routing correct; `allow_migrate` returns `True` only for `default` |
| 6c | Channels over Valkey | Two authenticated sockets in one user group, plus cross-process admission and delivery checks | one publication reaches both; connection rate is shared |
| 6d | Celery | `docker exec celery-worker-cw8rt celery -A config inspect ping`, then a round-trip task | `pong`, and the task completes |
| 6e | Cache | Write and read through `django.core.cache`; `valkey-cli -n 0 DBSIZE` | value round-trips; DB 0 non-empty and DB 1 untouched |
| 6f | Object storage | Upload through Django's storage API, fetch back from the S3 endpoint | byte-identical |
| 6g | Email | `send_mail`, then `GET http://localhost:8025/api/v1/messages` | Mailpit reports one message with the expected subject |
| 6h | Backup | `pgbackrest --stanza=localforge check`, then `backup --type=full`, then `info` | `check` exits `0`; `info` lists one full backup, status `ok` |
| 6i | Metrics | Query `django_http_requests_before_middlewares_total` through `http://localhost:9090/api/v1/query`; then Prometheus targets | query result is non-empty and `up{job="django"} == 1`; every target at `/targets` is `UP` |
| 6j | Logging | Emit a log line, query Loki through Grafana Explore | retrievable within 30 seconds |
| 6k | Reverse proxy | `curl -H "Host: localforge.localhost" http://localhost:8080/health/` | `200` through Traefik, route visible in the dashboard |
| 6l | Health aggregate | `curl -H "Accept: application/json" http://localhost:8000/health/` | status is `ready`; all seven named dependency checks report `working` |

Three of these — 6c, 6d, 6f — exercise dependencies carrying **release lag**. They are end-to-end on purpose: an
import check would pass while the behaviour is broken. If one fails, apply the escape in
[../adr/0016-accept-release-lag.md](../adr/0016-accept-release-lag.md) for that dependency only, and record the
commit and CI run in that file.

Gate: all twelve pass and `./localforge.sh check` is still green.

### Phase 7 — Testing environment

**7a.** Write `compose.testing.yaml` with the seven services from
[../platform/service-inventory.md](../platform/service-inventory.md) Section 4 and **no dashboard or UI service**.

```console
./localforge.sh testing-reset
./localforge.sh testing-up
./localforge.sh testing-health
```

Environment preparation ends here and runs no application tests. The phase gate then runs the explicit test
commands:

```console
./localforge.sh testing-test-container
./localforge.sh testing-test-host
./localforge.sh testing-registration-timing-stability
./localforge.sh testing-down
```

The testing startup tasks retain the original storage step:
`uv run --project backend --directory backend python scripts/seed_storage.py --environment testing --endpoint http://127.0.0.1:28333`.

The complete host workflow includes five independent production-shaped registration timing passes after the full
suite. Each pass publishes to an isolated RabbitMQ queue without eager task execution or a worker, while separate
Mailpit cases prove delivery. `./localforge.sh testing-verify` remains an explicit full-suite workflow. A failure leaves
the testing services running for diagnosis.

Gate:

+ Pass: both complete tasks exit `0`; each core stage reports 100% branch coverage; each core count plus its
  security-timing count equals the complete collection; host and container totals match; the stack contains
  nothing from the exclusion list. The persistent runner writes no test artifact through a writable repository
  bind; its complete output and exit status are the container evidence.
+ Fail: differing test counts mean environment-dependent skipping, which hides real failures. A host-only failure
  is almost always a `*_HOST` variable in `.env.testing.host` still naming a container instead of `127.0.0.1`.

### Phase 8 — Convention audit

```console
uv run --project backend --directory backend python -m scripts.audit_naming --environment development
uv run --project backend --directory backend python -m scripts.audit_naming --environment testing
```

Then run the five audits in [../platform/service-inventory.md](../platform/service-inventory.md) Section 6 by hand
and compare. **All of them filter by Compose project label** — this machine runs unrelated containers.

Pass: both exit `0`; no anonymous volumes in the project; no `_default` network; every name matches
`^[a-z][a-z-]*-[a-z2-9]{5}$`; every published port matches the inventory.

Fail: fix the Compose file, recreate the affected service, re-run the **full** audit.

### Ticket set handover

The platform is now running, integrated, and audited. **Stop building infrastructure here** and continue from
[.scratch/README.md](../../.scratch/README.md), where ticket phases 4 through 9 deliver the application surface,
the full test and audit passes, the evidence-led SOLID architecture audit, and the public edge and email.

Do not start a route, model, or feature outside the fixed surface listed in [AGENTS.md](../../AGENTS.md).

Ticket 30 proves SimpleJWT's upstream `flushexpiredtokens` command deletes expired outstanding and cascaded
blacklist rows on the authoritative primary while preserving unexpired state. Ticket 43 owns the daily
database-backed scheduler entry, its observed execution, and its logs; the earlier ticket must not start Celery Beat
or claim that retention is operationally bounded before that schedule exists.

Report before handing over: every file created, grouped by Section 2; how each release-lag gate resolved; any
pinned version that had moved since 2026-09-13, with its new release date; the phase 8 audit output; and anything
in `docs/` that turned out to be wrong.

### Phase 9 — Public edge and transactional email

Phase 9 publishes the existing Docker deployment through Cloudflare Tunnel and adds Resend for development email.
The authoritative scope, security boundary, email contract, and rollback sequence live in
[phase-9-public-edge-email.md](../architecture/phase-9-public-edge-email.md) and
[the runbook](../runbooks/phase-9-public-edge-email.md). Work the Phase 9 tickets in `.scratch/` only after the
Phase 8 handover is complete.

Gate: external DNS and TLS reach only `localforge.datarohit.com`; `/health/`, authenticated REST, and authenticated
WebSocket flows work through the Tunnel; development email sends through Resend from
`no-reply@localforge.datarohit.com`; testing still uses Mailpit; operator surfaces and secrets remain private; and
the affected runtime log window is clean.

The prepared operator record is [docs/handover/phase-9.md](../handover/phase-9.md). Phase 9 is closed. No new
implementation scope starts until the Phase 10 monorepo documents and ticket set are accepted.

### Phase 10 — Backend-first monorepo restructure

Phase 10 moves backend Python ownership into `backend/` while keeping environment files, Compose orchestration,
documentation, repository policy, and future frontend ownership global at the root. It creates no frontend and no new
runtime environment. The authoritative scope, ownership table, requirements, and decision are
[phase-10-monorepo.md](../architecture/phase-10-monorepo.md),
[phase-10-monorepo-spec.md](../architecture/phase-10-monorepo-spec.md), and
[ADR 0023](../adr/0023-monorepo-layout.md).

Work tickets 71–78 in `.scratch/phase-10-monorepo/` in order. Ticket 78 closes only after a clean-checkout install,
both environment rebuilds, quality gates, SOPS parity, security audits, and runtime truth evidence pass. The final
record is [docs/handover/phase-10.md](../handover/phase-10.md).

Phase 10 root command contract: run `./localforge.sh <task>` from a POSIX shell or
`./localforge.ps1 <task>` from PowerShell. Each wrapper resolves the repository root, runs from the selected Python
project, and selects `backend/` once its `backend/pyproject.toml` exists. Environment and Compose files remain
addressed from the repository root.

## 4. Rollback

```console
docker compose --env-file .env.development -f compose.yaml -f compose.development.yaml down --volumes --remove-orphans
```

Deletes named volumes, so database contents and Grafana state are lost. Env files live outside Docker and are
untouched, so `gen_secrets.py` need not run again and credential-derived state stays consistent.

**Never run `docker system prune -a` on this machine.** It is shared, and that command would remove images and
volumes belonging to other work.
