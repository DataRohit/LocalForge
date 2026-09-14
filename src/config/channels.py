"""Channel layer that confirms a subscription before the call that made it returns.

Wraps the publish and subscribe layer so a subscriber is registered on the server before
`group_add` or `new_channel` returns, because the client writes SUBSCRIBE without reading its
reply and a message published immediately afterwards is otherwise dropped.
"""

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Self, cast, override

from channels_redis.pubsub import (
    RedisPubSubChannelLayer,
    RedisPubSubLoopLayer,
    RedisSingleShardConnection,
)
from channels_redis.utils import _wrap_close

if TYPE_CHECKING:
    from collections.abc import Mapping

    from redis.asyncio.client import PubSub, Redis

logger = logging.getLogger(__name__)

CONFIRMATION_CHANNEL_INFIX = "__confirm__"
CONFIRMATION_POLL_SECONDS = 0.005
CONFIRMATION_TIMEOUT_SECONDS = 10.0


class SubscriptionNotConfirmedError(RuntimeError):
    """Raised when the server never reports a subscription this process asked for.

    Signals that the channel instance accepted a SUBSCRIBE but never registered it within the
    confirmation budget, which means messages published now would be silently lost.

    Inherits from `RuntimeError`.

    Attributes:
        None.

    Members:
        None.
    """


class ConfirmingShardConnection(RedisSingleShardConnection):  # type: ignore[misc]
    """A shard connection whose subscribe waits for the server to register the subscription.

    Issues a uniquely named probe subscription on the same connection after the real one and polls
    the server until the probe is visible, which proves the earlier subscription is live too.

    Inherits from `channels_redis.pubsub.RedisSingleShardConnection`.

    Attributes:
        Adds a lock serialising subscription changes to those of the base class.

    Members:
        subscribe: Subscribe to a channel and return only once the server has registered it.
        unsubscribe: Drop a subscription unless the layer has taken it up again.
        flush: Reset the connection with no subscription change in flight.
    """

    _transitions: asyncio.Lock

    def __init__(self, host: Mapping[str, object], channel_layer: object) -> None:
        """Build the connection with no subscription change under way.

        Adds the lock that makes subscribing and confirming one transition, so a caller can never
        observe a subscription this connection has written but the instance has not registered.

        Arguments:
            host: Address of the instance this shard talks to.
            channel_layer: Layer this connection belongs to.

        Returns:
            None.
        """
        super().__init__(host, channel_layer)

        self._transitions = asyncio.Lock()

    @override
    async def subscribe(self, channel: str) -> None:
        """Subscribe to a channel and wait for the server to register it.

        Holds the transition lock across the subscribe and its confirmation, so a concurrent caller
        for the same channel waits rather than returning on a subscription the instance has not yet
        registered, and withdraws the subscription again if it cannot be confirmed.

        Arguments:
            channel: Name of the channel or group channel to subscribe to.

        Returns:
            None.

        Raises:
            SubscriptionNotConfirmedError: If the instance does not register the subscription.
        """
        async with self._transitions:
            if channel in self._subscribed_to:
                return

            await super().subscribe(channel)

            try:
                await self._confirm_pending_subscriptions()
            except BaseException:
                await self._withdraw(channel)
                raise

    @override
    async def unsubscribe(self, channel: str) -> None:
        """Drop a subscription unless the layer has taken it up again.

        Re-reads the layer's membership while holding the transition lock, because a group emptied
        by one caller can be rejoined by another before this runs and unsubscribing then would
        leave the new member with no subscription at all.

        Arguments:
            channel: Name of the channel or group channel to unsubscribe from.

        Returns:
            None.
        """
        async with self._transitions:
            if self.channel_layer.groups.get(channel):
                return

            await super().unsubscribe(channel)

    @override
    async def flush(self) -> None:
        """Reset the connection with no subscription change in flight.

        Takes the transition lock the base class knows nothing about, because a reset landing
        between a subscribe and its confirmation would build a new connection and let the
        confirmation succeed against one that never carried the subscription.

        Arguments:
            None.

        Returns:
            None.
        """
        async with self._transitions:
            await super().flush()

    async def _withdraw(self, channel: str) -> None:
        """Undo a subscription the instance never confirmed.

        Sends the unsubscribe the base class would otherwise never send, because a subscription the
        server holds but this connection has stopped tracking survives until the connection dies.

        Arguments:
            channel: Name of the channel or group channel to withdraw.

        Returns:
            None.
        """
        try:
            await super().unsubscribe(channel)
        except Exception:
            logger.exception("could not withdraw the unconfirmed subscription %s", channel)
        finally:
            self._subscribed_to.discard(channel)

    async def _confirm_pending_subscriptions(self) -> None:
        """Block until everything already written to this connection has been registered.

        Subscribes to a name no other process can hold and polls the instance for it, relying on
        the server handling one connection's commands in the order they were written.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            SubscriptionNotConfirmedError: If the instance does not register the probe in time.
        """
        probe = f"{self.channel_layer.prefix}{CONFIRMATION_CHANNEL_INFIX}{uuid.uuid4().hex}"

        async with self._lock:
            self._ensure_redis()
            self._ensure_receiver()
            handle = cast("PubSub", self._pubsub)
            instance = cast("Redis", self._redis)
            await handle.subscribe(probe)

        try:
            await self._poll_until_registered(instance, probe)
        finally:
            await self._discard_probe(handle, probe)

    async def _discard_probe(self, handle: PubSub, probe: str) -> None:
        """Unsubscribe the probe without displacing a failure already in flight.

        Logs a cleanup failure rather than raising it, because the probe is a name nothing publishes
        to and losing the reason a confirmation failed would cost more than leaving one behind.

        Arguments:
            handle: Publish and subscribe handle the probe was subscribed on.
            probe: Name to unsubscribe.

        Returns:
            None.
        """
        try:
            async with self._lock:
                await handle.unsubscribe(probe)
        except Exception:
            logger.exception("could not unsubscribe the subscription probe %s", probe)

    async def _poll_until_registered(self, instance: Redis, probe: str) -> None:
        """Ask the instance for the probe until it reports it.

        Polls the subscriber count for a name unique to this call, so the answer cannot be
        satisfied by another process that happens to share a group.

        Arguments:
            instance: Client used to ask the server about the probe.
            probe: Channel name this call alone subscribed to.

        Returns:
            None.

        Raises:
            SubscriptionNotConfirmedError: If the count stays at zero for the whole budget.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CONFIRMATION_TIMEOUT_SECONDS

        while True:
            counts = await instance.pubsub_numsub(probe)

            if counts and counts[0][1]:
                return

            if loop.time() >= deadline:
                message = (
                    f"the channel instance did not register {probe} "
                    f"within {CONFIRMATION_TIMEOUT_SECONDS} seconds"
                )
                raise SubscriptionNotConfirmedError(message)

            await asyncio.sleep(CONFIRMATION_POLL_SECONDS)


class ConfirmingLoopLayer(RedisPubSubLoopLayer):  # type: ignore[misc]
    """A per-event-loop layer whose shards confirm their subscriptions.

    Replaces the shard connections the base class builds with confirming ones, reusing the hosts
    the base class already decoded so the host list stays the single source of addresses.

    Inherits from `channels_redis.pubsub.RedisPubSubLoopLayer`.

    Attributes:
        None beyond those of the base class.

    Members:
        None beyond those of the base class.
    """

    _shards: list[ConfirmingShardConnection]

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Build the layer and swap in confirming shard connections.

        Lets the base class decode the configured hosts, then rebuilds the shard list from the
        addresses it produced so no host parsing is duplicated here.

        Arguments:
            *args: Positional arguments accepted by the base layer.
            **kwargs: Keyword arguments accepted by the base layer.

        Returns:
            None.
        """
        super().__init__(*args, **kwargs)

        self._shards = [ConfirmingShardConnection(shard.host, self) for shard in self._shards]


class ConfirmedRedisPubSubChannelLayer(RedisPubSubChannelLayer):  # type: ignore[misc]
    """The configured channel layer, serving confirming layers to each event loop.

    Behaves exactly like the publish and subscribe layer it extends except that a subscription is
    registered on the instance before the call that asked for it returns.

    Inherits from `channels_redis.pubsub.RedisPubSubChannelLayer`.

    Attributes:
        None beyond those of the base class.

    Members:
        None beyond those of the base class.
    """

    @override
    def _get_layer(self: Self) -> ConfirmingLoopLayer:
        """Return the confirming layer bound to the running event loop.

        Mirrors the base class, which names its layer type directly and so offers no other way to
        substitute one, and registers the same close hook so the layer dies with its loop.

        Arguments:
            None.

        Returns:
            The confirming layer for the running event loop, created on first use.
        """
        loop = asyncio.get_running_loop()

        try:
            return cast("ConfirmingLoopLayer", self._layers[loop])
        except KeyError:
            layer = ConfirmingLoopLayer(*self._args, **self._kwargs, channel_layer=self)
            self._layers[loop] = layer
            _wrap_close(self, loop)

        return layer
