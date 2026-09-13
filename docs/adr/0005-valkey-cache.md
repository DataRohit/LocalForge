---
status: accepted
date: 2026-09-13
---

# Valkey for caching, through Django's built-in cache backend

Valkey 9.1.2 (2026-09-01) is the cache, consumed through Django's own
`django.core.cache.backends.redis.RedisCache`. Valkey maintains the 9.1, 9.0, and 8.1 lines in parallel, its
repository was pushed 2026-09-13, and it is **BSD-3-Clause** — the permissive continuation of the Redis codebase,
speaking the same protocol.

There is **no Docker Official Image for Valkey**: `docker.io/library/valkey` returns 404. The reference is
`docker.io/valkey/valkey`. Do not "correct" this to a `library/` path.

A second, separate Valkey instance serves the Channels layer — see [0003](./0003-channels-dedicated-valkey.md).

## Considered options

**Redis OSS 8.10.1** — actively maintained, but since Redis 8 it is tri-licensed RSALv2 / SSPLv1 / AGPLv3. Only the
AGPLv3 option is OSI-approved and none is permissive. Valkey is protocol-compatible, so avoiding the licence
question costs nothing functionally. See [0015](./0015-reject-restricted-licenses.md).

**`django-redis` 7.0.0** (2026-06-02, declares Django 6.0 and Python 3.14) — a good library, rejected because
`RedisCache` ships inside Django and therefore cannot lag a Django release, and this platform needs no feature
beyond it. `django-redis` is the fallback if a needed feature appears.

**Memcached 1.6.45** (2026-07-09) — no persistence, no pub/sub, and no data structures, so it could serve neither
[0003](./0003-channels-dedicated-valkey.md) nor the Celery result backend. One Valkey image covering three roles
beats two tools covering the same ground.

## Consequences

Two behaviours verified in the Django 6.0 source on 2026-09-13 shape the configuration:

1. **`redis-py` is mandatory and `valkey-py` cannot substitute.** Django does not pin or even declare `redis-py` —
   it does a literal `import redis` and reaches for `redis.Redis`, `redis.ConnectionPool`, and
   `redis.connection.DefaultParser`. The fork is not a drop-in. Install `redis-py` and point it at Valkey, which
   works because Valkey speaks RESP.
2. **`RedisCache.clear()` issues `FLUSHDB`**, wiping the entire logical database. The Celery result backend shares
   this Valkey instance, so cache and results **must use different DB indexes** — otherwise a routine
   `cache.clear()` in a test or a shell silently destroys every pending task result. Cache takes DB `0`, Celery
   results take DB `1`.

Valkey ships no web UI, and the obvious companion is unusable: **RedisInsight is SSPL-licensed and has no Valkey
support** — the string "Valkey" appears nowhere in its README or its fifteen most recent release notes. The
dashboard is therefore `redis_exporter` scraped into Prometheus and rendered in Grafana, plus `valkey-cli` for
ad-hoc inspection. This is recorded so the missing UI is not "fixed" by adding RedisInsight later. See
[0009](./0009-prometheus-grafana.md).
