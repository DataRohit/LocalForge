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
| --- | --- |
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

Every ID is exactly five characters and unique across both environments: **30 containers and 8 networks, 38 IDs, no
duplicates.** The additional development container is the Phase 10 Cloudflare Tunnel client; it reuses the existing
public edge network and does not create a third environment.

### 2.2 Development

| Container name | ID | Role | Image |
| --- | --- | --- | --- |
| `traefik-tk2jp` | `tk2jp` | reverse proxy | `docker.io/library/traefik:v3.7.13` |
| `cloudflared-cf7q2` | `cf7q2` | Cloudflare Tunnel public edge client | pinned Cloudflare cloudflared release, selected by Ticket 66 |
| `django-uv5n2` | `uv5n2` | Django ASGI app | built, `docker/django/Dockerfile` |
| `postgres-pg3ka` | `pg3ka` | PostgreSQL primary | built, `docker/pgbackrest/Dockerfile` |
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
| `grafana-gf7qv` | `gf7qv` | visualization | `docker.io/grafana/grafana-oss:13.0.2` |
| `loki-lk3ny` | `lk3ny` | log storage | `docker.io/grafana/loki:3.7.7` |
| `alloy-al6wz` | `al6wz` | log collection | `docker.io/grafana/alloy:v1.19.2` |
| `cadvisor-cv8mh` | `cv8mh` | container metrics | `ghcr.io/google/cadvisor:v0.60.5` |
| `postgres-exporter-pe4rk` | `pe4rk` | PostgreSQL metrics | `quay.io/prometheuscommunity/postgres-exporter:v0.20.1` |
| `valkey-cache-exporter-ve7ts` | `ve7ts` | cache Valkey metrics | `docker.io/oliver006/redis_exporter:v1.91.1` |
| `valkey-channels-exporter-vx4nq` | `vx4nq` | channels Valkey metrics | `docker.io/oliver006/redis_exporter:v1.91.1` |

### 2.3 Testing

Its own IDs, so it can coexist with development. Same never-regenerate rule.

| Container name | ID | Role | Image |
| --- | --- | --- | --- |
| `django-test-dt5qx` | `dt5qx` | pytest runner | built, `test` stage |
| `postgres-tp8vn` | `tp8vn` | PostgreSQL, single node | `docker.io/library/postgres:18.6` |
| `valkey-cache-tv4kq` | `tv4kq` | cache | `docker.io/valkey/valkey:9.1.2` |
| `valkey-channels-tv9zw` | `tv9zw` | Channels layer | `docker.io/valkey/valkey:9.1.2` |
| `rabbitmq-tr6mc` | `tr6mc` | Celery broker | `docker.io/library/rabbitmq:4.3.5` |
| `seaweedfs-ts3jd` | `ts3jd` | S3 object storage | `docker.io/chrislusf/seaweedfs:4.46` |
| `mailpit-tm7bh` | `tm7bh` | SMTP capture, profile `smtp` | `docker.io/axllent/mailpit:v1.31.1` |

### 2.4 Networks

| Network | ID | Environment | Purpose | `internal` |
| --- | --- | --- | --- | --- |
| `edge-net-ne2vk` | `ne2vk` | development | Traefik to the Django app, subnet `10.89.2.0/24` | no |
| `access-net-ha4mz` | `ha4mz` | development | host access for every service publishing a port | no |
| `app-net-na6hy` | `na6hy` | development | app tier to brokers, cache, storage, mail | **yes** |
| `data-net-nd9pc` | `nd9pc` | development | PostgreSQL nodes, backup agent, pgAdmin | **yes** |
| `obsv-net-nb4xt` | `nb4xt` | development | Prometheus, Grafana, Loki, Alloy, exporters, cAdvisor | **yes** |
| `access-net-ht6pn` | `ht6pn` | testing | host access for host-mode testing | no |
| `app-net-nt5rk` | `nt5rk` | testing | test runner to brokers, cache, storage, mail | **yes** |
| `data-net-nt8fq` | `nt8fq` | testing | test runner to PostgreSQL | **yes** |

Services attach to more than one network where needed. `django-uv5n2` is on all four development service zones;
`celery-worker-cw8rt` also joins `edge-net-ne2vk` so its development-only Resend SMTP delivery has approved egress;
`postgres-exporter-pe4rk` is on `data-net-nd9pc` and `obsv-net-nb4xt`.

`internal: true` on a service zone is the enforcement mechanism for the offline constraint: no container whose
networks are all internal can reach the internet even if a dependency tries. It does **not** restrict traffic
between members of that network.

`edge-net-ne2vk` has the fixed `10.89.2.0/24` subnet so Django can trust forwarded client addresses only from an
immediate peer on the proxy network. The Cloudflare Tunnel client joins this network and reaches only Traefik's web
entrypoint; it has no access-zone, application, data, or observability membership. No private-address wildcard is
accepted. The direct application publication is `127.0.0.1:8000:8000`, so host diagnostics remain available without
exposing a remotely reachable proxy bypass.

**A published host port does not work on an internal network.** Docker drops the publication silently — no warning,
no error, no non-zero exit — so the container runs healthily while the port is unreachable. Every service the
inventory gives a host port therefore also joins its environment's access zone, which is the only reason those two
zones exist. The inverse is enforced: a service without a host publication must not join an access zone, because
doing so restores internet egress despite its internal service network. Measured 2026-09-13 and re-audited
2026-09-22; see
[../adr/0021-access-zone-for-published-ports.md](../adr/0021-access-zone-for-published-ports.md).

### 2.5 Volumes

| Volume | Owner | Contents | Environment |
| --- | --- | --- | --- |
| `postgres-pg3ka-data` | `postgres-pg3ka` | `PGDATA`, mounted at `/var/lib/postgresql` | development |
| `postgres-pg3ka-socket` | `postgres-pg3ka` | Unix socket the backup agent connects through | development |
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
| --- | --- | --- | --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | `django-uv5n2` | settings module | `config.settings.development` | no | yes |
| `DJANGO_SECRET_KEY` | `django-uv5n2` | signing key | `<GENERATED>` | **yes** | yes |
| `DJANGO_JWT_SIGNING_KEY` | `django-uv5n2` | dedicated HS256 JSON web token signing key, distinct from `DJANGO_SECRET_KEY` | `<GENERATED>` | **yes** | yes |
| `DJANGO_API_THROTTLE_IDENTITY_HMAC_KEY` | `django-uv5n2` | dedicated domain-separated HMAC-SHA-256 key for opaque throttle identities, encoded as unpadded URL-safe Base64 and distinct from Django and JSON web token signing keys | `<GENERATED>` | **yes** | yes |
| `DJANGO_DEBUG` | `django-uv5n2` | debug toggle; generated public development profile is `false` | `false` generated default | no | yes |
| `DJANGO_ALLOWED_HOSTS` | `django-uv5n2` | host header allowlist | `localhost,127.0.0.1,localforge.localhost,localforge.datarohit.com,django-uv5n2,django-metrics-nb4xt` | no | yes |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `django-uv5n2` | CSRF origins behind Traefik | `http://localhost:8080,http://localforge.localhost:8080,https://localforge.datarohit.com` | no | yes |
| `DJANGO_CORS_ALLOWED_ORIGINS` | `django-uv5n2` | exact browser origins allowed to read credentialed cross-origin responses | `http://localhost:8080,http://localforge.localhost:8080,https://localforge.datarohit.com` | no | yes |
| `DJANGO_CORS_ALLOW_CREDENTIALS` | `django-uv5n2` | permit credentials only with exact configured origins; wildcard origins are rejected at startup | `true` | no | yes |
| `DJANGO_API_DOCUMENTATION_ENABLED` | `django-uv5n2` | expose the OpenAPI schema, Swagger UI, and ReDoc routes at process startup | `true` development, `false` testing | no | yes |
| `DJANGO_TRUSTED_PROXY_NETWORKS` | `django-uv5n2` | immediate-peer networks allowed to supply forwarded client addresses | `10.89.2.0/24` development, `none` testing | no | yes |
| `DJANGO_API_REQUEST_BODY_MAX_BYTES` | `django-uv5n2` | versioned API body ceiling and request spool memory limit | `1048576` | no | yes |
| `DJANGO_API_AUTHENTICATION_THROTTLE_RATE` | `django-uv5n2` | shared cache-backed aggregate scope for authentication, recovery, and account-security operations | `30/minute` | no | yes |
| `DJANGO_API_AUTHENTICATED_READ_THROTTLE_RATE` | `django-uv5n2` | shared cache-backed account and address-account composite scope for authenticated reads | `120/minute` | no | yes |
| `DJANGO_API_ANONYMOUS_THROTTLE_RATE` | `django-uv5n2` | shared cache-backed address scope for anonymous API use | `60/minute` | no | yes |
| `DJANGO_API_BOUNDARY_ADDRESS_THROTTLE_RATE` | `django-uv5n2` | broad pre-framework source scope for every versioned API request | `600/minute` | no | yes |
| `DJANGO_WEBSOCKET_APPLICATION_MAX_MESSAGE_BYTES` | `django-uv5n2` | exact complete-message ceiling enforced by the notification consumer before frame decoding | `65536` | no | yes |
| `DJANGO_WEBSOCKET_CONNECTION_THROTTLE_RATE` | `django-uv5n2` | shared authenticated-user WebSocket connection admissions per epoch-aligned fixed window | `30/minute` | no | yes |
| `DJANGO_WEBSOCKET_CONNECTION_ADMISSION_TIMEOUT_SECONDS` | `django-uv5n2` | maximum wait for the authoritative shared connection-admission decision | `5` | no | yes |
| `DJANGO_JWT_ACCESS_TOKEN_LIFETIME_SECONDS` | `django-uv5n2` | JSON web token access lifetime | `300` | no | yes |
| `DJANGO_JWT_REFRESH_TOKEN_LIFETIME_SECONDS` | `django-uv5n2` | JSON web token refresh lifetime, longer than access | `86400` | no | yes |
| `DJANGO_TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE` | `django-uv5n2` | strict atomic token-login admissions per primary-resolved account identity in one rolling window | `5/minute` | no | yes |
| `DJANGO_TOKEN_LOGIN_ADDRESS_THROTTLE_RATE` | `django-uv5n2` | strict atomic token-login admissions per client address in one rolling window | `30/minute` | no | yes |
| `DJANGO_USER_REGISTRATION_ADDRESS_THROTTLE_RATE` | `django-uv5n2` | strict atomic account-registration admissions per client address in one rolling window | `5/minute` | no | yes |
| `DJANGO_USER_REGISTRATION_MINIMUM_RESPONSE_DURATION_SECONDS` | `django-uv5n2` | monotonic public response floor applied only to completed indistinguishable registration outcomes | `0.200` | no | yes |
| `DJANGO_ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS` | `django-uv5n2` | maximum age of one signed account-activation token | `86400` | no | yes |
| `DJANGO_ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE` | `django-uv5n2` | strict atomic activation-resend admissions per client address | `30/hour` | no | yes |
| `DJANGO_ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE` | `django-uv5n2` | shared strict atomic activation-resend rate for the stable normalized-email and optional immutable-account dimensions | `3/hour` | no | yes |
| `DJANGO_PASSWORD_RESET_TOKEN_LIFETIME_SECONDS` | `django-uv5n2` | maximum age of one account-bound password-reset token | `86400` | no | yes |
| `DJANGO_PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS` | `django-uv5n2` | monotonic response floor for accepted known and unknown reset requests | `0.200` | no | yes |
| `DJANGO_PASSWORD_RESET_ADDRESS_THROTTLE_RATE` | `django-uv5n2` | strict atomic password-reset admissions per client address on each reset route | `30/hour` | no | yes |
| `DJANGO_PASSWORD_RESET_ACCOUNT_THROTTLE_RATE` | `django-uv5n2` | strict atomic reset rate for request email/account and confirmation account dimensions | `3/hour` | no | yes |
| `DJANGO_USERNAME_RESET_TOKEN_LIFETIME_SECONDS` | `django-uv5n2` | maximum age of one account-bound username-reset token | `86400` | no | yes |
| `DJANGO_USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS` | `django-uv5n2` | monotonic response floor for accepted known and unknown username-reset requests | `0.200` | no | yes |
| `DJANGO_USERNAME_RESET_ADDRESS_THROTTLE_RATE` | `django-uv5n2` | strict atomic username-reset admissions per client address on each reset route | `30/hour` | no | yes |
| `DJANGO_USERNAME_RESET_ACCOUNT_THROTTLE_RATE` | `django-uv5n2` | strict atomic username-reset rate for request email/account and confirmation account dimensions | `3/hour` | no | yes |
| `DJANGO_LOG_LEVEL` | `django-uv5n2` | root log level | `INFO` | no | no |
| `DJANGO_TIME_ZONE` | `django-uv5n2` | application timezone, stored datetimes stay UTC-aware | `UTC` | no | no |
| `LOCALFORGE_WAIT_SERVICES` | `django-uv5n2` | services the entrypoint waits for before migrating, space-separated. Deliberately outside a vendor prefix, like the backup schedules | `postgres valkey-cache` | no | no |
| `LOCALFORGE_WAIT_TIMEOUT` | `django-uv5n2` | seconds the entrypoint waits before giving up on a dependency | `120` | no | no |
| `UVICORN_WORKERS` | `django-uv5n2` | ASGI worker count | `2` | no | no |
| `UVICORN_WEBSOCKET_MAX_SIZE_BYTES` | `django-uv5n2` | transport-level assembled WebSocket message ceiling, larger than the application contract | `131072` | no | yes |
| `POSTGRES_DB` | `postgres-pg3ka` | database name | `localforge` | no | yes |
| `POSTGRES_USER` | `postgres-pg3ka` | superuser role | `localforge_app` | no | yes |
| `POSTGRES_PASSWORD` | `postgres-pg3ka` | superuser password | `<GENERATED>` | **yes** | yes |
| `POSTGRES_HOST` | `django-uv5n2` | primary host | `postgres-pg3ka` | no | yes |
| `POSTGRES_PORT` | `django-uv5n2` | primary port | `5432` | no | yes |
| `POSTGRES_REPLICA_HOST` | `django-uv5n2` | replica host | `postgres-replica-pg6vy` | no | yes |
| `POSTGRES_REPLICA_PORT` | `django-uv5n2` | replica port | `5432` | no | yes |
| `POSTGRES_REPLICATION_ALIAS` | `postgres-replica-pg6vy` | primary alias resolving on the data zone alone | `postgres-primary-nd9pc` | no | yes |
| `POSTGRES_REPLICATION_CIDR` | `postgres-pg3ka` | source range the replication rule accepts | `10.89.4.0/24` | no | yes |
| `POSTGRES_REPLICATION_USER` | `postgres-pg3ka` | replication role | `localforge_repl` | no | yes |
| `POSTGRES_REPLICATION_PASSWORD` | `postgres-pg3ka` | replication password | `<GENERATED>` | **yes** | yes |
| `POSTGRES_REPLICATION_SLOT` | `postgres-pg3ka` | physical slot name | `localforge_standby` | no | yes |
| `PGBACKREST_STANZA` | `pgbackrest-pb2wj`, `postgres-pg3ka` | stanza name | `localforge` | no | yes |
| `LOCALFORGE_BACKUP_FULL_SCHEDULE` | `pgbackrest-pb2wj` | cron, full backup. Deliberately outside the `PGBACKREST_` prefix, which the tool claims entirely | `0 2 * * 0` | no | yes |
| `LOCALFORGE_BACKUP_DIFF_SCHEDULE` | `pgbackrest-pb2wj` | cron, differential | `0 2 * * 1-6` | no | yes |
| `LOCALFORGE_BACKUP_WAIT_SECONDS` | `pgbackrest-pb2wj` | seconds to wait for the primary before giving up | `180` | no | no |
| `PGBACKREST_REPO1_RETENTION_FULL` | `pgbackrest-pb2wj` | full backups retained | `2` | no | yes |
| `PGBACKREST_REPO1_RETENTION_DIFF` | `pgbackrest-pb2wj` | differential backups retained | `6` | no | yes |
| `PGBACKREST_ARCHIVE_TIMEOUT` | `pgbackrest-pb2wj`, `postgres-pg3ka` | seconds a WAL segment may take to reach the repository | `120` | no | yes |
| `PGADMIN_DEFAULT_EMAIL` | `pgadmin-pa7fe` | dashboard login | `dev@localforge.invalid` | no | yes |
| `PGADMIN_DEFAULT_PASSWORD` | `pgadmin-pa7fe` | dashboard password | `<GENERATED>` | **yes** | yes |
| `PGADMIN_LISTEN_ADDRESS` | `pgadmin-pa7fe` | bind address | `0.0.0.0` | no | yes |
| `PGADMIN_DISABLE_POSTFIX` | `pgadmin-pa7fe` | skip the bundled mail server | `True` | no | yes |
| `PGADMIN_REPLACE_SERVERS_ON_STARTUP` | `pgadmin-pa7fe` | declarative server list | `True` | no | yes |
| `PGADMIN_SERVER_JSON_FILE` | `pgadmin-pa7fe` | mounted server definitions | `/pgadmin4/servers.json` | no | no |
| `PGADMIN_CONFIG_ALLOW_SPECIAL_EMAIL_DOMAINS` | `pgadmin-pa7fe` | permit the reserved login domain | `["invalid"]` | no | yes |
| `PGADMIN_CONFIG_UPGRADE_CHECK_ENABLED` | `pgadmin-pa7fe` | stop the upgrade check reaching the vendor | `False` | no | no |
| `VALKEY_CACHE_HOST` | `django-uv5n2` | cache host | `valkey-cache-vc5tn` | no | yes |
| `VALKEY_CACHE_PORT` | `django-uv5n2` | cache port | `6379` | no | yes |
| `VALKEY_CACHE_PASSWORD` | `valkey-cache-vc5tn` | `requirepass` | `<GENERATED>` | **yes** | yes |
| `VALKEY_CACHE_DB` | `django-uv5n2` | cache DB index | `0` | no | yes |
| `VALKEY_RESULTS_DB` | `celery-worker-cw8rt` | Celery result DB index | `1` | no | yes |
| `VALKEY_CACHE_MAXMEMORY` | `valkey-cache-vc5tn` | eviction ceiling | `256mb` | no | yes |
| `VALKEY_CACHE_MAXMEMORY_POLICY` | `valkey-cache-vc5tn` | eviction policy once the ceiling is reached | `allkeys-lru` | no | yes |
| `VALKEY_CHANNELS_HOST` | `django-uv5n2` | Channels host | `valkey-channels-vh8dm` | no | yes |
| `VALKEY_CHANNELS_PORT` | `django-uv5n2` | Channels port | `6379` | no | yes |
| `VALKEY_CHANNELS_PASSWORD` | `valkey-channels-vh8dm` | `requirepass` | `<GENERATED>` | **yes** | yes |
| `RABBITMQ_HOST` | `celery-worker-cw8rt` | broker host | `rabbitmq-rq4sx` | no | yes |
| `RABBITMQ_PORT` | `celery-worker-cw8rt` | broker AMQP port | `5672` | no | yes |
| `RABBITMQ_MANAGEMENT_PORT` | `flower-fl9zd` | broker management API port | `15672` | no | yes |
| `RABBITMQ_DEFAULT_USER` | `rabbitmq-rq4sx` | broker user | `localforge_broker` | no | yes |
| `RABBITMQ_DEFAULT_PASS` | `rabbitmq-rq4sx` | broker password | `<GENERATED>` | **yes** | yes |
| `RABBITMQ_DEFAULT_VHOST` | `rabbitmq-rq4sx` | broker vhost | `localforge` | no | yes |
| `CELERY_BROKER_URL` | `celery-worker-cw8rt` | AMQP URL | composed from the five above | **yes** | yes |
| `CELERY_RESULT_BACKEND` | `celery-worker-cw8rt` | result store, DB 1 | composed | **yes** | yes |
| `CELERY_TASK_ALWAYS_EAGER` | `django-test-dt5qx` | run tasks inline | `false` dev, `true` testing | no | no |
| `CELERY_WORKER_CONCURRENCY` | `celery-worker-cw8rt` | worker process concurrency | `2` | no | yes |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | `celery-worker-cw8rt` | tasks reserved per worker process | `1` | no | yes |
| `CELERY_WORKER_SHUTDOWN_TIMEOUT_SECONDS` | `celery-worker-cw8rt` | bounded grace period for in-flight tasks during stop | `300` | no | yes |
| `CELERY_WORKER_HEALTH_TIMEOUT_SECONDS` | `celery-worker-cw8rt` | transient worker-reply timeout, greater than the two-second hard task limit | `10` | no | yes |
| `CELERY_BEAT_MAX_LOOP_INTERVAL_SECONDS` | `celery-beat-cb4hq` | maximum delay before the database scheduler checks for edits | `5` | no | yes |
| `CELERY_TOMBSTONE_CLEANUP_BATCH_SIZE` | `celery-beat-cb4hq` | maximum expired rows removed from each account-token table per run | `100` | no | yes |
| `CELERY_TOMBSTONE_CLEANUP_INTERVAL_SECONDS` | `celery-beat-cb4hq` | persisted account-token cleanup interval and message expiry | `300` | no | yes |
| `CELERY_JWT_CLEANUP_HOUR` | `celery-beat-cb4hq` | local-time hour for daily expired JWT cleanup | `0` | no | yes |
| `CELERY_JWT_CLEANUP_MINUTE` | `celery-beat-cb4hq` | local-time minute for daily expired JWT cleanup | `0` | no | yes |
| `CELERY_JWT_CLEANUP_EXPIRY_SECONDS` | `celery-beat-cb4hq` | maximum age of a queued daily JWT cleanup invocation | `3600` | no | yes |
| `FLOWER_BASIC_AUTH` | `flower-fl9zd` | dashboard credentials | `<GENERATED>` | **yes** | yes |
| `FLOWER_BROKER_API` | `flower-fl9zd` | authenticated RabbitMQ management API URL for queue depth | composed | **yes** | yes |
| `EMAIL_BACKEND` | `django-uv5n2` | Django email backend | `django.core.mail.backends.smtp.EmailBackend` | no | yes |
| `EMAIL_HOST` | `django-uv5n2` | development SMTP host | `smtp.resend.com` (Mailpit in testing) | no | yes |
| `EMAIL_PORT` | `django-uv5n2` | development SMTP port | `587` (Mailpit `1025` in testing) | no | yes |
| `MAILPIT_WEB_PORT` | `mailpit-mp6gb` | web and readiness port the dependency gate probes | `8025` | no | no |
| `MP_UI_AUTH` | `mailpit-mp6gb` | web UI and API Basic authentication credentials | `<GENERATED>` | **yes** | yes |
| `DEFAULT_FROM_EMAIL` | `django-uv5n2` | development envelope sender | `no-reply@localforge.datarohit.com` (testing uses the local profile) | no | yes |
| `DJANGO_SITE_NAME` | `django-uv5n2` | application name rendered in email | `LocalForge` | no | yes |
| `DJANGO_SITE_URL` | `django-uv5n2` | absolute base URL for email links | `https://localforge.datarohit.com` | no | yes |
| `S3_ENDPOINT_URL` | `django-uv5n2` | SeaweedFS S3 gateway | `http://seaweedfs-sw9cr:8333` | no | yes |
| `SEAWEEDFS_MASTER_PORT` | `seaweedfs-sw9cr` | master status UI port | `9333` | no | yes |
| `SEAWEEDFS_FILER_PORT` | `seaweedfs-sw9cr` | filer browser port | `8888` | no | yes |
| `S3_ACCESS_KEY_ID` | `seaweedfs-sw9cr` | S3 access key | `<GENERATED>` | **yes** | yes |
| `S3_SECRET_ACCESS_KEY` | `seaweedfs-sw9cr` | S3 secret key | `<GENERATED>` | **yes** | yes |
| `WEED_S3_SSE_KEK_PASSPHRASE` | `seaweedfs-sw9cr` | persistent SSE-S3 key-encryption-key passphrase; changing it requires the environment's SeaweedFS data volume to be recreated | `<GENERATED>` | **yes** | yes |
| `S3_BUCKET_NAME` | `django-uv5n2` | media bucket | `localforge-media` | no | yes |
| `S3_REGION_NAME` | `django-uv5n2` | region string the SDK requires | `us-east-1` | no | yes |
| `GRAFANA_ADMIN_USER` | `grafana-gf7qv` | dashboard login | `admin` | no | yes |
| `GRAFANA_ADMIN_PASSWORD` | `grafana-gf7qv` | dashboard password | `<GENERATED>` | **yes** | yes |
| `PROMETHEUS_RETENTION_TIME` | `prometheus-pm5db` | TSDB retention | `7d` | no | no |
| `LOKI_RETENTION_PERIOD` | `loki-lk3ny` | log retention | `168h` | no | no |
| `TRAEFIK_DASHBOARD_PASSWORD` | `traefik-tk2jp` | dashboard password, what a developer types | `<GENERATED>` | **yes** | yes |
| `TRAEFIK_DASHBOARD_AUTH` | `traefik-tk2jp` | basic-auth hash | `<GENERATED>` | **yes** | yes |
| `COMPOSE_PROJECT_NAME` | Compose | project namespace | `localforge-dev` | no | yes |

`VALKEY_CACHE_DB` and `VALKEY_RESULTS_DB` must differ. Django's `RedisCache.clear()` issues `FLUSHDB`, which wipes
the whole logical database — sharing an index means a routine `cache.clear()` destroys every pending Celery result.
See [../adr/0005-valkey-cache.md](../adr/0005-valkey-cache.md).

Ticket 35 adds four reusable fixed-window scopes in cache DB `0`: a broad `600/minute` address boundary at the outer
ASGI HTTP layer before declared or streamed 413 handling and Django; `30/minute` across authentication, recovery,
and account-security operations; `120/minute` for authenticated reads; and `60/minute` for anonymous API use. The
boundary charges every `/api/v1/` request exactly once, including preflight, malformed JSON, unsupported and
unacceptable representations, invalid credentials, and anonymous permission failures. Health, administration,
and non-API paths are excluded. It is a deliberately broad source circuit breaker, not the tight identity policy,
so one shared office does not consume another account's operation budget.

Within an operation scope, an unidentified request uses only its opaque address. An identified request uses the
immutable authenticated account plus an `(address, account)` composite and never a global operation-address
counter. Identity precedence is authenticated `user.pk`, then a cryptographically validated subject from the
view's declared `token` or `refresh` field. Raw `account`, `username`, and `email` values never create general
account buckets, and every request serializer rejects undeclared keys.

One Valkey script obtains production time with `TIME`, checks every applicable dimension, and increments all
dimensions only when every one admits. A denied request therefore cannot poison an otherwise available dimension.
Identified multi-key decisions use `api-throttle:{<scope>:<opaque-account-tag>}:<dimension>:<opaque-value>`.
The braces contain only a digest derived from the scope and immutable account identity; `account` and `composite`
remain readable outside the braces. The two keys therefore share one Valkey Cluster slot, while unrelated accounts
distribute by distinct tags and no address or account value is recoverable from a key.

The fixed epoch window supplies the complete `Retry-After` delay. Tests may inject one coordinated millisecond
value into the cache adapter so spawned processes cannot straddle a wall-clock boundary; production never supplies
that value and remains server-time-defined. The ASGI boundary runs this synchronous cache operation in its own
five-worker executor with one nonblocking admission slot per worker. Saturation creates no executor backlog,
emits one fixed redacted warning per minute, and immediately follows the same general-scope fail-open behavior as a
cache outage. Cancellation retains a slot until any already-running client call finishes, so a cancelled request
cannot oversubscribe the executor. The health endpoint and unrelated executor work never enter this pool.

These general scopes do not replace or weaken the strict PostgreSQL admissions below. Token login, registration,
activation resend, password recovery, and username recovery keep their ticket-owned exact rolling windows and
validation ordering in the primary. Cache eviction can reset only the broad aggregate protection, never the
security decision that guards account or inbox abuse.

Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: same-origin`, and the local sidecar-compatible content security policy. Credentialed CORS reflects
only an exact `DJANGO_CORS_ALLOWED_ORIGINS` member; every response carrying any `Origin` varies on `Origin`, including
denied or duplicated origins and ASGI-level 413 or 429 responses. Duplicate origins are never reflected. `*` is
refused during settings import. Preflight short-circuits only a resolvable versioned API route and advertises
methods implemented by that resolved operation. Unknown, admin, and
health OPTIONS requests follow ordinary routing. The health endpoint is explicitly unthrottled so the load balancer
cannot lock out its own readiness polling. Every exact allowed origin receives
`Access-Control-Expose-Headers: Retry-After, X-Request-ID` in that stable order on ordinary, ASGI 413, boundary 429,
and REST framework 429 responses. Missing, denied, and ambiguous origins receive no exposure header.

HEAD shares the authenticated-read scope with GET. OPTIONS and unsupported safe methods do not consume tight
authentication or recovery account budgets; the broad address boundary remains available for source protection.
Every OpenAPI 429 response declares integer `Retry-After` and UUID `X-Request-ID` response headers centrally.

Testing keeps local plaintext transport: `SECURE_SSL_REDIRECT=false`, `SECURE_HSTS_SECONDS=0`, secure cookie flags
are false, and `SECURE_PROXY_SSL_HEADER` is unset. Development has one exception: Traefik's exact
`localforge.datarohit.com` router overwrites `X-Forwarded-Proto` with `https`, and development settings trust that
known edge contract, emit one-year HSTS, and mark session and CSRF cookies secure. A middleware removes forwarded
protocol headers from peers outside `10.89.2.0/24` before Django evaluates transport. The local development hostname
uses a separate router and remains HTTP. No application code trusts arbitrary forwarded headers from direct clients.

Token login applies both throttle rates to every attempt, successful or failed. The address dimension limits one
source across usernames. It uses `REMOTE_ADDR` unless the immediate peer is in
`DJANGO_TRUSTED_PROXY_NETWORKS`; only then does it walk the forwarded chain from right to left and select the first
valid untrusted hop. Trusted valid hops are skipped lazily, so malformed data farther left than an already selected
client is irrelevant; malformed data encountered before any valid client hop falls back to the immediate peer. The
account dimension resolves an existing account on the primary and hashes its immutable ID, so
database-case-insensitive variants share one bucket while distinct PostgreSQL identities do not collide. Unknown
input uses a hash of PostgreSQL's own lowercase result and discloses no existence state.

Both dimensions are stored in the primary beside account state. Deterministically ordered transaction-scoped
advisory locks serialize a bucket across workers; primary-database time defines each rolling window; only active
events are counted; and one request is recorded in every dimension only when all dimensions admit. Every admission
also takes a separate global cleanup advisory lock and deletes at most the oldest 64 events beyond the longest
supported one-day window through the `occurred_at`-leading index. Login admission inserts at most two events and
activation resend inserts at most three, so sustained admission removes expired backlog faster than it can add rows
without making any request delete an unbounded current-bucket history. Cache clear, `allkeys-lru` eviction, and cache
restart therefore cannot reset security state. PostgreSQL outage or table loss fails closed as a correlated `503`,
and rejection carries the database-derived `Retry-After`. No global password-hash concurrency limit is added:
Ticket 29's exact `30/minute` address and `5/minute` account admission bounds are the accepted governing limits, and
neither this ticket nor the registry defines another limit.

Activation resend uses the same primary-backed admission store with separate opaque bucket prefixes. Only a complete
request body with the exact shape `{email}` and a valid normalized address records admission. It atomically records
the client address, the stable normalized-email identity, and, when the primary resolves an account, that immutable
account identity: at most three events. The email identity is identical before and after account creation and has no
existence-dependent discriminator. Undeclared fields, a missing field, a non-object body, an invalid email, and every
non-POST method record nothing. The exact limits are `30/hour` per client address and `3/hour` for each recipient
identity. Database loss fails closed with the same correlated `503` and primary-derived `Retry-After` behavior as
login admission.

Password reset uses separate opaque prefixes in the same store. Reset request charges address, stable normalized
email, and optional immutable account dimensions; reset confirmation charges address and the submitted immutable
account. Reset request validates the exact `{email}` shape and normalized address before admission. Reset confirmation
validates its exact four-field shape, matching confirmation, and account-independent password policy before admission;
account-sensitive policy runs after bearer authentication and locking. Malformed or pre-admission-invalid bodies
record nothing. The limits are `30/hour` per route-specific address dimension and `3/hour` per recipient or account
dimension. PostgreSQL loss fails closed with the shared correlated `503`.

Username reset uses another set of opaque prefixes in the same store. Reset request charges address, stable
normalized email, and optional immutable account dimensions; reset confirmation charges address and the submitted
immutable account. Reset request validates exact `{email}` shape and normalized address before admission. Reset
confirmation validates exact `{account, token, new_username}` shape and account-independent username format before
admission; PostgreSQL case-insensitive availability runs after bearer authentication and account locking. Malformed
or pre-admission-invalid bodies record nothing. The limits are `30/hour` per route-specific address dimension and
`3/hour` per recipient or account dimension. PostgreSQL loss fails closed with the shared correlated `503`.

### 3.3 Testing overrides

Testing overrides are present only in `.env.testing`:

| Variable | Value | Reason |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | `localforge-test` | selects the testing project namespace |
| `DJANGO_SETTINGS_MODULE` | `config.settings.testing` | selects the testing module |
| `DJANGO_DEBUG` | `false` | tests must not depend on debug behaviour |
| `DJANGO_ALLOWED_HOSTS` | replaces `django-uv5n2` with `django-test-dt5qx` | the test runner's own container name; the development app does not run here |
| `DJANGO_CORS_ALLOWED_ORIGINS` | `http://localhost:8080` | testing permits only its single local browser origin |
| `DJANGO_API_DOCUMENTATION_ENABLED` | `false` | testing is headless; schema contract tests invoke the generator directly |
| `DJANGO_TRUSTED_PROXY_NETWORKS` | `none` | testing has no proxy; direct requests use `REMOTE_ADDR` |
| `POSTGRES_HOST`, `POSTGRES_REPLICA_HOST` | `postgres-tp8vn` | single node; the replica alias points at it |
| `VALKEY_CACHE_HOST` | `valkey-cache-tv4kq` | the testing cache container |
| `VALKEY_CHANNELS_HOST` | `valkey-channels-tv9zw` | the testing channel-layer container |
| `RABBITMQ_HOST` | `rabbitmq-tr6mc` | the testing broker container |
| `S3_ENDPOINT_URL` | `http://seaweedfs-ts3jd:8333` | the testing storage container |
| `SEAWEEDFS_MASTER_PORT` | `9333` | native master status UI inside the testing network |
| `SEAWEEDFS_FILER_PORT` | `8888` | native filer browser inside the testing network |
| `EMAIL_HOST` | `mailpit-tm7bh` | the testing mail container, profile `smtp` |
| `EMAIL_BACKEND` | `django.core.mail.backends.locmem.EmailBackend` | default; SMTP only under the `smtp` profile |
| `DJANGO_SITE_URL` | `http://localhost:8000` | host-mode links stay inside the testing process |
| `CELERY_TASK_ALWAYS_EAGER` | `true` | default; the broker integration test overrides it |

Every `*_HOST` override follows from the testing registry in Section 2.3: the testing stack runs its own
containers, so a host left naming a development container would resolve to nothing on the testing networks.

`.env.testing.host` is the same file with every `*_HOST` set to `127.0.0.1` and every port set to the published host
port from [service-inventory.md](./service-inventory.md) Section 4. That includes `MAILPIT_WEB_PORT`, which the
dependency gate probes: left at the development value it would reach the **development** Mailpit on `8025` and
report the testing one ready while it was dead.

### 3.4 Public deployment profile

The public deployment uses the existing `development` environment and its generated `.env.development` file. It does
not create a production file or Compose project. These values define the Phase 10 contract before runtime hardening:

| Variable or boundary | Public value | Contract |
| --- | --- | --- |
| `DJANGO_ALLOWED_HOSTS` | `localforge.datarohit.com` plus existing local and container names | Accept canonical public Host and preserve local diagnostics. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://localforge.datarohit.com` | Trust only canonical HTTPS browser origin for state-changing requests. |
| `DJANGO_CORS_ALLOWED_ORIGINS` | `https://localforge.datarohit.com` | Reflect one exact credentialed browser origin; never `*`. |
| `DJANGO_SITE_URL` | `https://localforge.datarohit.com` | Generate activation and recovery links on canonical origin. |
| `DEFAULT_FROM_EMAIL` | `no-reply@localforge.datarohit.com` | Use Resend-verified development sender. |
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` | Send development mail through Resend SMTP. |
| `EMAIL_HOST` | `smtp.resend.com` | Resend SMTP relay. |
| `EMAIL_PORT` | `587` | Resend submission port with TLS. |
| `DJANGO_API_DOCUMENTATION_ENABLED` | `true` | Keep schema, Swagger UI, and ReDoc available on application router. |
| WebSocket `Origin` | `https://localforge.datarohit.com` | Reuse exact CORS origin allowlist for socket admission. |

Development Resend SMTP also sets `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD` from the domain-scoped
`RESEND_API_KEY`, `EMAIL_USE_TLS=true`, and `DEFAULT_REPLY_TO_EMAIL=datarohit@outlook.com`. These development-only
settings do not enter testing files.

Public-only settings are reserved only in the development file: `TUNNEL_TOKEN=<GENERATED>` is replaced by the
provider-issued Tunnel credential, `RESEND_API_KEY=<GENERATED>` is replaced by the domain-scoped Resend key, and
`CLOUDFLARED_TUNNEL_NAME=localforge-public` is a non-secret development setting. The testing environment therefore
receives no Tunnel or Resend credential and continues using Mailpit.

Cloudflare Tunnel is the only public ingress. It routes one hostname to Traefik's web entrypoint. Traefik rejects
unmatched hosts; no dashboard, data service, direct Django port, or testing service is published. The secret workflow
records and preserves the provider-issued `RESEND_API_KEY` and `TUNNEL_TOKEN`. Both are mounted
through `env_file` and never appear in Compose
literals, images, logs, browser responses, or documentation.

## 4. Planned scripts

Each is a **separate file**. Nothing here is inlined into a Compose file, a Dockerfile `RUN`, or a settings module.
None exists yet.
Implementation language is Python, run as `uv run python scripts/<name>.py`, except where a script runs inside an
image with no Python. The development machine is Windows, so a `.sh` entrypoint would not run on the host.

### 4.1 `scripts/gen_secrets.py`

| Property | Value |
| --- | --- |
| Responsibility | Create or top up `.env.development`, `.env.testing`, `.env.testing.host` |
| Inputs | `--environment {development,testing,all}`, `--force`; `.env.example` is the variable manifest |
| Generation | Independent `secrets.token_urlsafe(64)` values for `DJANGO_SECRET_KEY`, `DJANGO_JWT_SIGNING_KEY`, and `DJANGO_API_THROTTLE_IDENTITY_HMAC_KEY`; independent `token_urlsafe(32)` values for passwords and the persistent SeaweedFS SSE-S3 key-encryption-key passphrase; `token_hex(20)` for S3 keys; bcrypt at cost 12 for `TRAEFIK_DASHBOARD_AUTH`, which is **composed from** `TRAEFIK_DASHBOARD_PASSWORD` rather than from a password thrown away at generation, because a dashboard credential nobody holds cannot be used to log in. `FLOWER_BASIC_AUTH` and `MP_UI_AUTH` are **plaintext** `user:password` pairs, because those services compare their configured values literally — hashing one would make the digest itself the password. `FLOWER_BROKER_API` is composed from the existing RabbitMQ credential and management endpoint, so queue-depth access creates no second broker secret. Generated plaintext files remain comment-free and use blank lines between logical service groups; encrypted SOPS dotenv files cannot preserve those separators. |
| HMAC startup validation | Requires the exact unpadded textual alphabet `[A-Za-z0-9_-]+`, rejecting standard Base64 `+` and `/` plus `=` padding, then decodes at least 32 bytes and rejects clearly degenerate repeated bytes or known placeholder text. The decoded bytes, not their encoded text, are the runtime HMAC key. The encoded value must remain distinct from both signing keys. These structural checks do not prove randomness; `scripts/gen_secrets.py` remains the only supported source and uses `secrets.token_urlsafe(64)` |
| Quoting | A value containing `$` is written single-quoted. Compose expands unquoted values in **both** `env_file:` and `--env-file`, so a bare bcrypt hash loses everything from its third `$` onward and yields a credential that cannot authenticate. Verified against Compose v5.5.1 on 2026-09-13 |
| Idempotency | Default run **never overwrites an existing value**, and never discards one it does not recognise; it appends only absent variables, so adding an inventory row fills the gap without invalidating a running stack. A composed value is derived when absent and **refused when present but disagreeing** with the variables it is built from, because the generator cannot prove whether such a value is stale or a deliberate edit. `--force` regenerates everything and warns that credential-derived volumes must be recreated |
| Forcing against a live stack | **`--force` silently desynchronises a running stack.** The files get new credentials; every running service keeps the one it started with, and PostgreSQL and RabbitMQ keep theirs inside their data directories, where recreating the container does not reach them. Nothing detects it — every health check still passes, because each probe authenticates with the credential the service itself holds. **A `docker exec … psql -U …` probe proves nothing either**: `initdb` writes a default `pg_hba.conf` that trusts loopback inside the container, so an in-container connection succeeds whatever the role's password is. Measured 2026-09-14, when `postgres-tp8vn` accepted every in-container probe while rejecting the password in its own environment file from the published port. Verify a credential **from the host, over the published port**. Measured 2026-09-14, when a forced run left the primary, the standby, both brokers and all four cache instances rejecting the passwords in their own environment files. The refusal messages recommend `--force`, so this is easy to walk into: take the stack down first, and recreate the volumes the warning lists. To repair a stack already in this state without losing data, `ALTER ROLE … WITH PASSWORD` on PostgreSQL and `rabbitmqctl change_password` on RabbitMQ, then recreate every other service and re-bootstrap the standby, whose `primary_conninfo` still carries the old password |
| Transactionality | Every selected file is resolved and rendered before any is written, and writes are staged then replaced, with rollback. A refused run leaves every file byte-identical |
| Sharing | `.env.testing` and `.env.testing.host` address the same containers and therefore hold the same credentials. They are resolved together, so a run that regenerates one because the other is missing cannot leave the pair disagreeing |
| Exit codes | `0` ok; `1` refused because the existing files are in a state the generator will not silently resolve — a composed value disagreeing with its components, or two files that must share a credential holding different ones; `2` `.env.example` missing or unparsable; `3` refused to write a Git-tracked file, **or could not determine whether a file is tracked**; `4` `--force` without `--environment` |
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

Runs `stanza-create` unconditionally, which upstream documents as safe to repeat and which skips an existing
stanza, then `check`, then the backup schedule loop. Exit `0` clean shutdown, on `TERM` or `INT`; `1` stanza
creation or the configuration check failed; `2` a scheduled backup failed. Shell, same reason.

An existence probe based on `pgbackrest info` was tried and is wrong: `info --stanza=X` echoes `stanza: X` even
when the stanza is missing, so the probe always reported it present and the stanza was never created.

### 4.7 `scripts/seed_storage.py`

`--environment {development,testing}`, with `--endpoint` to override the gateway URL the environment file carries,
which the host needs because that file names the container. `--process-environment` lets the Django entrypoint use
the values Compose already loaded. Creates the media bucket and reads it back. Creating an existing bucket is
success. Exit `0` bucket present; `1` gateway unreachable or nothing configured; `2` credentials rejected.

The access-key identity is applied by the identities file the container renders from the environment at start, not
by this script: SeaweedFS reads `-s3.config` once at boot and exposes no API to install an identity afterwards.

### 4.8 `scripts/audit_naming.py`

Phase 8. Compares live Docker objects against Section 2, **scoped by the Compose project label** so unrelated
containers on this shared machine are ignored. Checks: every expected container exists and no unexpected one does;
every name matches `^[a-z][a-z-]*-[a-z2-9]{5}$`; no anonymous volumes in the project; no `_default` network; every
published port matches [service-inventory.md](./service-inventory.md); every bind is registered and read-only; every
network carries its registered `internal` flag; and a disposable pinned probe proves each internal network cannot
resolve an external name. Invoke `uv run python -m scripts.audit_naming` with
`--environment {development,testing,all}`. Exit `0` clean; `1` violations, printed as
`FAIL convention <check> <object> <detail>`.

### 4.9 `scripts/prepare_broker.py`

Runs after RabbitMQ readiness and before Django, any Celery companion, or the persistent testing runner becomes
healthy. Declares the 28 numbered native delayed-delivery exchanges and the terminal delivery exchange as durable
topic exchanges. This is idempotent and non-destructive; it creates no queues or bindings and prevents Kombu's
queue-before-next-exchange order from producing 28 missing dead-letter-exchange warnings on a fresh broker.

### 4.10 `scripts/audit_security.py`

Runs the Phase 7 security gates without printing secret values. Scopes are `deployment`, `history`,
`dependencies`, `images`, `runtime`, and `all`. The deployment scope requires exactly the four warnings created by
the documented local plaintext transport. The history scope uses pinned Gitleaks 8.28.0 with `--log-opts=--all` to
scan every revision reachable from any ref plus an isolated snapshot of every current tracked and untracked
non-ignored project file, always with redaction and the exact anchored fixture allowlist in `.gitleaks.toml`; a
separate staged scan reads index blobs rather than their working-tree paths. The dependency scope exports the
locked runtime resolution and runs pinned `pip-audit` 2.10.1.

The image scope runs pinned Trivy 0.68.2 against every unique registered local image and compares normalized
fixable high/critical findings with
[../security/image-vulnerability-policy.json](../security/image-vulnerability-policy.json). The policy stores the
exact result digest, package finding count, vulnerability identifiers, and image-filesystem secret findings for
each image, together with its immutable Docker and Trivy artifact IDs. Every required live container must run that
identity, and Trivy scans the identity rather than a mutable tag. Any added, removed, or changed finding fails until
a dated review updates both policy and findings document. Scanner output must have the complete Trivy image-report
schema. The runtime scope proves exact dashboard
rejection, explicit TCP refusal on private ports, exact broker accounts, and absence of every manifest-generated
credential from image history metadata and all retained required-container logs. Exit `0` all selected checks pass;
`1` at least one named scope fails.

### 4.11 `scripts/run_tests.py`

`--mode {container,host,both}`. Exit `0` both pass; `1` container failed; `2` host failed; `3` both failed.

### 4.12 `scripts/check_docstrings.py`

Enforces the documentation standard in [documentation-standard.md](./documentation-standard.md), which is the part
of [../adr/0020-no-comments-structured-docstrings.md](../adr/0020-no-comments-structured-docstrings.md) that the
linter cannot express. Checks two things across `src`, `tests`, `scripts`, and `.github/scripts`: that no comment
line survives outside the named pragma allowlist, and that every module, class, and callable carries a docstring
with the sections its level requires. Positional arguments override the default roots.

Generated and vendored paths are excluded by name — `migrations`, `__pycache__`, `.venv`, `.agents` — because a
file written by `makemigrations` cannot be held to a hand-written standard.

Exit `0` clean; `1` violations, printed one per line as `FAIL <path>:<line> <rule> <detail>` followed by a count.
Runs in `uv run poe check` and as a pre-commit hook.

### 4.13 `scripts/manage_platform.py`

Cross-platform operator adapter exposed by the `uv run poe ...` tasks. It centralizes Compose file selection,
environment preparation, safe rebuilds, explicitly destructive resets, readiness checks, logs, and host/container
test orchestration so onboarding documentation does not duplicate shell logic.

The stable interface is listed by `uv run poe help`. Safe `down` and `rebuild` commands preserve named volumes;
`development-reset` and `testing-reset` are the only task names that delete them. `environments-setup` prepares,
builds only missing local image tags once through representative services, starts with `--no-build`, times, and
audits both environments without running application tests. Existing local image tags make setup a no-build,
no-recreate path; use `development-rebuild` or `testing-rebuild` after source or Dockerfile changes. `docker-audit`
rejects missing, stale, unowned, mislabelled, duplicate, wrong-image, unexpectedly healthcheck-free, unhealthy, or
one-off LocalForge resources. Docker Hub's optional registry and `library` prefixes are normalized before exact
image and tag comparison; no other registry, repository, or tag variation is accepted.

Every Compose invocation pins `--project-name localforge-dev` or `--project-name localforge-test`. Development
commands accept `--proxy-only`, which adds `compose.proxy-only.yaml` and creates the registered `django-uv5n2`
service through Compose `up` without publishing port 8000. Container-mode tests execute inside the persistent
`django-test-dt5qx` service with Compose `exec`; no persistent workflow uses Compose `run`.

The exact `--proxy-only` allowlist is `environments-setup`, `development-up`, `development-rebuild`, and
`development-reset`. Every other command rejects the option before prerequisite, secret, Compose, or destructive
work begins.

Exit `0` means every requested step passed. A child command's non-zero status is returned unchanged; usage and
missing-environment-file refusals return `2`.

### 4.14 `scripts/celery_worker_health.py`

Compose-only health command for `celery-worker-cw8rt`; it is not a host operator workflow. Inputs are
`--destination celery@celery-worker-cw8rt` and `--timeout`, supplied from
`CELERY_WORKER_HEALTH_TIMEOUT_SECONDS`.

The command first requires PID 1's null-delimited command line to be the Celery worker subcommand with the exact
registered hostname. It creates a uniquely named exclusive, auto-deleting direct exchange and reply queue on one
context-managed Kombu connection, then publishes an `ignore_result` task through that queue's producer. Broker
delivery expires `two seconds` before the caller deadline; one-second soft and two-second hard execution limits keep
any started task inside the remaining window. Healthy requires the worker to echo the random identifier through the
transient reply queue before the full timeout. Closing the queue and connection deletes every reply resource, and no
result-backend record exists on success, timeout, expiry, or revocation. Exit `0` means the exact reply arrived; `1`
means identity, process-file, broker, worker, or reply readiness failed. It never prints credentials or payload data.

## 5. Secret handling

1. No real secret appears in `docs/`, `AGENTS.md`, `CONTEXT.md`, a Compose file, a Dockerfile, or a settings module.
   The only placeholder is `<GENERATED>`.
2. Committed artifacts are `.env.example` (placeholders only) and the age-encrypted `.env.*.sops` files.
3. `detect-private-key` is an active pre-commit hook. Do not disable it or add exclusions.
4. Secrets are generated on the machine that runs the platform and never copied between machines in plaintext.
5. On suspected exposure: `gen_secrets.py --force`, then recreate every volume whose contents derive from the old
   value. The set depends on the environment being regenerated:

   | Environment | Volumes to recreate |
   | --- | --- |
   | development | `postgres-pg3ka-data`, `postgres-replica-pg6vy-data`, `rabbitmq-rq4sx-data`, `grafana-gf7qv-data`, `pgadmin-pa7fe-data` |
   | testing | `postgres-tp8vn-data`, `rabbitmq-tr6mc-data` |

   The standby is listed because it holds a copy initialised with the old replication credential, so leaving it in
   place after re-seeding the primary produces a standby that cannot reconnect.
6. Every credential is distinct. No password is reused across services, which is why `VALKEY_CACHE_PASSWORD` and
   `VALKEY_CHANNELS_PASSWORD` are separate even though both run the same image — and why the platform runs two
   `redis_exporter` instances rather than one. See [service-inventory.md](./service-inventory.md) Section 1.1.
