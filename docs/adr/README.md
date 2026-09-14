# Architecture decision records

Every tool choice for the local backend platform, one decision per file. Read the one you are about to touch, not
all of them.

Format follows `.agents/skills/domain-modeling/ADR-FORMAT.md`: sequential numbering, `Status` frontmatter, and
`Considered options` / `Consequences` only where they earn their place.

## Decision summary

| ADR | Area | Decision | Version | Status |
|---|---|---|---|---|
| [0001](./0001-uvicorn-asgi-server.md) | ASGI app server | Uvicorn | 0.52.4 | accepted |
| [0002](./0002-drf-spectacular-openapi.md) | REST layer, OpenAPI, Swagger UI, ReDoc | DRF + drf-spectacular + sidecar | 3.18.1 / 0.30.0 / 2026.9.1 | accepted |
| [0003](./0003-channels-dedicated-valkey.md) | WebSockets + message layer | Channels + channels-redis on a dedicated Valkey | 4.3.2 / 4.3.0 | accepted |
| [0004](./0004-postgresql.md) | Relational database | PostgreSQL | 18.6 | accepted |
| [0005](./0005-valkey-cache.md) | Caching layer | Valkey + Django's built-in `RedisCache` | 9.1.2 | accepted |
| [0006](./0006-mailpit-smtp-capture.md) | Local SMTP capture | Mailpit | v1.31.1 | accepted |
| [0007](./0007-traefik-reverse-proxy.md) | Reverse proxy | Traefik | v3.7.13 | accepted |
| [0008](./0008-celery-rabbitmq.md) | Task queue + scheduler | Celery + django-celery-beat + RabbitMQ | 5.6.3 / 2.9.0 / 4.3.5 | accepted |
| [0009](./0009-prometheus-grafana.md) | Monitoring | Prometheus + Grafana OSS | v3.14.0 / 13.0.2 | accepted |
| [0010](./0010-loki-alloy-logging.md) | Centralized logging | Loki + Grafana Alloy | 3.7.7 / v1.19.2 | accepted |
| [0011](./0011-pgbackrest-backups.md) | Backup automation | pgBackRest | 2.59.1 | accepted |
| [0012](./0012-streaming-replication.md) | Read replication | PostgreSQL native streaming replication | 18.6 | accepted |
| [0013](./0013-seaweedfs-object-storage.md) | Object storage emulator | SeaweedFS | 4.46 | accepted |
| [0014](./0014-sops-age-secrets.md) | Secrets / env management | SOPS + age + per-environment `.env` | v3.13.3 / v1.3.2 | accepted |
| [0015](./0015-reject-restricted-licenses.md) | Cross-cutting constraint | Reject non-OSI, archived, and phone-home tools | — | accepted |
| [0016](./0016-accept-release-lag.md) | Cross-cutting constraint | Accept **release lag** on upstream-green dependencies | — | accepted |
| [0017](./0017-first-party-account-endpoints.md) | Account API surface | First-party endpoints on DRF + SimpleJWT, **not** Djoser | 5.5.1 | accepted |
| [0018](./0018-api-error-contract.md) | API error contract | One error envelope, exhaustive status-code documentation | — | accepted |
| [0019](./0019-websocket-authentication.md) | WebSocket auth | JWT over the subprotocol header, custom ASGI middleware | — | accepted |
| [0020](./0020-no-comments-structured-docstrings.md) | Code standards | No comments; structured docstrings, mechanically enforced | — | accepted |
| [0021](./0021-access-zone-for-published-ports.md) | Network topology | A dedicated access zone, because internal networks cannot publish host ports | — | accepted |

## Evidence standard

Every claim in these files was checked against a primary source on **2026-09-13**, and each carries that date.

The bar rose during verification. Trove classifiers on PyPI turned out to be a *lagging* indicator — they are
edited by hand and drift from reality in both directions. Where a question was "does this run on Python 3.14" or
"does this work with Django 6.0", the evidence recorded is **the project's CI matrix on its default branch**, quoted
from the workflow file, plus its packaging metadata and any maintainer statement. Three decisions changed shape once
that standard was applied — see [0016](./0016-accept-release-lag.md).

There are no open decisions and no `UNVERIFIED` markers left. Where a fact could not be established, the ADR says
what is unknown and what the executing agent must do about it, rather than deferring the choice.

That standard has now reversed four conclusions. Three are in [0016](./0016-accept-release-lag.md). The fourth is
[0017](./0017-first-party-account-endpoints.md): Djoser supplies exactly the required endpoint surface and
empirically works, but its CI has no Django 6.0 row, no Python 3.14 row, and pins DRF 3.14 — so adopting it would
make this project the QA for the combination.

## Where the rest lives

| Question | File |
|---|---|
| What are the services called, on which ports, in what order | [platform/service-inventory.md](../platform/service-inventory.md) |
| What is the naming registry and the variable inventory | [platform/conventions.md](../platform/conventions.md) |
| How does this map to Kubernetes later | [platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md) |
| What do I actually run, in what order | [build/plan.md](../build/plan.md) |
| What must be installed first | [build/prerequisites.md](../build/prerequisites.md) |
| Which ticket do I pick up next | [.scratch/README.md](../../.scratch/README.md) |
| What do the words mean | [CONTEXT.md](../../CONTEXT.md) |
