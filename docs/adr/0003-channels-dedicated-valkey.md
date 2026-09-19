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

## Verified on this Python

Measured 2026-09-14 while integrating the layer. `channels-redis` 4.3.0 runs on Python 3.14.6 against Valkey 9.1.2
without the [0016](./0016-accept-release-lag.md) escape: a group message published by a separate operating-system
process arrives at a consumer in this one, authenticated with the instance password, under both the host and the
container test modes. The release lag is packaging metadata only, as 0016 predicted, so no pin is relaxed and no
fork is carried.

The layer is configured with a **host list** rather than a single address, so moving to a clustered or replicated
instance later is a configuration change and not a code change, and with a `prefix` of `localforge`, so every
channel and group pub/sub name on the instance is attributable to this application. The pub/sub layer itself keeps
no Redis keys, so an operator looks for those with `PUBSUB CHANNELS`, not `SCAN`.

Ticket 41 also places bounded `localforge:websocket-admission:<uuid>` counters on this dedicated instance. They use
an atomic server-timed fixed-window script and expire just after their active window; unlike group subscriptions,
these admission keys are visible through `SCAN`. Keeping them beside channel traffic makes connection admission
shared across workers without exposing it to cache eviction.

## Subscribing is made a delivery barrier

`channels-redis` writes `SUBSCRIBE` to the pub/sub connection and returns without reading the reply — `redis-py`'s
`PubSub.execute_command` says so in terms: *"don't parse the response in this function -- it could pull a legitimate
message off the stack"*. `group_send` then publishes on a **different** pooled connection, so the server can process
the `PUBLISH` before the `SUBSCRIBE`. A message sent immediately after a socket joins a group is dropped.

Measured 2026-09-14 against `valkey-channels-tv9zw`: joining a fresh group and publishing to it at once lost **46 of
200 messages**, and a variant losing 77 of 200. This is not theoretical, and it lands exactly where the notification
socket will: a client joins, and the first thing addressed to it disappears.

`config.channels.ConfirmedRedisPubSubChannelLayer` subclasses the pub/sub layer and makes a subscription a barrier.
After the real `SUBSCRIBE` is written, the shard writes a second one for a name unique to that call and polls
`PUBSUB NUMSUB` for it on a pooled connection. Redis executes one connection's commands in the order they were
written, so once the probe is visible the real subscription is live. The probe is then unsubscribed. A name no other
process can hold is used deliberately: a shared group channel already carries other processes' subscribers, so its
count proves nothing about this one.

Subscribing and confirming are **one transition**, serialised per connection by a lock the publish path does not
take. The base class records a channel as subscribed the moment the command is written, so without that lock a
second caller joining the same group while the first was still confirming would return on a subscription the
instance had not registered. Serialising also bounds the confirmation's demand on the connection pool to one query
at a time, which matters because a burst of joins would otherwise open one pooled connection each against a
`max_connections` of 100.

Two further consequences follow from making the transition atomic. A confirmation that fails **withdraws** the
subscription from the instance rather than only forgetting it locally, because the base class gates its
`UNSUBSCRIBE` on the tracking set and a subscription the server holds but nothing tracks survives until the
connection dies. And an unsubscribe re-reads the layer's group membership while holding the lock, because a group
emptied by a departing socket can be rejoined before the unsubscribe runs, and sending it then would leave the new
member subscribed locally and absent on the instance.

Measured 2026-09-14 against `valkey-channels-tv9zw`, comparing the stock layer with this one. Joining a fresh group
and publishing to it at once: stock lost **46 of 200**; this layer lost **0 of 600**. Two channels joining one group
through `asyncio.gather` and publishing at once: stock lost **66 of 300 receives**; this layer lost **0 of 600
receives**. The confirmation costs one extra `SUBSCRIBE`, one or more `PUBSUB NUMSUB` calls, and one `UNSUBSCRIBE`
per new subscription, at a mean of roughly 7 ms and a 95th percentile of roughly 22 ms per join **in host test
mode**, with one poll the common case; in-container it is roughly eight times faster, so 22 ms is not a figure to
carry into sizing. `config.channels.CONFIRMATION_TIMEOUT_SECONDS` bounds the wait, and the host list carries
`socket_timeout`, so an instance that never registers a subscription raises rather than returning a membership that
silently drops messages, and a black-holed connection fails rather than holding the lock for the whole budget.

The cost of serialising is that joins to **distinct** channels cannot overlap. Joins to one group are cheap, because
every caller after the first finds the subscription already confirmed and returns without touching the instance. A
socket connecting costs two distinct-channel transitions, its own channel and its group, so a reconnect storm of two
hundred sockets is on the order of half a second in the deployed topology. That is the trade against the connection
pool, and it is the number to revisit if the notification socket in tickets 38 to 41 ever serves a larger fleet.

## Considered options

**django-eventstream** — server-sent events only, so it cannot carry a bidirectional WebSocket requirement.

**Centrifugo** — a capable standalone realtime server, rejected because it moves connection state outside Django.
Every service in this platform must be reachable from and integrated with Django; a realtime tier that Django only
publishes into breaks that.

**One shared Valkey for cache and channels** — rejected above. The saving is one container; the cost is a cache
policy that silently corrupts realtime delivery.
