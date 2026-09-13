# Conventions

Authoritative for: naming of containers, volumes, and networks; the five-character ID registry; the
environment-variable inventory; secret handling; and the contract of every planned script.

Nothing here has been created. It defines names the executing agent reuses verbatim.

## 1. Naming scheme

### 1.1 Containers and Compose services

Format: `<service-type>-<short-unique-id>`

- `<service-type>` is lowercase and hyphen-separated, and describes the role rather than the vendor version:
  `postgres`, `postgres-replica`, `celery-worker`, `valkey-cache-exporter`.
- `<short-unique-id>` is a fixed five-character lowercase alphanumeric code from the registry in Section 2.
- The Compose service key and the container's `container_name` are **identical**.

Forbidden, without exception: generic names (`db`, `cache`, `web`, `queue`, `proxy`, `app`); numeric or ordinal
suffixes (`postgres-1`, `redis-2`, `worker-01`); regenerated or "refreshed" IDs; Docker-assigned default names.
Every service declares `container_name`.

### 1.2 Volumes

Format: `<owner-container-name>-<content>`, for example `postgres-pg3ka-data`. The volume inherits its owner's ID,
so uniqueness is automatic and every volume traces to exactly one container. Every volume is declared under the
top-level `volumes:` key with an explicit name. **Anonymous volumes are forbidden** — a `VOLUME` inherited from a
base image is either mapped to a named volume or deliberately left unmapped and documented as ephemeral.

### 1.3 Networks

Format: `<zone>-net-<short-unique-id>`, declared under the top-level `networks:` key. **No service relies on the
Compose default network**, and no service omits its `networks:` key.

### 1.4 Compose projects

| Environment | Project name |
|---|---|
| development | `localforge-dev` |
| testing | `localforge-test` |

Set with the top-level `name:` key. The top-level `version:` key is **obsolete** — Compose v5.5.1 warns on it and
always validates against the newest schema.

Container names, volume names, network names, and host ports are disjoint across the two environments, so both
stacks can run at once. That is deliberate: it is what makes "run the test suite while the dev stack is up"
possible.

## 2. The ID registry

### 2.1 The never-regenerate rule

These IDs were assigned once, on 2026-09-13. The executing agent **copies them verbatim**. It does not generate new
ones, renumber, tidy them, or substitute a different code because one looks arbitrary. They are arbitrary by design.
Adding a service means adding a row here first, in a documentation change, before the service is written.

Character set: lowercase `a–z` plus digits `2–9`. Digits `0` and `1` are excluded, which removes the `0`/`o` and
`1`/`l` transcription-ambiguity pairs without dropping the letters — so `lk3ny` and `al6wz` are valid and
unambiguous. The matching validation pattern, used by `scripts/audit_naming.py` and by the audits in
[service-inventory.md](./service-inventory.md) Section 6, is `^[a-z][a-z-]*-[a-z2-9]{5}$`.

Every ID is exactly five characters and unique across both environments: **29 containers and 6 networks, 35 IDs, no
duplicates.**

### 2.2 Development

| Container name | ID | Role | Image |
|---|---|---|---|
| `traefik-tk2jp` | `tk2jp` | reverse proxy | `docker.io/library/traefik:v3.7.13` |
| `django-uv5n2` | `uv5n2` | Django ASGI app | built, `docker/django/Dockerfile` |
| `postgres-pg3ka` | `pg3ka` | PostgreSQL primary | `docker.io/library/postgres:18.6` |
| `postgres-replica-pg6vy` | `pg6vy` | PostgreSQL hot standby | `docker.io/library/postgres:18.6` |
| `pgbackrest-pb2wj` | `pb2wj` | backup agent | built, `docker/pgbackrest/Dockerfile` |
| `pgadmin-pa7fe` | `pa7fe` | PostgreSQL dashboard | `docker.io/dpage/pgadmin4:9.17` |
| `valkey-cache-vc5tn` | `vc5tn` | cache + Celery results | `docker.io/valkey/valkey:9.1.2` |
| `valkey-channels-vh8dm` | `vh8dm` | Channels layer | `docker.io/valkey/valkey:9.1.2` |
| `rabbitmq-rq4sx` | `rq4sx` | Celery broker | `docker.io/library/rabbitmq:4.3.5-management` |
| `celery-worker-cw8rt` | `cw8rt` | task worker | built, `docker/django/Dockerfile` |
| `celery-beat-cb4hq` | `cb4hq` | task scheduler | built, `docker/django/Dockerfile` |
| `flower-fl9zd` | `fl9zd` | Celery dashboard | built, `docker/django/Dockerfile` |
| `mailpit-mp6gb` | `mp6gb` | SMTP capture | `docker.io/axllent/mailpit:v1.31.1` |
| `seaweedfs-sw9cr` | `sw9cr` | S3 object storage | `docker.io/chrislusf/seaweedfs:4.46` |
| `prometheus-pm5db` | `pm5db` | metrics collection | `docker.io/prom/prometheus:v3.14.0` |
| `grafana-gf7qv` | `gf7qv` | visualization | `docker.io/grafana/grafana-oss:13.2.1` |
| `loki-lk3ny` | `lk3ny` | log storage | `docker.io/grafana/loki:3.7.7` |
| `alloy-al6wz` | `al6wz` | log collection | `docker.io/grafana/alloy:v1.19.2` |
| `cadvisor-cv8mh` | `cv8mh` | container metrics | `ghcr.io/google/cadvisor:v0.60.5` |
| `postgres-exporter-pe4rk` | `pe4rk` | PostgreSQL metrics | `quay.io/prometheuscommunity/postgres-exporter:v0.20.1` |
| `valkey-cache-exporter-ve7ts` | `ve7ts` | cache Valkey metrics | `docker.io/oliver006/redis_exporter:v1.91.1` |
| `valkey-channels-exporter-vx4nq` | `vx4nq` | channels Valkey metrics | `docker.io/oliver006/redis_exporter:v1.91.1` |

### 2.3 Testing

Its own IDs, so it can coexist with development. Same never-regenerate rule.

| Container name | ID | Role | Image |
|---|---|---|---|
| `django-test-dt5qx` | `dt5qx` | pytest runner | built, `test` stage |
| `postgres-tp8vn` | `tp8vn` | PostgreSQL, single node | `docker.io/library/postgres:18.6` |
| `valkey-cache-tv4kq` | `tv4kq` | cache | `docker.io/valkey/valkey:9.1.2` |
| `valkey-channels-tv9zw` | `tv9zw` | Channels layer | `docker.io/valkey/valkey:9.1.2` |
| `rabbitmq-tr6mc` | `tr6mc` | Celery broker | `docker.io/library/rabbitmq:4.3.5` |
| `seaweedfs-ts3jd` | `ts3jd` | S3 object storage | `docker.io/chrislusf/seaweedfs:4.46` |
| `mailpit-tm7bh` | `tm7bh` | SMTP capture, profile `smtp` | `docker.io/axllent/mailpit:v1.31.1` |

### 2.4 Networks

| Network | ID | Environment | Purpose | `internal` |
|---|---|---|---|---|
| `edge-net-ne2vk` | `ne2vk` | development | Traefik to the Django app | no |
| `app-net-na6hy` | `na6hy` | development | app tier to brokers, cache, storage, mail | **yes** |
| `data-net-nd9pc` | `nd9pc` | development | PostgreSQL nodes, backup agent, pgAdmin | **yes** |
| `obsv-net-nb4xt` | `nb4xt` | development | Prometheus, Grafana, Loki, Alloy, exporters, cAdvisor | **yes** |
| `app-net-nt5rk` | `nt5rk` | testing | test runner to brokers, cache, storage, mail | **yes** |
| `data-net-nt8fq` | `nt8fq` | testing | test runner to PostgreSQL | **yes** |

Services attach to more than one network where needed. `django-uv5n2` is on all four development networks;
`postgres-exporter-pe4rk` is on `data-net-nd9pc` and `obsv-net-nb4xt`.

`internal: true` on every network except `edge-net-ne2vk` is the enforcement mechanism for the offline constraint:
no container on an internal network can reach the internet even if a dependency tries. It does **not** restrict
traffic between members of that network, and the gateway IP remains reachable.

### 2.5 Volumes

| Volume | Owner | Contents | Environment |
|---|---|---|---|
| `postgres-pg3ka-data` | `postgres-pg3ka` | `PGDATA` | development |
| `postgres-pg3ka-wal` | `postgres-pg3ka` | WAL archive staging for pgBackRest | development |
| `postgres-replica-pg6vy-data` | `postgres-replica-pg6vy` | standby `PGDATA` | development |
| `pgbackrest-pb2wj-repo` | `pgbackrest-pb2wj` | backup repository, full + incremental + WAL | development |
| `pgadmin-pa7fe-data` | `pgadmin-pa7fe` | server list, preferences | development |
| `valkey-cache-vc5tn-data` | `valkey-cache-vc5tn` | RDB snapshot | development |
| `valkey-channels-vh8dm-data` | `valkey-channels-vh8dm` | RDB snapshot | development |
| `rabbitmq-rq4sx-data` | `rabbitmq-rq4sx` | Mnesia, queue metadata | development |
| `mailpit-mp6gb-data` | `mailpit-mp6gb` | captured messages | development |
| `seaweedfs-sw9cr-data` | `seaweedfs-sw9cr` | master, volume, filer data | development |
| `prometheus-pm5db-data` | `prometheus-pm5db` | TSDB | development |
| `grafana-gf7qv-data` | `grafana-gf7qv` | dashboards, users, plugin state | development |
| `loki-lk3ny-data` | `loki-lk3ny` | chunks and index | development |
| `alloy-al6wz-data` | `alloy-al6wz` | positions file / WAL | development |
| `django-uv5n2-static` | `django-uv5n2` | `collectstatic` output | development |
| `postgres-tp8vn-data` | `postgres-tp8vn` | `PGDATA` | testing |
| `valkey-cache-tv4kq-data` | `valkey-cache-tv4kq` | RDB snapshot | testing |
| `valkey-channels-tv9zw-data` | `valkey-channels-tv9zw` | RDB snapshot | testing |
| `rabbitmq-tr6mc-data` | `rabbitmq-tr6mc` | Mnesia, queue metadata | testing |
| `seaweedfs-ts3jd-data` | `seaweedfs-ts3jd` | master, volume, filer data | testing |
| `mailpit-tm7bh-data` | `mailpit-tm7bh` | captured messages | testing |

Testing volumes exist so a restart does not lose state mid-debug. Discard them with
`docker compose --project-name localforge-test down --volumes` between full runs.

## 3. Environment variables

### 3.1 Rules

1. **No value is hardcoded inline in Compose.** This is not style: **`environment:` silently overrides
   `env_file:`**, so a literal left inline wins over the generated file and produces a stack that ignores its own
   configuration. Prefer `env_file:` alone; where `environment:` is unavoidable, it may only pass a variable
   through, as in `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}`.
2. One env file per environment: `.env.development` and `.env.testing`, plus `.env.testing.host` for host mode.
3. No generated `.env` file is committed. `.gitignore` excludes `.env.development`, `.env.testing`, and
   `.env.testing.host`, and explicitly does **not** exclude `.env.example` or the `.sops` files.
4. Secrets come only from `scripts/gen_secrets.py`. The placeholder everywhere in documentation is `<GENERATED>`.
5. Django reads variables through `django-environ`, never `os.environ` directly in settings.
6. A required variable that is missing raises at startup. Silent defaults for required values turn a configuration
   error into a runtime mystery.

### 3.2 Inventory

**Sec** = secret, **Req** = required.

| Variable | Owning service | Purpose | Example | Sec | Req |
|---|---|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `django-uv5n2` | settings module | `config.settings.development` | no | yes |
| `DJANGO_SECRET_KEY` | `django-uv5n2` | signing key | `<GENERATED>` | **yes** | yes |
| `DJANGO_DEBUG` | `django-uv5n2` | debug toggle | `true` dev, `false` testing | no | yes |
| `DJANGO_ALLOWED_HOSTS` | `django-uv5n2` | host header allowlist | `localhost,127.0.0.1,django-uv5n2` | no | yes |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `django-uv5n2` | CSRF origins behind Traefik | `http://localhost:8080` | no | yes |
| `DJANGO_LOG_LEVEL` | `django-uv5n2` | root log level | `INFO` | no | no |
| `UVICORN_WORKERS` | `django-uv5n2` | ASGI worker count | `2` | no | no |
| `POSTGRES_DB` | `postgres-pg3ka` | database name | `localforge` | no | yes |
| `POSTGRES_USER` | `postgres-pg3ka` | superuser role | `localforge_app` | no | yes |
| `POSTGRES_PASSWORD` | `postgres-pg3ka` | superuser password | `<GENERATED>` | **yes** | yes |
| `POSTGRES_HOST` | `django-uv5n2` | primary host | `postgres-pg3ka` | no | yes |
| `POSTGRES_PORT` | `django-uv5n2` | primary port | `5432` | no | yes |
| `POSTGRES_REPLICA_HOST` | `django-uv5n2` | replica host | `postgres-replica-pg6vy` | no | yes |
| `POSTGRES_REPLICA_PORT` | `django-uv5n2` | replica port | `5432` | no | yes |
| `POSTGRES_REPLICATION_USER` | `postgres-pg3ka` | replication role | `localforge_repl` | no | yes |
| `POSTGRES_REPLICATION_PASSWORD` | `postgres-pg3ka` | replication password | `<GENERATED>` | **yes** | yes |
| `POSTGRES_REPLICATION_SLOT` | `postgres-pg3ka` | physical slot name | `localforge_standby` | no | yes |
| `PGBACKREST_STANZA` | `pgbackrest-pb2wj` | stanza name | `localforge` | no | yes |
| `PGBACKREST_FULL_SCHEDULE` | `pgbackrest-pb2wj` | cron, full backup | `0 2 * * 0` | no | yes |
| `PGBACKREST_DIFF_SCHEDULE` | `pgbackrest-pb2wj` | cron, differential | `0 2 * * 1-6` | no | yes |
| `PGBACKREST_RETENTION_FULL` | `pgbackrest-pb2wj` | full backups retained | `2` | no | yes |
| `PGADMIN_DEFAULT_EMAIL` | `pgadmin-pa7fe` | dashboard login | `dev@localforge.invalid` | no | yes |
| `PGADMIN_DEFAULT_PASSWORD` | `pgadmin-pa7fe` | dashboard password | `<GENERATED>` | **yes** | yes |
| `PGADMIN_LISTEN_ADDRESS` | `pgadmin-pa7fe` | bind address | `0.0.0.0` | no | yes |
| `PGADMIN_DISABLE_POSTFIX` | `pgadmin-pa7fe` | skip the bundled mail server | `True` | no | yes |
| `PGADMIN_REPLACE_SERVERS_ON_STARTUP` | `pgadmin-pa7fe` | declarative server list | `True` | no | yes |
| `VALKEY_CACHE_HOST` | `django-uv5n2` | cache host | `valkey-cache-vc5tn` | no | yes |
| `VALKEY_CACHE_PORT` | `django-uv5n2` | cache port | `6379` | no | yes |
| `VALKEY_CACHE_PASSWORD` | `valkey-cache-vc5tn` | `requirepass` | `<GENERATED>` | **yes** | yes |
| `VALKEY_CACHE_DB` | `django-uv5n2` | cache DB index | `0` | no | yes |
| `VALKEY_RESULTS_DB` | `celery-worker-cw8rt` | Celery result DB index | `1` | no | yes |
| `VALKEY_CACHE_MAXMEMORY` | `valkey-cache-vc5tn` | eviction ceiling | `256mb` | no | yes |
| `VALKEY_CHANNELS_HOST` | `django-uv5n2` | Channels host | `valkey-channels-vh8dm` | no | yes |
| `VALKEY_CHANNELS_PORT` | `django-uv5n2` | Channels port | `6379` | no | yes |
| `VALKEY_CHANNELS_PASSWORD` | `valkey-channels-vh8dm` | `requirepass` | `<GENERATED>` | **yes** | yes |
| `RABBITMQ_HOST` | `celery-worker-cw8rt` | broker host | `rabbitmq-rq4sx` | no | yes |
| `RABBITMQ_PORT` | `celery-worker-cw8rt` | broker AMQP port | `5672` | no | yes |
| `RABBITMQ_DEFAULT_USER` | `rabbitmq-rq4sx` | broker user | `localforge_broker` | no | yes |
| `RABBITMQ_DEFAULT_PASS` | `rabbitmq-rq4sx` | broker password | `<GENERATED>` | **yes** | yes |
| `RABBITMQ_DEFAULT_VHOST` | `rabbitmq-rq4sx` | broker vhost | `localforge` | no | yes |
| `CELERY_BROKER_URL` | `celery-worker-cw8rt` | AMQP URL | composed from the five above | **yes** | yes |
| `CELERY_RESULT_BACKEND` | `celery-worker-cw8rt` | result store, DB 1 | composed | **yes** | yes |
| `CELERY_TASK_ALWAYS_EAGER` | `django-test-dt5qx` | run tasks inline | `false` dev, `true` testing | no | no |
| `FLOWER_BASIC_AUTH` | `flower-fl9zd` | dashboard credentials | `<GENERATED>` | **yes** | yes |
| `EMAIL_BACKEND` | `django-uv5n2` | Django email backend | `django.core.mail.backends.smtp.EmailBackend` | no | yes |
| `EMAIL_HOST` | `django-uv5n2` | SMTP host | `mailpit-mp6gb` | no | yes |
| `EMAIL_PORT` | `django-uv5n2` | SMTP port | `1025` | no | yes |
| `DEFAULT_FROM_EMAIL` | `django-uv5n2` | envelope sender | `no-reply@localforge.invalid` | no | yes |
| `S3_ENDPOINT_URL` | `django-uv5n2` | SeaweedFS S3 gateway | `http://seaweedfs-sw9cr:8333` | no | yes |
| `S3_ACCESS_KEY_ID` | `seaweedfs-sw9cr` | S3 access key | `<GENERATED>` | **yes** | yes |
| `S3_SECRET_ACCESS_KEY` | `seaweedfs-sw9cr` | S3 secret key | `<GENERATED>` | **yes** | yes |
| `S3_BUCKET_NAME` | `django-uv5n2` | media bucket | `localforge-media` | no | yes |
| `S3_REGION_NAME` | `django-uv5n2` | region string the SDK requires | `us-east-1` | no | yes |
| `GRAFANA_ADMIN_USER` | `grafana-gf7qv` | dashboard login | `admin` | no | yes |
| `GRAFANA_ADMIN_PASSWORD` | `grafana-gf7qv` | dashboard password | `<GENERATED>` | **yes** | yes |
| `PROMETHEUS_RETENTION_TIME` | `prometheus-pm5db` | TSDB retention | `7d` | no | no |
| `LOKI_RETENTION_PERIOD` | `loki-lk3ny` | log retention | `168h` | no | no |
| `TRAEFIK_DASHBOARD_AUTH` | `traefik-tk2jp` | basic-auth hash | `<GENERATED>` | **yes** | yes |
| `COMPOSE_PROJECT_NAME` | Compose | project namespace | `localforge-dev` | no | yes |

`VALKEY_CACHE_DB` and `VALKEY_RESULTS_DB` must differ. Django's `RedisCache.clear()` issues `FLUSHDB`, which wipes
the whole logical database — sharing an index means a routine `cache.clear()` destroys every pending Celery result.
See [../adr/0005-valkey-cache.md](../adr/0005-valkey-cache.md).

Testing overrides, present only in `.env.testing`:

| Variable | Value | Reason |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `localforge-test` | selects the testing project namespace |
| `DJANGO_SETTINGS_MODULE` | `config.settings.testing` | selects the testing module |
| `DJANGO_DEBUG` | `false` | tests must not depend on debug behaviour |
| `DJANGO_ALLOWED_HOSTS` | replaces `django-uv5n2` with `django-test-dt5qx` | the test runner's own container name; the development app does not run here |
| `POSTGRES_HOST`, `POSTGRES_REPLICA_HOST` | `postgres-tp8vn` | single node; the replica alias points at it |
| `VALKEY_CACHE_HOST` | `valkey-cache-tv4kq` | the testing cache container |
| `VALKEY_CHANNELS_HOST` | `valkey-channels-tv9zw` | the testing channel-layer container |
| `RABBITMQ_HOST` | `rabbitmq-tr6mc` | the testing broker container |
| `S3_ENDPOINT_URL` | `http://seaweedfs-ts3jd:8333` | the testing storage container |
| `EMAIL_HOST` | `mailpit-tm7bh` | the testing mail container, profile `smtp` |
| `EMAIL_BACKEND` | `django.core.mail.backends.locmem.EmailBackend` | default; SMTP only under the `smtp` profile |
| `CELERY_TASK_ALWAYS_EAGER` | `true` | default; the broker integration test overrides it |

Every `*_HOST` override follows from the testing registry in Section 2.3: the testing stack runs its own
containers, so a host left naming a development container would resolve to nothing on the testing networks.

`.env.testing.host` is the same file with every `*_HOST` set to `127.0.0.1` and every port set to the published host
port from [service-inventory.md](./service-inventory.md) Section 4.

## 4. Planned scripts

Each is a **separate file**. Nothing here is inlined into a Compose file, a Dockerfile `RUN`, or a settings module.
None exists yet.

Implementation language is Python, run as `uv run python scripts/<name>.py`, except where a script runs inside an
image with no Python. The development machine is Windows, so a `.sh` entrypoint would not run on the host.

### 4.1 `scripts/gen_secrets.py`

| Property | Value |
|---|---|
| Responsibility | Create or top up `.env.development`, `.env.testing`, `.env.testing.host` |
| Inputs | `--environment {development,testing,all}`, `--force`; `.env.example` is the variable manifest |
| Generation | `secrets.token_urlsafe(64)` for `DJANGO_SECRET_KEY`; `token_urlsafe(32)` for passwords; `token_hex(20)` for S3 keys; bcrypt at cost 12 for `TRAEFIK_DASHBOARD_AUTH`. `FLOWER_BASIC_AUTH` is a **plaintext** `user:password` pair, because Flower compares its configured value literally — hashing it would make the digest itself the password |
| Quoting | A value containing `$` is written single-quoted. Compose expands unquoted values in **both** `env_file:` and `--env-file`, so a bare bcrypt hash loses everything from its third `$` onward and yields a credential that cannot authenticate. Verified against Compose v5.5.1 on 2026-09-13 |
| Idempotency | Default run **never overwrites an existing value**, and never discards one it does not recognise; it appends only absent variables, so adding an inventory row fills the gap without invalidating a running stack. A composed value is the exception: it is re-derived whenever the variables it is built from change, because a URL that disagrees with the password beside it is worse than no URL. `--force` regenerates everything and warns that credential-derived volumes must be recreated |
| Sharing | `.env.testing` and `.env.testing.host` address the same containers and therefore hold the same credentials. They are resolved together, so a run that regenerates one because the other is missing cannot leave the pair disagreeing |
| Exit codes | `0` ok; `2` `.env.example` missing or unparsable; `3` refused to write a Git-tracked file, **or could not determine whether a file is tracked**; `4` `--force` without `--environment` |
| Never | Prints a secret, logs a value, or writes into a `.sops` file |

### 4.2 `scripts/preflight.py`

Runs the checklist in [../build/prerequisites.md](../build/prerequisites.md) and prints a pass/fail table; `--json`
for machine output. Exit `0` when every required check passes, `1` otherwise, with optional-check failures printed
as warnings.

### 4.3 `scripts/sops_env.py`

`--mode {encrypt,decrypt} --environment <name>`, age recipient from `.sops.yaml`. Exit `0` ok; `1` the operation
failed — a missing source file, or `sops` itself refusing; `2` `sops` or `age` not on `PATH`; `3` no age key; `4`
decrypt would overwrite newer plaintext without `--force`.

`1` is deliberately separate from `3`. A missing age key is a one-time setup problem with a known remedy, while a
`sops` failure is anything else, and collapsing the two would tell an operator to generate a key they already have.
The tool's own error text is not reproduced in the output: it is written against a file of credentials, and nothing
guarantees a future version will not quote the line it failed on.

### 4.4 `scripts/wait_for_services.py`

Blocks until each named dependency answers a real readiness probe, not a TCP connect. `--timeout`, default 120.
Probes: PostgreSQL `SELECT 1`; Valkey `PING`; RabbitMQ AMQP handshake; SeaweedFS `GET /healthz`; Mailpit
`GET /readyz`. Exit `0` all ready; `1` timeout, naming the service and last error.

### 4.5 `scripts/pg_replica_bootstrap.sh`

Waits for the primary, runs `pg_basebackup -R -X stream --slot="$POSTGRES_REPLICATION_SLOT"` into an empty `PGDATA`,
then execs the normal Postgres entrypoint. Skips the base backup when `PGDATA/PG_VERSION` already exists. Exit `0`
started; `1` primary unreachable; `2` `PGDATA` non-empty but not a valid standby.

Shell, because it runs inside `postgres:18.6`, which has no Python.

### 4.6 `scripts/pgbackrest_entrypoint.sh`

Creates the stanza if `pgbackrest info` does not already report it, then runs the backup schedule loop. Exit `0`
clean shutdown; `1` stanza creation failed; `2` a scheduled backup failed. Shell, same reason.

### 4.7 `scripts/seed_storage.py`

Creates the S3 bucket and applies the access-key identity in SeaweedFS. Creating an existing bucket is success. Exit
`0` bucket present; `1` gateway unreachable; `2` credentials rejected.

### 4.8 `scripts/audit_naming.py`

Phase 8. Compares live Docker objects against Section 2, **scoped by the Compose project label** so unrelated
containers on this shared machine are ignored. Checks: every expected container exists and no unexpected one does;
every name matches `^[a-z][a-z-]*-[a-z2-9]{5}$`; no anonymous volumes in the project; no `_default` network; every
published port matches [service-inventory.md](./service-inventory.md). Exit `0` clean; `1` violations, printed as
`FAIL <check> <object> <detail>`.

### 4.9 `scripts/run_tests.py`

`--mode {container,host,both}`. Exit `0` both pass; `1` container failed; `2` host failed; `3` both failed.

## 5. Secret handling

1. No real secret appears in `docs/`, `AGENTS.md`, `CONTEXT.md`, a Compose file, a Dockerfile, or a settings module.
   The only placeholder is `<GENERATED>`.
2. Committed artifacts are `.env.example` (placeholders only) and the age-encrypted `.env.*.sops` files.
3. `detect-private-key` is an active pre-commit hook. Do not disable it or add exclusions.
4. Secrets are generated on the machine that runs the platform and never copied between machines in plaintext.
5. On suspected exposure: `gen_secrets.py --force`, then recreate every volume whose contents derive from the old
   value. The set depends on the environment being regenerated:

   | Environment | Volumes to recreate |
   |---|---|
   | development | `postgres-pg3ka-data`, `postgres-replica-pg6vy-data`, `rabbitmq-rq4sx-data`, `grafana-gf7qv-data`, `pgadmin-pa7fe-data` |
   | testing | `postgres-tp8vn-data`, `rabbitmq-tr6mc-data` |

   The standby is listed because it holds a copy initialised with the old replication credential, so leaving it in
   place after re-seeding the primary produces a standby that cannot reconnect.
6. Every credential is distinct. No password is reused across services, which is why `VALKEY_CACHE_PASSWORD` and
   `VALKEY_CHANNELS_PASSWORD` are separate even though both run the same image — and why the platform runs two
   `redis_exporter` instances rather than one. See [service-inventory.md](./service-inventory.md) Section 1.1.
