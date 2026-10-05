# Service inventory

> Deprecated compatibility copy. The authoritative Phase 9 inventory is
> [platform/service-inventory.md](platform/service-inventory.md); use it for all current services, ports, and health checks.

Historical compatibility reference only; see the canonical platform service inventory linked above.

Names come from the registry in [conventions.md](./conventions.md). Tool choices and their evidence are in
[../adr/](../adr/README.md). Every port, endpoint, and command below was verified against upstream source or
official documentation on **2026-09-13**; corrections found during that pass are flagged inline, because each one
would otherwise have surfaced as a mysterious failure at build time.

## 1. Development stack

22 services. Ports listed are **host** ports; the internal port is given where it differs.

| Container name | Role | Host ports | Internal | Networks |
| --- | --- | --- | --- | --- |
| `traefik-tk2jp` | reverse proxy, Docker label discovery | `127.0.0.1:8080` web, `127.0.0.1:8081` dashboard | 80, 8080, 8082 health internal only | `edge-net-ne2vk` |
| `django-uv5n2` | Django ASGI app plus observability-only metrics listener | `127.0.0.1:8000` | 8000 app, 8001 metrics on `obsv-net-nb4xt` only | `edge-net-ne2vk`, `app-net-na6hy`, `data-net-nd9pc`, `obsv-net-nb4xt`, `access-net-ha4mz` |
| `postgres-pg3ka` | PostgreSQL 18.6 primary | `127.0.0.1:5432` | 5432 | `data-net-nd9pc`, `access-net-ha4mz` |
| `postgres-replica-pg6vy` | PostgreSQL 18.6 hot standby | `127.0.0.1:5433` | 5432 | `data-net-nd9pc`, `access-net-ha4mz` |
| `pgbackrest-pb2wj` | backup agent, scheduled | none | — | `data-net-nd9pc` |
| `pgadmin-pa7fe` | PostgreSQL dashboard | `127.0.0.1:5050` | 80 | `data-net-nd9pc`, `access-net-ha4mz` |
| `valkey-cache-vc5tn` | cache (DB 0) + Celery results (DB 1) | `127.0.0.1:6379` | 6379 | `app-net-na6hy`, `access-net-ha4mz` |
| `valkey-channels-vh8dm` | Channels layer | `127.0.0.1:6380` | 6379 | `app-net-na6hy`, `access-net-ha4mz` |
| `rabbitmq-rq4sx` | Celery broker | `127.0.0.1:5672` AMQP, `127.0.0.1:15672` management | 5672, 15672, 15692 | `app-net-na6hy`, `access-net-ha4mz` |
| `celery-worker-cw8rt` | task worker | none | — | `app-net-na6hy`, `data-net-nd9pc` |
| `celery-beat-cb4hq` | periodic task scheduler, including daily credential cleanup | none | — | `app-net-na6hy`, `data-net-nd9pc` |
| `flower-fl9zd` | Celery dashboard | `127.0.0.1:5555` | 5555 | `app-net-na6hy`, `access-net-ha4mz` |
| `mailpit-mp6gb` | SMTP capture | `127.0.0.1:1025` SMTP, `127.0.0.1:8025` web | 1025, 8025 | `app-net-na6hy`, `access-net-ha4mz` |
| `seaweedfs-sw9cr` | S3 storage, all-in-one | `127.0.0.1:8333` S3 only | 9333 master, 8080 volume, 8888 filer, 8333 S3 | `app-net-na6hy`, `access-net-ha4mz` |
| `prometheus-pm5db` | metrics collection | none | 9090 | `obsv-net-nb4xt` |
| `grafana-gf7qv` | metrics + logs visualization | `127.0.0.1:3000` | 3000 | `obsv-net-nb4xt`, `access-net-ha4mz` |
| `loki-lk3ny` | log storage and query | none | 3100 | `obsv-net-nb4xt` |
| `alloy-al6wz` | log collection | none | 12345 | `obsv-net-nb4xt` |
| `cadvisor-cv8mh` | container metrics | none | 8080 | `obsv-net-nb4xt` |
| `postgres-exporter-pe4rk` | PostgreSQL metrics, both nodes | none | 9187 | `data-net-nd9pc`, `obsv-net-nb4xt` |
| `valkey-cache-exporter-ve7ts` | cache Valkey metrics | none | 9121 | `app-net-na6hy`, `obsv-net-nb4xt` |
| `valkey-channels-exporter-vx4nq` | channels Valkey metrics | none | 9121 | `app-net-na6hy`, `obsv-net-nb4xt` |

### 1.1 Why two Valkey exporters

`redis_exporter` can scrape several instances from one process through `/scrape?target=`, and the obvious design is
one exporter for both Valkey instances. Its README rules that out for us:

> If authentication is needed for the Redis instances then you can set the password via the `--redis.password`
> command line option of the exporter (this means you can currently only use one password across the instances you
> try to scrape this way. Use several exporters if this is a problem).

[conventions.md](./conventions.md) Section 5 requires a distinct credential per service, so the two instances have
different passwords and one exporter cannot reach both. Two exporters is the resolution; sharing a password to save
a container is not.

PostgreSQL has the opposite shape — primary and standby are one cluster sharing one credential — so a single
`postgres-exporter-pe4rk` covers both nodes.

### 1.2 Port allocations that are not defaults

Each is a deliberate remap. Reverting one reintroduces a collision.

| Service | Default | Published as | Reason |
| --- | --- | --- | --- |
| `postgres-replica-pg6vy` | 5432 | `5433` | both PostgreSQL nodes reachable from the host at once |
| `valkey-channels-vh8dm` | 6379 | `6380` | both Valkey instances reachable at once |

Every published development port is bound to host loopback. Native SeaweedFS administration, Prometheus, Loki,
Alloy, cAdvisor, and exporter ports remain container-internal; Grafana is the authenticated observability surface.
Services without a host publication do not join the non-internal access zone, so the observability backends and
exporters retain only their internal service networks and cannot reach the internet.
The direct Django publication remains useful for local diagnostics and health checks. `edge-net-ne2vk` is fixed at
`10.89.2.0/24`; Django trusts forwarded client addresses only when the immediate peer belongs to that explicit
proxy subnet. The Traefik web entrypoint keeps insecure forwarded-header trust disabled, replaces untrusted client
metadata, and supplies its own standard forwarded address. Testing has no trusted proxy subnet and uses
`REMOTE_ADDR` directly.

### 1.3 Four flags that are not optional

**SeaweedFS binds two extra ports by default in 4.46.** `weed server -s3` opens an Iceberg REST catalog on **8181**
and a Lance namespace server on **9101** unless told otherwise, and 9101 is the conventional `node_exporter` port.
This platform uses neither, so start it with both disabled:

```text
weed server -dir=/data -s3 -s3.config=/etc/seaweedfs/s3.json -s3.port.iceberg=0 -s3.port.lance=0 \
  -ip.bind=0.0.0.0 -master.telemetry=false
```

`-s3` implicitly enables the filer, so `-filer` is redundant. gRPC ports are derived as `10000 + port` — 19333,
18080, 18888, 18333 — and stay container-internal.

**SeaweedFS binds one detected interface, not every interface.** `-ip.bind` defaults to `-ip`, which defaults to
the first container address SeaweedFS detects. On a container attached to both a service zone and an access zone
that address is whichever Docker enumerated first, so a published port reaches the container and is then refused:
the socket accepts and closes, the container stays healthy, and nothing reports a fault. Measured 2026-09-14 —
development bound its access-zone address and worked by accident while testing bound its app-zone address and
failed. Pass `-ip.bind=0.0.0.0` so both the published port and the service zone reach it.

**SeaweedFS reports to its vendor unless told not to.** `weed server` logs
`Reporting anonymous cluster statistics to https://telemetry.seaweedfs.com/api/collect every 24h0m0s` and schedules
that POST once 10 GiB are stored. The service sits on an access zone, which ADR-0021 deliberately leaves
non-internal, so the egress is real rather than theoretical. Measured 2026-09-14. Under `weed server` the flag is
namespaced to the master — `-master.telemetry=false`, **not** the bare `-telemetry=false` the log line suggests,
which that subcommand does not accept.

**Alloy listens on loopback by default.** Its default `--server.http.listen-addr` is `127.0.0.1:12345`, so without
an override the UI and its metrics are unreachable from outside the container and the health check fails while the
process is perfectly healthy:

```text
run --server.http.listen-addr=0.0.0.0:12345 --storage.path=/var/lib/alloy/data /etc/alloy/config.alloy
```

## 2. Dependency order

`depends_on` with `condition: service_healthy`. Every service another service connects to declares a real
`healthcheck`; `service_started` is not sufficient and is not used for those.

| Tier | Services | Waits for |
| --- | --- | --- |
| 1 | `postgres-pg3ka`, `valkey-cache-vc5tn`, `valkey-channels-vh8dm`, `rabbitmq-rq4sx`, `mailpit-mp6gb`, `seaweedfs-sw9cr`, `loki-lk3ny` | nothing |
| 2 | `postgres-replica-pg6vy`, `pgbackrest-pb2wj` | `postgres-pg3ka` healthy |
| 3 | `django-uv5n2` | tier 1 healthy, plus `postgres-replica-pg6vy` healthy |
| 4 | `celery-worker-cw8rt`, `celery-beat-cb4hq` | `rabbitmq-rq4sx` and `valkey-cache-vc5tn` healthy, and `django-uv5n2` healthy so migrations have run |
| 5 | `traefik-tk2jp`, `pgadmin-pa7fe` | their backends healthy |
| 6 | `flower-fl9zd` | broker and `celery-worker-cw8rt` healthy |
| 7 | `postgres-exporter-pe4rk`, `valkey-cache-exporter-ve7ts`, `valkey-channels-exporter-vx4nq`, `cadvisor-cv8mh`, `alloy-al6wz` | their scrape targets healthy |
| 8 | `prometheus-pm5db` | `django-uv5n2` and health-checkable exporters healthy; remaining exporters started |
| 9 | `grafana-gf7qv` | `prometheus-pm5db` healthy, `loki-lk3ny` started — that image carries no probe, so it can never report healthy |

`django-uv5n2` runs migrations in its entrypoint **before** binding its port, so tier 4 waiting on it healthy also
waits on the schema being current. Celery workers never run migrations: they reuse the same image and pass their own
command to the entrypoint, which waits for dependencies and then hands over.

`celery-worker-cw8rt` consumes the explicit durable default and slow queues with environment-controlled concurrency
and prefetch. Its health check executes one opaque task and result round trip after confirming the registered local
node identity, its Compose stop grace is bounded by the environment inventory, and terminal failures publish scrubbed
records to the durable dead-letter queue, which the worker does not consume. Task logs retain only exception types,
never arbitrary exception text or tracebacks. One slow task cannot starve ordinary work because the configured
concurrency may not fall below two, and the operational slow probe cannot exceed the worker soft time limit even when
executed eagerly.

`flower-fl9zd` reads worker execution events and the authenticated RabbitMQ management API to show registered workers,
active tasks, default and slow queue depths, and recent outcomes. Basic authentication applies to the UI and API;
unauthenticated API mode is not enabled. Flower waits for the broker and the worker health check before starting, so
its initial inspection cannot race worker registration and emit false warning records. It sits on the application and
access networks, carries no edge labels, and is excluded from testing. Before event dispatch, successful results
become type names and retry/failure exception plus traceback fields are redacted, so task history cannot recover a
value hidden from arguments or logs.

Ticket 43 makes `celery-beat-cb4hq` operationally responsible for running SimpleJWT's upstream
`flushexpiredtokens` command once daily. The command's delete is routed to `default`, the authoritative primary;
Ticket 30 proves expired outstanding and cascaded blacklist rows are removed without touching unexpired rows, but
does not start the future scheduler early. The same ticket owns bounded primary cleanup of expired activation,
password-reset, and username-reset tombstones after each protocol's configured maximum age.

Both schedules are persisted through django-celery-beat and editable in the Django administration interface.
Account-token cleanup uses oldest-first bounded batches, message expiry equal to its interval, and one
database-scoped advisory lock so an overrun is skipped rather than stacked. Beat stores `last_run_at` and run counts
on the primary, preserving due state across container restart. A scheduler-specific database router pins every
django-celery-beat read and write to `default`, ahead of the ordinary replica router. Account-token retention adds one
second beyond each configured lifetime so whole-second signed bearers remain classifiable through their inclusive
validity boundary.

**Tier 3's wait covers the tier-1 services that have a probe.** `loki-lk3ny` has none — Section 2.1 records why — so
nothing can depend on it with `service_healthy`, and the application does not depend on it at all: a log store that
is down must not stop the application serving.

### 2.1 Health checks

Every row verified against upstream source or docs on 2026-09-13.

| Service | Check | Note |
| --- | --- | --- |
| `postgres-pg3ka`, `postgres-tp8vn` | `pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"` | |
| `postgres-replica-pg6vy` | `pg_isready` **and** `psql -tAc "SELECT pg_is_in_recovery()"` returning `t` | `pg_isready` alone cannot tell a standby from a primary |
| `valkey-*` | `valkey-cli --no-auth-warning -a "$PASSWORD" ping` returning `PONG` | |
| `rabbitmq-*` | `rabbitmq-diagnostics -q check_running && rabbitmq-diagnostics -q check_local_alarms` | upstream's documented **stage 3** check, verbatim. See the cost note below |
| `mailpit-*` | `CMD ["/mailpit", "readyz"]` | **not** an HTTP probe. The image is Alpine with no `curl` or `wget`, and upstream's own `HEALTHCHECK` uses this CLI form. `/readyz` and `/livez` exist over HTTP but nothing inside the image can call them |
| `loki-lk3ny`, `valkey-*-exporter-*` | **none** | measured 2026-09-14: these three images ship no shell and no HTTP client, so no in-container probe is possible. `/ready` and `/metrics` answer over HTTP but nothing inside can call them, and unlike Mailpit they carry no CLI probe either. Their liveness is asserted by Prometheus, whose targets page is the criterion that matters. Depend on them with `service_started`, never `service_healthy` |
| `alloy-al6wz` | `bash -c "exec 3<>/dev/tcp/127.0.0.1/12345"` | the image carries no `curl` or `wget` but does carry a full Debian userland, so bash opens the socket directly. Alloy is the one service whose silent death stops log collection with no other symptom, so it keeps a probe |
| `seaweedfs-*` | `GET /healthz` on 9333 and 8333 | **the master has no `/status` route.** The S3 gateway accepts `/status`, `/healthz`, and `/readyz`; `/healthz` works on both, so use it uniformly |
| `django-uv5n2` | `GET /health/`, aggregating primary, replica, cache, channel layer, broker, object storage, and mail | Returns `200` only when every dependency is `working`; otherwise `503`. Public JSON exposes stable states only, while authenticated staff also receive bounded durations and generic failure categories. |
| `prometheus-pm5db` | `GET /-/ready` | `/-/healthy` exists but answers liveness, not readiness |
| `grafana-gf7qv` | `GET /api/health` | |
| `cadvisor-cv8mh` | `GET /healthz` | Registered machine-id mounts keep host metadata warning-free; OOM events use an exact read-only `/dev/kmsg` device grant instead of privileged mode |

**RabbitMQ health checks are expensive.** Each `rabbitmq-diagnostics` invocation joins and leaves the Erlang
distribution cluster. Use `interval: 30s` or longer with a generous `start_period`. Upstream notes that its own
Kubernetes Operator uses a plain **TCP check on the AMQP port** as the readiness probe and defines no liveness
probe, calling that the best practice; stage 3 is a defensible richer check for a dev stack, provided it does not
run every five seconds.

**RabbitMQ must pin its hostname.** The node derives its name, and therefore its Mnesia directory, from the
container hostname, which Compose leaves as the container ID unless `hostname:` is set. Without it the broker
stores state under `rabbit@<container-id>`: a `docker restart` preserves the ID and appears to work, but any
recreate — `down` then `up`, a manifest edit, a re-pinned image — starts an empty node, silently discards every
durable queue, and orphans the previous directory in the volume forever. Measured 2026-09-14. Both brokers
therefore set `hostname:` to their registered container name.

**Application liveness is distinct from readiness.** A running Django process remains alive during a dependency
outage, but `/health/` reports `readiness: not_ready` and returns `503`. Compose and Traefik both use that endpoint,
so an unavailable instance leaves proxy rotation without being killed or restarted merely because one dependency
is temporarily down. Overlapping Docker, Traefik, and operator polls join one process-wide aggregate dependency
collection instead of duplicating work against the five-worker probe pool. Waiters reuse the elected result and
retain an eight-second response deadline below the ten-second infrastructure timeout. The elected collection keeps
single-flight ownership in its coordinator worker until it actually completes, so poll schedule alignment cannot
create capacity-shaped false `503` responses or duplicate stalled work.

**External mail readiness is bounded and stable.** Development opens Resend SMTP, retries one transient connection
or cleanup failure, then reuses that successful result for 60 seconds inside each application process. Frequent
Compose and Traefik polling therefore shares the aggregate probe and does not flood the hosted relay or remove Django
for lock contention or one brief transport error. After the bounded success expires, two failed live attempts still
report mail unavailable and return `503`. Testing keeps its configured Mailpit or in-memory backend and never reaches
Resend.

**Worker health proves a current round trip.** The probe confirms PID 1 is the exact registered Celery node, then
creates an exclusive auto-deleting direct exchange and reply queue on one context-managed Kombu connection. It
publishes an `ignore_result` task through that queue's producer, with delivery expiry reserving the final two seconds
for the task's one-second soft and two-second hard execution limits. Healthy requires the worker to echo the opaque
identifier through the transient queue before the full timeout. Closing the queue and connection deletes every reply
resource, no result-backend key can be recreated later, and the explicit contexts prevent RabbitMQ abandoned-client
warnings.

**WebSocket admission uses the dedicated channel instance.** After Host, Origin, and JSON web token authentication,
the application applies the configured per-account fixed-window connection rate through one atomic Valkey script.
All Django workers share the count; store timeout or loss fails closed. Complete messages are limited to 65,536
bytes in the application, while Uvicorn independently rejects messages above its configured 131,072-byte transport
ceiling before ASGI dispatch.

### 2.2 Runtime truth gate

Container health is necessary but not sufficient. After startup, rebuild, failure recovery, or a service-facing
change, record one bounded observation window beginning before the exercised behavior:

1. Run environment health and the project-scoped Docker ownership audit.
2. Exercise the changed deployed route, WebSocket, task, schedule, storage, mail, dashboard, or operator seam.
3. Inspect every affected container's logs for that exact window.
4. Reconcile response or task outcome with log severity and message.

Pass requires the documented behavior plus no unexplained `WARNING`, `ERROR`, or `CRITICAL`. In particular, a
successful terminal HTTP body is logged as `request completed` at info level; `request stream failed` at warning
level is reserved for actual body-delivery interruption. Expected warnings must be listed in the owning ADR or this
inventory with their trigger and safety argument. Absence of a test failure is never evidence that a live warning
is acceptable.

Public internet scanners routinely request paths outside the fixed application surface. A correct HTTP `404` remains
fully structured and correlated but is normalized from Django's default `WARNING` to `INFO`, because absence of
`/wp-admin`, `.env`, or another unknown path is the required secure outcome rather than a service fault. Other
client failures and every server failure retain their standard warning or error severity.

The following vendor startup records are expected only between process start and the service becoming ready. They
must not recur in a post-readiness exercise window:

| Service | Record | Trigger and disposition |
| --- | --- | --- |
| `grafana-gf7qv` | `skipped registering status sub-resource that does not support dual writing` | Grafana 13 registers its bundled recording-rule API while unified storage is enabled. The resource remains available and `/api/health` must pass. |
| `loki-lk3ny` | `error getting ingester clients`, `empty ring` | Loki's single process initializes the query path before its ingester joins the in-memory ring. `/ready` must pass after the documented delay. |
| `prometheus-pm5db` | `A lockfile from a previous execution already existed. It was replaced` | A source-preserving Docker recreation can leave the exclusive-volume lock file behind after the old container has stopped. WAL replay and `/-/ready` must pass, and only one container may own the volume. |
| `pgadmin-pa7fe` | Python `SyntaxWarning: 'return' in a 'finally' block` from `sshtunnel.py` | pgAdmin's vendored dependency is compiled during Python 3.14 startup and warns about syntax that remains executable. The warning must occur only before readiness; the dashboard health check and authenticated database connection must pass. |
| `rabbitmq-rq4sx` | deprecated `management_metrics_collection` warning | RabbitMQ 4.3 reports the management plugin's metrics collector that Flower uses for queue depth. The authenticated management API and Prometheus endpoint must both pass. |
| `rabbitmq-*` | none for worker QoS | Consumed task queues are quorum queues and Celery detects that topology, so it uses consumer-scoped QoS instead of RabbitMQ's removed global QoS mode. Any `global_qos` record is a blocker. |
| `rabbitmq-tr6mc` | `client unexpectedly closed TCP connection` during the bounded suite window | Separate-process worker tests use Celery's remote shutdown before their bounded fallback, but Celery and pytest worker process exit still close AMQP sockets without RabbitMQ's close handshake. The exact paired signature is classified independently of collection, suite, health, ownership, and residue so another failure does not manufacture a secondary log-policy failure; the original failing gate still blocks the mode. Every per-test queue must be deleted and broker health green afterward. Any idle, unpaired, or non-test occurrence is a blocker. |
| `postgres-tp8vn` | Account duplicate-key violations, the two named diagnostic-marker cast failures, or missing `accounts_login_throttle_event` during a bounded suite window | Integration tests deliberately exercise PostgreSQL uniqueness races, redacted driver diagnostics, and authoritative throttle-table loss. Exact registered constraint/table names and marker values are classified independently of collection, suite, health, ownership, and residue so another failure does not manufacture a secondary log-policy failure; the original failing gate still blocks the mode. Any other PostgreSQL warning-or-higher record or any idle occurrence is a blocker. |
| `seaweedfs-*` | info-level `Not current leader`, local gRPC socket connection failure, or `skipping default store dir in /data/filerldb2` | SeaweedFS all-in-one components begin dialing before the embedded Raft leader and local sockets exist, and the volume server excludes the filer's metadata directory from its data-directory scan. Embedded IAM is disabled because the platform uses the fixed S3 identity file, and a generated persistent KEK passphrase protects any SSE-S3 key material. Both `/healthz` probes and an S3 byte round trip must pass. |
| `traefik-tk2jp` | encoded-character rejection warning | Traefik 3.7 warns when the entrypoint explicitly rejects encoded slash, backslash, null, semicolon, percent, question-mark, and hash characters. Both entrypoints pin every option to `false` to prevent proxy/backend split views. |

The controlled integration audit deliberately produces one bounded application error record while the real testing
cache is stopped:

| Service | Record | Trigger and disposition |
| --- | --- | --- |
| host and `django-test-dt5qx` runtime probes | `django.request` error for `Service Unavailable: /health/` | Expected only between the project-scoped cache stop and recovery. The response must be `503`, the public body must name only the cache as unavailable, both modes must pass the degraded assertion, and no traceback or infrastructure detail may enter the response. |

## 3. Dashboards

Every service either exposes a native UI or is given a companion.

| Service | Dashboard | URL | Native / companion | Auth |
| --- | --- | --- | --- | --- |
| `traefik-tk2jp` | Traefik dashboard | `http://localhost:8081/dashboard/` | native | basic auth, `TRAEFIK_DASHBOARD_AUTH` |
| `django-uv5n2` | Django admin | `http://localhost:8000/admin/` | native | Django superuser |
| `django-uv5n2` | OpenAPI schema | `http://localhost:8000/api/schema/` | native, drf-spectacular | none locally |
| `django-uv5n2` | Swagger UI | `http://localhost:8000/api/schema/swagger-ui/` | native, drf-spectacular | none locally; assets from the sidecar |
| `django-uv5n2` | ReDoc | `http://localhost:8000/api/schema/redoc/` | native, drf-spectacular | none locally |
| `django-uv5n2` | JSON readiness | `http://localhost:8000/health/` | first-party API | public stable states; authenticated staff receive bounded details |
| `postgres-pg3ka`, `postgres-replica-pg6vy` | pgAdmin 4 | `http://localhost:5050/` | **companion** | `PGADMIN_DEFAULT_EMAIL` + `PGADMIN_DEFAULT_PASSWORD` |
| `valkey-cache-vc5tn`, `valkey-channels-vh8dm` | Grafana dashboard fed by the two exporters | `http://localhost:3000/` | **companion** | Grafana login |
| `rabbitmq-rq4sx` | management plugin | `http://localhost:15672/` | native | `RABBITMQ_DEFAULT_USER` + `RABBITMQ_DEFAULT_PASS` |
| `celery-worker-cw8rt` | Flower | `http://localhost:5555/` | **companion** | `FLOWER_BASIC_AUTH` |
| `celery-beat-cb4hq` | django-celery-beat admin pages | `http://localhost:8000/admin/django_celery_beat/` | companion, via Django admin | Django superuser |
| `mailpit-mp6gb` | Mailpit web UI | `http://localhost:8025/` | native | `MP_UI_AUTH` |
| `seaweedfs-sw9cr` | master status and filer browser | container-internal only | native | not host-published |
| `prometheus-pm5db` | metrics through Grafana | `http://localhost:3000/` | **companion** | Grafana login |
| `grafana-gf7qv` | Grafana | `http://localhost:3000/` | native | `GRAFANA_ADMIN_USER` + `GRAFANA_ADMIN_PASSWORD` |
| `loki-lk3ny` | Grafana Explore | `http://localhost:3000/explore` | **companion** | Grafana login |
| `alloy-al6wz` | component state through its internal API and Grafana-fed logs | container-internal only | native + companion | not host-published |
| `cadvisor-cv8mh` | container metrics through Grafana | `http://localhost:3000/` | **companion** | Grafana login |
| `pgbackrest-pb2wj` | `pgbackrest info`, plus its logs in Loki | CLI and Grafana Explore | **companion** | container shell |
| `postgres-exporter-pe4rk` | metrics through Grafana | `http://localhost:3000/` | **companion** | Grafana login |
| `valkey-cache-exporter-ve7ts` | metrics through Grafana | `http://localhost:3000/` | **companion** | Grafana login |
| `valkey-channels-exporter-vx4nq` | metrics through Grafana | `http://localhost:3000/` | **companion** | Grafana login |

After setup and verification, run `make developer-access-export` to create browser-importable
`bookmarks.html` and `passwords.csv` files at the repository root. The password import contains only credentials
generated in `.env.development`; create and save the Django superuser separately with `make superuser`.
Both generated files are ignored by Git.

The schema, Swagger UI, and ReDoc entries are three optional infrastructure routes on the existing Django service.
Their `/static/drf_spectacular_sidecar/` browser dependencies are static resources intercepted by the development
ASGI entry point, not application routes, and do not expand the fixed route table. The same flag removes all three
routes and static interception in headless testing. Traefik forwards both pages and assets through its existing
`localforge` router; there is no separate static router or service.

### 3.1 Dashboard configuration that is easy to get wrong

**Traefik's dashboard router must match `/api` as well as `/dashboard`.** The dashboard is a single-page app that
calls the API; a rule matching only `/dashboard` renders a blank page. Set `providers.docker.exposedByDefault:
false` — it defaults to `true`, which would publish every container in the stack. Avoid `api.insecure: true`: it
serves an unauthenticated dashboard on an auto-created `traefik` entrypoint at `:8080`, colliding with the web
entrypoint.

**pgAdmin needs five settings beyond the credentials.** `PGADMIN_LISTEN_ADDRESS=0.0.0.0` — the default `[::]` fails
in IPv4-only setups. `PGADMIN_DISABLE_POSTFIX=True` avoids starting an unused mail server.
`PGADMIN_SERVER_JSON_FILE` points at a mounted `servers.json` so both PostgreSQL nodes are pre-registered; those
definitions load **only on first launch** unless `PGADMIN_REPLACE_SERVERS_ON_STARTUP=True`, which is what makes the
registration declarative.

Two more were measured on 2026-09-14, when the dashboard was built. The login now uses the monitored
`support@datarohit.com` identity, so `PGADMIN_CONFIG_ALLOW_SPECIAL_EMAIL_DOMAINS=[]` grants no reserved-domain
exception. `PGADMIN_CONFIG_UPGRADE_CHECK_ENABLED=False` prevents the dashboard from fetching
`https://www.pgadmin.org/versions.json` from a container that ADR-0021 leaves with real egress. Both settings land
in the image's generated `config_distro.py`, which is where to confirm them.

Its health check reads the configuration database rather than the `/misc/ping` route, which answers unconditionally:
the upstream entrypoint does not stop on a failed server import, so the dashboard can serve happily with neither
node registered. The probe asserts the administrator exists and that both registered hosts are present.

**Flower's option is `basic_auth`.** The CLI flag `--basic-auth` and the env var `FLOWER_BASIC_AUTH` are the same
option — every Flower option accepts a `FLOWER_`-prefixed env var. Multiple users are comma-separated. It is not the
`auth` option, which is an OAuth email-allowlist regex.

**A generated password reaches `redis_exporter` through its argument list.** That image carries no shell, so the
render pattern used elsewhere is unavailable, and the two instances need different values from one environment
file. The password is therefore visible in `docker inspect` and `docker compose config`. Accepted knowingly, on
the same footing as Valkey's own `--requirepass`; the security audit owns whether that stands.

**postgres_exporter takes credentials split out.** `DATA_SOURCE_URI` accepts the host only; username and password go
in `DATA_SOURCE_USER` and `DATA_SOURCE_PASS`, or `DATA_SOURCE_PASS_FILE` to keep the password out of the
environment. The process runs as uid/gid 65534, and its multi-target probe path is `/probe`, not `/scrape`.

**One exporter reaches both nodes through `/probe`, not through a list.** Measured 2026-09-14 against v0.20.1:
`DATA_SOURCE_URI` is a single URI, so a comma-separated value is swallowed into the last query parameter and the
exporter reports `unsupported sslmode "disable,postgres-replica-…"`. Only the legacy `DATA_SOURCE_NAME` accepts a
list, and that is one connection string with the password inside it, which the ticket's own criterion forbids. The
resolution keeps both: the primary is scraped at `/metrics` using the split variables, and the standby is scraped
at `/probe?target=…&auth_module=…`, whose module carries the username and password as discrete fields and supplies
`sslmode: disable` — without it the probe defaults to requiring TLS and fails against a server that has none. The
module file is rendered at start from the environment, so no credential is committed.

Grafana is provisioned as code: datasource and dashboard provider files are mounted read-only from
`docker/grafana/provisioning/`, so wiping the volume loses nothing.

**Application observability stays on the observability path.** Prometheus scrapes
`http://django-metrics-nb4xt:8001/metrics` over `obsv-net-nb4xt`. A second Uvicorn listener binds only to that
network alias; the edge-facing listener on port 8000 carries no metrics route, and port 8001 is not published.
`django-prometheus` instruments requests and both database aliases, and its multiprocess directory is cleared before
either listener starts so both application workers contribute to one scrape. The provisioned dashboard renders
request rate, status, latency, and database query rate.

Every HTTP request receives a generated `X-Request-ID`. The same value is attached to structured records emitted
inside the request and to its body-free completion record. Alloy parses the JSON stream, preserves the bounded
`level` and `logger` fields as labels, and adds `service=django-uv5n2` from Docker discovery; the request identifier
stays a parsed field rather than a high-cardinality label. The formatter redacts named credential fields, textual
credential assignments, object representations, and passwords embedded in connection URLs before stdout. The health
view uses a context-preserving executor so its dependency-check logs keep the same identifier across the thread
boundary.

## 4. Testing stack

Seven services, one profile-gated. Every dashboard and UI service is dropped; nothing here listens for a human.

| Container name | Role | Host ports | Networks | Default |
| --- | --- | --- | --- | --- |
| `django-test-dt5qx` | persistent pytest runner, idle until explicit `compose exec` | none | `app-net-nt5rk`, `data-net-nt8fq` | yes |
| `postgres-tp8vn` | PostgreSQL 18.6, single node | `127.0.0.1:25432` | `data-net-nt8fq`, `access-net-ht6pn` | yes |
| `valkey-cache-tv4kq` | cache | `127.0.0.1:26379` | `app-net-nt5rk`, `access-net-ht6pn` | yes |
| `valkey-channels-tv9zw` | Channels layer | `127.0.0.1:26380` | `app-net-nt5rk`, `access-net-ht6pn` | yes |
| `rabbitmq-tr6mc` | Celery broker, no management plugin | `127.0.0.1:25672` | `app-net-nt5rk`, `access-net-ht6pn` | yes |
| `seaweedfs-ts3jd` | S3 storage | `127.0.0.1:28333` S3 only | `app-net-nt5rk`, `access-net-ht6pn` | yes |
| `mailpit-tm7bh` | SMTP capture | `127.0.0.1:21025` SMTP, `127.0.0.1:28025` web | `app-net-nt5rk`, `access-net-ht6pn` | **no — profile `smtp`** |

Three networks, not five: there is no `edge` zone because no proxy runs, and no `obsv` zone because every
observability service is excluded. The two service zones are `internal: true`; `access-net-ht6pn` is not, because
every testing service publishes a host port and Docker drops a publication made from an internal network. See
[../adr/0021-access-zone-for-published-ports.md](../adr/0021-access-zone-for-published-ports.md).

Published host ports are loopback-only. Testing ports are the development port plus 20000 where a development
publication remains; SeaweedFS master and filer are intentionally internal in both environments.

`django-test-dt5qx` bind-mounts `.env.development` and `.env.testing` read-only. The suite asserts that each
environment file carries the credentials its services were started with — a distinct password per Valkey instance,
a broker user that matches the compose definition — and without the files those assertions skip, which would make
container mode report a different result from host mode for an environmental reason. The files stay on the
machine, out of the image, and out of version control.

The runner's Compose command is an idle Python process with a local filesystem health check. Setup starts it with
`compose up`; explicit container-mode test commands use `compose exec`. This keeps the registered container inside
the `localforge-test` project and prevents generated `*-run-*` one-off containers.

WebSocket integration tests use the repository's ASGI queue communicator from `backend/tests/websocket.py`. They do not
import `channels.testing`, so Daphne and its deprecated Windows event-loop-policy side effect are absent from the
development dependency group.

### 4.1 Exclusions, and why

| Excluded | Reason |
| --- | --- |
| `traefik-tk2jp` | Tests call the ASGI app directly. A proxy between the test and the code under test adds a failure mode and proves nothing |
| `pgadmin-pa7fe`, `flower-fl9zd`, `grafana-gf7qv` | Dashboards. Headless rule |
| `prometheus-pm5db` | Collection backend for dashboards. The metrics endpoint itself is asserted in-process against `django-prometheus` |
| `loki-lk3ny`, `alloy-al6wz` | Log aggregation is an operator concern. Tests assert on Django's logging configuration |
| `cadvisor-cv8mh`, all three exporters | They exist only to feed Prometheus, which is excluded |
| `postgres-replica-pg6vy` | The `replica` alias points at `postgres-tp8vn`. Router paths are exercised; replication lag is not. See [../adr/0012-streaming-replication.md](../adr/0012-streaming-replication.md) |
| `pgbackrest-pb2wj` | Time-based operational behaviour, verified in development by the phase 6 gate |
| `celery-worker-cw8rt`, `celery-beat-cb4hq` | `CELERY_TASK_ALWAYS_EAGER=true` runs ordinary tasks in-process. Broker tests override the namespaced setting; worker-service tests start the real Celery command as a separate bounded process, while focused logging tests retain the in-process worker. Control, reply, and event queues are also declared directly because the focused workers skip bootsteps |
| `mailpit-tm7bh` | Default `EMAIL_BACKEND` is `locmem`. Every complete host/container gate temporarily starts `--profile smtp`, runs all five SMTP cases, then clears and removes the container. The web port lets host mode assert through the REST API, not only send |

Every account email uses a Celery task. Credential-link tasks carry account ID plus the required raw bearer and keep
their durable at-most-once claim/no-retry contract. Credential-free password and username change notices carry only
account ID, retry with bounded backoff, and accept harmless duplicate security notices after worker-loss redelivery.
All request-side publication occurs after primary transaction commit and cannot change the public response. The
dispatch boundary contains eager task retry signals and queued publication failures with type-only logs.

After a username-change email succeeds, its worker task synchronously publishes
`account.username_changed` with `{}` to the account's immutable notification group. An offline account is a safe
no-op. Channel-layer failure is contained after the completed task work and produces only a type-only error record,
so the email task does not retry or fail because live notification delivery is temporarily unavailable.

### 4.2 The two required modes

| Mode | Command | Env file | Hostnames |
| --- | --- | --- | --- |
| Container | `make testing-test-container` | `.env.testing` | container names on the testing networks |
| Host | `make testing-test-host` | `.env.testing.host` | `127.0.0.1` and the published ports above |

Both modes temporarily start Mailpit before collection and run the same complete internal Poe task, which keeps
their collection arithmetic and stage timings comparable. Mailpit is accepted only during the complete run and is
removed before the command returns. That interface hides two stages:

1. `test-core` selects `not security_timing` and runs `pytest -n auto --dist loadgroup` with 100% branch coverage.
2. `test-security-timing` selects `security_timing` and runs `pytest -n 4 --dist load --no-cov`.

The timing stage contains exactly 23 credential wall-clock cases: 14 secondary-token cases, five JSON web token
cases, two registration cases covering normal and isolated activation-token store operation, one password-reset
case, and one username-reset case. Twenty-one cases perform five warmups and thirty measured requests per path. The
remaining secondary-token and JSON web token cases each warm one four-request batch per outcome, then measure seven
alternating four-request batches per outcome to detect lock serialization under concurrency. Every case enforces a
median delta no larger than the greater of twenty percent or ten milliseconds. The deterministic equivalent-work,
schedule, and policy tests remain in the covered core stage. Timing cases carry no `serial` marker, and
`--dist load` deliberately ignores the modules' load-group affinity so independent parameter cases can occupy the
bounded four-worker pool.

Registration timing disables eager Celery execution and publishes each real or dummy activation task to an isolated
per-worker RabbitMQ quorum queue. The test consumes the single queued task outside the timed interval and never
starts a worker, so response timing matches the public broker-publication boundary while Mailpit delivery remains a
separate integration concern. After a successful complete host suite, the orchestrator runs the registration timing
file in five independent host pytest processes. Any failed attempt blocks the host gate.

`test`, `test-parallel`, `test-serial`, `test-fresh`, and `test-integration` enter SMTP-aware host orchestration.
Their internal `*-stages` tasks run only after dependencies and temporary Mailpit are ready. Focused integration
uses the same parallel load-group runner while excluding timing cases, so shared-state tests keep their required
process isolation. `test-security-timing` remains the internal focused timing interface. Pytest warnings are errors,
and the controller fails the session if any report is skipped. A bare
`uv run --project backend --directory backend pytest` remains the same complete collection in one process, and
`make test-serial` is the documented way to read the stack of a test
that timed out.

Both parallel stages set `--max-worker-restart=0`. A timed-out or crashed worker therefore fails the gate
immediately instead of being replaced, avoiding known `loadgroup` restart hangs in pytest-xdist 3.8.0.

Core and timing evidence is written separately to `test-results/pytest-core.xml` and
`test-results/pytest-security-timing.xml` in host mode. The one-off container keeps the repository read-only; its
complete console output and exit status provide the independently auditable container counts.

Host mode is why every testing service publishes a host port even though container mode never uses them.

`testing-test-both` first collects complete, core, and security-timing selections in each mode and requires
`core + timing == complete`, then runs both complete suites even when the first fails. After each mode it verifies
testing health, project-scoped container/network/volume/image ownership, the exact six-container headless set, and
a bounded testing log window. It prints the two complete counts, per-mode wall-clock durations, post-mode results,
and total duration. Exit `10` means container only failed, `11` host only, `12` both, and `13` equal-mode execution
passed but complete collection counts differed. Standalone mode commands preserve the underlying failed child
status.

`make testing-verify` is the complete operator workflow: rebuild the source-matched test image, recreate and
verify the dependency stack, run container mode followed by host mode and its five-pass registration timing
stability gate, then stop the testing environment while preserving its named volumes. It leaves a failed stack
running so status and logs remain available. `make testing-registration-timing-stability` exposes the same
host stability gate without rerunning the complete suite.

## 5. Image pins

Exact versions everywhere. `latest` is forbidden, including Dockerfile base images. All tags checked 2026-09-13.

| Image | Tag |
| --- | --- |
| `docker.io/library/postgres` | `18.6` |
| `docker.io/valkey/valkey` | `9.1.2` |
| `docker.io/library/rabbitmq` | `4.3.5-management` (development) |
| `docker.io/library/rabbitmq` | `4.3.5` (testing) |
| `docker.io/library/traefik` | `v3.7.13` |
| `docker.io/axllent/mailpit` | `v1.31.1` |
| `docker.io/chrislusf/seaweedfs` | `4.46` |
| `docker.io/dpage/pgadmin4` | `9.17` |
| `docker.io/prom/prometheus` | `v3.14.0` |
| `docker.io/grafana/grafana-oss` | `13.0.2` |
| `docker.io/grafana/loki` | `3.7.7` |
| `docker.io/grafana/alloy` | `v1.19.2` |
| `ghcr.io/google/cadvisor` | `v0.60.5` |
| `quay.io/prometheuscommunity/postgres-exporter` | `v0.20.1` |
| `docker.io/oliver006/redis_exporter` | `v1.91.1` |

Built locally rather than pulled:

| Image | Dockerfile | Base |
| --- | --- | --- |
| `localforge/django` | `docker/django/Dockerfile`, target `runtime`, tagged `0.1.0` | `python:3.14.6-slim`. Serves `django-uv5n2`, and later `celery-worker-cw8rt`, `celery-beat-cb4hq` and `flower-fl9zd`, which pass their own command to the entrypoint and therefore wait for dependencies without migrating |
| `localforge/django-test` | `docker/django/Dockerfile`, target `test`, tagged `0.1.0` | the runtime stage plus the development dependencies, the suite, and the repository artifacts the suite reads |
| `localforge/pgbackrest` | `docker/pgbackrest/Dockerfile` | `postgres:18.6` plus PGDG `pgbackrest`, tagged `18.6`. Run by **both** `pgbackrest-pb2wj` and `postgres-pg3ka`, because `archive_command` executes on the primary and therefore needs the binary there. See [../adr/0011-pgbackrest-backups.md](../adr/0011-pgbackrest-backups.md) |

The application image installs from the lockfile with a pinned `uv` binary copied from `ghcr.io/astral-sh/uv:0.12.1`,
rather than fetching one at build time. Measured 2026-09-14: a build container cannot reach `files.pythonhosted.org`
on this network — TLS interception breaks the handshake — while the configured package index answers normally, so
bootstrapping the installer through `pip` fails and copying the binary is the only reliable route.

A rebuild with unchanged inputs is fully cached and therefore performs no downloads. Note that
`docker build --network none` still fails: BuildKit includes the network mode in a `RUN` layer's cache key, so
changing it invalidates the dependency layer and re-runs the install. Cache reuse, not the flag, is what makes the
repeat build offline.

Two registry facts that look like typos and are not:

- **Valkey has no Docker Official Image.** `docker.io/library/valkey` returns 404; the reference is
  `docker.io/valkey/valkey`.
- **cAdvisor moved to GHCR.** `gcr.io/cadvisor/cadvisor` carries only versions below v0.53.0.

One pin was wrong and has been corrected:

- **Grafana 13.2.1 was never published.** The pull fails with `not found`. Checked 2026-09-14 against the Docker
  Hub tag list: the newest published `grafana/grafana-oss` is **13.0.2** (pushed 2026-06-02), and the 13 line runs
  13.0.1 to 13.0.2. Pinned to `13.0.2`, which pulls. A tag that does not exist fails at the first `up`, so this cost
  nothing but a minute — unlike the silent failures recorded above.

## 6. Audit commands

Run in phase 8 of [../build/plan.md](../build/plan.md).

**Scope every audit to the Compose project.** This machine is shared and already runs unrelated containers, volumes,
and networks — an unfiltered `docker ps` returns other people's work, and a count-based check against it fails for
the wrong reason.

### 6.1 Containers

```console
docker ps --filter "label=com.docker.compose.project=localforge-dev" --format "{{.Names}}\t{{.Status}}"
```

Expected: exactly 22 rows for development, every name matching `^[a-z][a-z-]*-[a-z2-9]{5}$`, every `Status`
beginning with `Up` and containing `(healthy)` where a health check exists.

Pass: the name set equals [conventions.md](./conventions.md) Section 2.2 exactly — nothing missing, nothing extra.

Fail: any numeric suffix such as `-1`; any Docker-generated name shaped like `localforge-dev-postgres-1`; any
container `Restarting` or `unhealthy`.

### 6.2 Volumes

```console
docker volume ls --filter "label=com.docker.compose.project=localforge-dev" --format "{{.Name}}"
docker volume ls --filter "dangling=true" --format "{{.Name}}"
```

Pass: the first lists one row per volume-registry entry. The second lists **no volume belonging to this project** —
a 64-hex-character name under our project is an anonymous volume and an automatic fail. Dangling volumes from other
projects on this machine are not ours to judge.

### 6.3 Networks

```console
docker network ls --filter "label=com.docker.compose.project=localforge-dev" --format "{{.Name}}\t{{.Driver}}"
```

Pass: the registry networks are present and **no `localforge-dev_default` exists**. A `_default` network means some
service omitted its `networks:` key.

### 6.4 Mounts

```console
docker inspect postgres-pg3ka --format "{{range .Mounts}}{{.Type}} {{.Name}} -> {{.Destination}}{{println}}{{end}}"
```

Pass: every `volume` row carries a name from the registry; every `bind` row points inside the repository and is
read-only. Two services mount the Docker socket, both read-only: `traefik-tk2jp` and `alloy-al6wz`. A read-only
bind does not make the API read-only, so both are root-equivalent over the daemon; the security audit owns that.
Read-only by
requirement.

### 6.5 Offline enforcement

```console
docker exec pgbackrest-pb2wj getent hosts example.com
```

Pass: non-zero exit and no output, because every network `pgbackrest-pb2wj` is on carries `internal: true`.
Containers on an internal network still reach each other normally — `internal` blocks outbound traffic, not traffic
between members.

The probe targets `pgbackrest-pb2wj` rather than `postgres-pg3ka` because this audit proves a *container* is
offline, not a network. A container on an access zone resolves `example.com` by design, so only the three services
that publish no host port — `pgbackrest-pb2wj`, `celery-worker-cw8rt`, and `celery-beat-cb4hq` — can be asserted
offline. See [../adr/0021-access-zone-for-published-ports.md](../adr/0021-access-zone-for-published-ports.md).
