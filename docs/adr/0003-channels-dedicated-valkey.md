---
status: accepted
date: 2026-09-13
---

# Django Channels on a dedicated Valkey channel layer

WebSockets are served by Django Channels 4.3.2 with `channels-redis` 4.3.0, using
`channels_redis.pubsub.RedisPubSubChannelLayer` against **its own Valkey instance**, separate from the cache.
Channels lives under the `django` GitHub organization and declares Django 6.0 and Python 3.14, so governance risk is
low despite one release in twelve months.

`channels-redis` carries release lag — its CI tests Python 3.14 but its published artifact predates that work. See
[0016](./0016-accept-release-lag.md); it is not re-argued here.

## Why a second Valkey instance

The cache instance runs with `maxmemory` and an LRU eviction policy. A channel layer must never have keys evicted
under memory pressure — eviction silently drops WebSocket messages — and pub/sub traffic distorts cache hit-rate
metrics. Two instances of the same image cost almost nothing locally and become two independent StatefulSets in
[../platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md) rather than one workload with two
incompatible tuning requirements.

`RedisPubSubChannelLayer` is chosen over `RedisChannelLayer` because the pub/sub layer does not depend on the
sorted-set group expiry that has produced most of the reported correctness issues.

## Considered options

**django-eventstream** — server-sent events only, so it cannot carry a bidirectional WebSocket requirement.

**Centrifugo** — a capable standalone realtime server, rejected because it moves connection state outside Django.
Every service in this platform must be reachable from and integrated with Django; a realtime tier that Django only
publishes into breaks that.

**One shared Valkey for cache and channels** — rejected above. The saving is one container; the cost is a cache
policy that silently corrupts realtime delivery.
