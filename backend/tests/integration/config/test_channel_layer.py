"""Integration tests for the channel layer.

Covers delivery between two WebSocket connections, delivery from another process, and the
subscription confirmation that stops a message published straight after a join being dropped.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, cast, override

import pytest
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from channels.routing import ProtocolTypeRouter, URLRouter
from channels_redis.pubsub import RedisPubSubChannelLayer
from django.conf import settings
from django.urls import path
from redis import asyncio as aioredis

from config.channels import ConfirmedRedisPubSubChannelLayer
from config.settings.base import CHANNEL_LAYER_PREFIX
from tests.websocket import WebsocketCommunicator

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
RECEIVE_TIMEOUT_SECONDS = 10
PUBLISHER_TIMEOUT_SECONDS = 60
IMMEDIATE_SEND_ROUNDS = 40
CONCURRENT_JOIN_ROUNDS = 25

PUBLISHER = """
import asyncio
import os
import sys

import django

django.setup()

from channels.layers import get_channel_layer


async def publish() -> None:
    layer = get_channel_layer()
    await layer.group_send(sys.argv[1], {"type": "relay.message", "sent_by": os.getpid()})


print(os.getpid(), flush=True)
asyncio.run(publish())
"""


class RelayConsumer(AsyncJsonWebsocketConsumer):  # type: ignore[misc]
    """A consumer that joins a group and relays what the group carries.

    Exists so the delivery tests drive real WebSocket connections rather than raw channel names,
    which is the boundary the platform actually serves.

    Inherits from `channels.generic.websocket.AsyncJsonWebsocketConsumer`.

    Attributes:
        group: Name of the group this connection joined, taken from the URL.

    Members:
        connect: Accept the socket and join the group named in the URL.
        disconnect: Leave the group when the socket closes.
        receive_json: Publish the received payload to the whole group.
        relay_message: Forward a group message down this socket.
    """

    group: str

    @override
    async def connect(self) -> None:
        """Accept the connection and join its group.

        Reads the group from the URL so each test can use a name no other worker holds, and joins
        before accepting so a message sent straight after the handshake cannot be missed.

        Arguments:
            None.

        Returns:
            None.
        """
        self.group = self.scope["url_route"]["kwargs"]["group"]
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    @override
    async def disconnect(self, code: int) -> None:
        """Leave the group when the socket closes.

        Keeps the instance free of subscriptions belonging to connections that have gone away, so
        one test's group cannot outlive it and feed another.

        Arguments:
            code: WebSocket close code supplied by the framework.

        Returns:
            None.
        """
        del code
        await self.channel_layer.group_discard(self.group, self.channel_name)

    @override
    async def receive_json(self, content: dict[str, Any], **kwargs: object) -> None:
        """Publish what arrived on the socket to the whole group.

        Turns a message sent by one connection into a group message every other connection in the
        group receives, which is the fan-out under test.

        Arguments:
            content: Decoded payload sent by the client.
            **kwargs: Additional arguments supplied by the framework.

        Returns:
            None.
        """
        del kwargs
        await self.channel_layer.group_send(
            self.group,
            {"type": "relay.message", "payload": content},
        )

    async def relay_message(self, event: dict[str, Any]) -> None:
        """Forward a group message down this socket.

        Sends the event the group carried to the connected client, so a test can assert a message
        crossed from another connection or another process.

        Arguments:
            event: Group message dispatched by the channel layer.

        Returns:
            None.
        """
        await self.send_json(event)


def configured_host() -> dict[str, Any]:
    """Return the first configured channel host.

    Reads the address the layer is pointed at out of the settings, so a test can open its own
    connection to the same instance the application uses.

    Arguments:
        None.

    Returns:
        The first entry of the configured host list.
    """
    layer = cast("dict[str, Any]", settings.CHANNEL_LAYERS["default"])
    configuration = cast("dict[str, Any]", layer["CONFIG"])

    return cast("list[dict[str, Any]]", configuration["hosts"])[0]


def open_instance_client() -> aioredis.Redis:
    """Open a direct client to the configured channel instance.

    Gives a test an observation path independent of the channel layer, so what the layer claims can
    be checked against what the instance reports.

    Arguments:
        None.

    Returns:
        A client connected to the instance the channel layer is configured against.
    """
    host = configured_host()

    return aioredis.Redis(
        host=host["host"],
        port=host["port"],
        password=host["password"],
    )


async def connect_relay(group: str) -> WebsocketCommunicator:
    """Open a WebSocket connection joined to a group.

    Connects a communicator to the relay consumer and asserts the handshake succeeded, so a test
    that goes on to assert delivery is never reading from a socket that was refused.

    Arguments:
        group: Group the connection should join.

    Returns:
        The connected communicator.

    Raises:
        AssertionError: If the connection is not accepted.
    """
    application = ProtocolTypeRouter(
        {"websocket": URLRouter([path("relay/<str:group>/", RelayConsumer.as_asgi())])},
    )
    communicator = WebsocketCommunicator(application, f"/relay/{group}/")
    connected, _ = await communicator.connect()

    assert connected

    return communicator


@pytest.mark.integration
@pytest.mark.services("valkey-channels")
@pytest.mark.asyncio
async def test_a_message_sent_by_one_connection_reaches_another(worker_namespace: str) -> None:
    """Deliver a message between two WebSocket connections in one group.

    Confirms a payload sent by one connection arrives at a second connection in the same group,
    which is the fan-out every notification the platform sends depends on.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.

    Returns:
        None.

    Raises:
        AssertionError: If the second connection does not receive what the first sent.
    """
    group = f"{worker_namespace}-connections-{uuid.uuid4().hex}"
    sender = await connect_relay(group)
    receiver = await connect_relay(group)

    try:
        await sender.send_json_to({"greeting": "hello"})

        received = await receiver.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)

        assert received["payload"] == {"greeting": "hello"}
    finally:
        await sender.disconnect()
        await receiver.disconnect()


@pytest.mark.integration
@pytest.mark.services("valkey-channels")
@pytest.mark.asyncio
async def test_a_message_reaches_a_connection_in_another_process(worker_namespace: str) -> None:
    """Deliver a group message across process boundaries.

    Confirms a message published by a separate operating-system process arrives at a WebSocket
    connection here, which is the property that later lets the application run as more than one
    instance and the one an in-process layer would fake.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.

    Returns:
        None.

    Raises:
        AssertionError: If nothing arrives from the process the test started.
    """
    group = f"{worker_namespace}-processes-{uuid.uuid4().hex}"
    receiver = await connect_relay(group)

    try:
        environment = {**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT / "backend" / "src")}
        publisher = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", PUBLISHER, group],
            capture_output=True,
            text=True,
            env=environment,
            cwd=REPOSITORY_ROOT,
            timeout=PUBLISHER_TIMEOUT_SECONDS,
            check=False,
        )

        assert publisher.returncode == 0, publisher.stderr

        received = await receiver.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)

        assert received["sent_by"] == int(publisher.stdout.strip())
    finally:
        await receiver.disconnect()


@pytest.mark.integration
@pytest.mark.services("valkey-channels")
@pytest.mark.asyncio
async def test_a_message_sent_immediately_after_joining_is_not_dropped(
    worker_namespace: str,
) -> None:
    """Publish the instant a group is joined, repeatedly.

    Confirms the layer waits for the instance to register a subscription before returning from the
    join, because the client writes SUBSCRIBE without reading its reply and an unconfirmed layer
    loses roughly a fifth of the messages published this way.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.

    Returns:
        None.

    Raises:
        AssertionError: If any round loses its message.
    """
    layer = get_channel_layer()

    for round_number in range(IMMEDIATE_SEND_ROUNDS):
        group = f"{worker_namespace}-immediate-{uuid.uuid4().hex}"
        channel = await layer.new_channel()
        await layer.group_add(group, channel)
        await layer.group_send(group, {"type": "relay.message", "round": round_number})

        received = await asyncio.wait_for(
            layer.receive(channel),
            timeout=RECEIVE_TIMEOUT_SECONDS,
        )

        assert received["round"] == round_number

        await layer.group_discard(group, channel)


@pytest.mark.integration
@pytest.mark.services("valkey-channels")
@pytest.mark.asyncio
async def test_two_connections_joining_at_once_both_receive(worker_namespace: str) -> None:
    """Join one group from two connections concurrently and publish at once.

    Confirms the barrier holds under the pattern the notification socket will use, where several
    sockets join one group and the first thing addressed to that group follows immediately, which
    is where the unconfirmed layer loses about a fifth of its messages.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.

    Returns:
        None.

    Raises:
        AssertionError: If either connection misses the message.
    """
    layer = get_channel_layer()

    for _ in range(CONCURRENT_JOIN_ROUNDS):
        group = f"{worker_namespace}-concurrent-{uuid.uuid4().hex}"
        first = await layer.new_channel()
        second = await layer.new_channel()

        await asyncio.gather(
            layer.group_add(group, first),
            layer.group_add(group, second),
        )
        await layer.group_send(group, {"type": "relay.message"})

        for channel in (first, second):
            received = await asyncio.wait_for(
                layer.receive(channel),
                timeout=RECEIVE_TIMEOUT_SECONDS,
            )

            assert received["type"] == "relay.message"

        await layer.group_discard(group, first)
        await layer.group_discard(group, second)


@pytest.mark.integration
@pytest.mark.services("valkey-channels")
@pytest.mark.asyncio
async def test_group_subscriptions_carry_the_project_prefix(worker_namespace: str) -> None:
    """Make every group attributable on the shared instance.

    Confirms the name the layer subscribes to for a group carries the configured prefix, read back
    from the instance itself, so an operator listing it can tell this application's traffic apart.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.

    Returns:
        None.

    Raises:
        AssertionError: If the prefixed group name is not subscribed on the instance.
    """
    layer = get_channel_layer()
    group = f"{worker_namespace}-prefix-{uuid.uuid4().hex}"
    expected = f"{CHANNEL_LAYER_PREFIX}__group__{group}"
    channel = await layer.new_channel()
    await layer.group_add(group, channel)
    client = open_instance_client()

    try:
        subscribed = await client.pubsub_channels(f"*{group}")
        names = [name.decode() if isinstance(name, bytes) else name for name in subscribed]

        assert channel.startswith(CHANNEL_LAYER_PREFIX)
        assert names == [expected]
    finally:
        await client.aclose()
        await layer.group_discard(group, channel)


@pytest.mark.integration
@pytest.mark.services("valkey-channels")
@pytest.mark.asyncio
async def test_the_layer_is_the_publish_subscribe_backend_on_its_own_instance() -> None:
    """Run the layer on the instance reserved for it.

    Confirms the configured backend is the confirming publish and subscribe layer, addressed by a
    host list rather than a single address, and that the endpoint answers and is not the cache's,
    because a shared instance would let cache eviction drop a WebSocket message.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If another backend, instance, or host form is configured.
    """
    layer = cast("dict[str, Any]", settings.CHANNEL_LAYERS["default"])
    configuration = cast("dict[str, Any]", layer["CONFIG"])
    hosts = configuration["hosts"]
    first = {name: value for name, value in hosts[0].items() if name != "password"}
    credentials_come_from_the_environment = (
        hosts[0]["password"] == os.environ["VALKEY_CHANNELS_PASSWORD"]
    )
    client = open_instance_client()

    try:
        reachable = await client.ping()
    finally:
        await client.aclose()

    assert layer["BACKEND"] == "config.channels.ConfirmedRedisPubSubChannelLayer"
    assert issubclass(ConfirmedRedisPubSubChannelLayer, RedisPubSubChannelLayer)
    assert type(hosts).__name__ == "list"
    assert first["host"] == os.environ["VALKEY_CHANNELS_HOST"]
    assert first["port"] == int(os.environ["VALKEY_CHANNELS_PORT"])
    assert credentials_come_from_the_environment
    assert reachable
    assert (first["host"], first["port"]) != (
        os.environ["VALKEY_CACHE_HOST"],
        int(os.environ["VALKEY_CACHE_PORT"]),
    )
    assert configuration["prefix"] == CHANNEL_LAYER_PREFIX
