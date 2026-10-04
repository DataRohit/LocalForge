"""Integration tests for user-targeted notification delivery.

Exercises the synchronous publisher through authenticated deployed sockets so group addressing,
fan-out, isolation, and the public notification envelope are observed together.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from channels.db import database_sync_to_async
from django.conf import settings
from redis import asyncio as aioredis

from accounts.jwt_authentication import PrimaryRefreshToken
from config.asgi import application
from notifications.delivery import JsonValue, notification_group_name, publish_notification
from notifications.protocol import WebSocketOutcome
from tests.websocket import WebsocketCommunicator

if TYPE_CHECKING:
    from accounts.models import User

ROUTE = "/ws/notifications/"
HEADERS = [(b"host", b"localhost"), (b"origin", b"http://localhost:8080")]
RECEIVE_TIMEOUT_SECONDS = 10
NO_MESSAGE_TIMEOUT_SECONDS = 0.2
CLEANUP_TIMEOUT_SECONDS = 10
PUBLISHER_TIMEOUT_SECONDS = 60
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

PUBLISHER = """
import os
import sys
import uuid

import django

django.setup()

from notifications.delivery import publish_notification

publisher_pid = os.getpid()
publish_notification(
    uuid.UUID(sys.argv[1]),
    "cross.process",
    {"publisher_pid": publisher_pid},
)
print(publisher_pid, flush=True)
"""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.services("postgres", "valkey-channels"),
    pytest.mark.django_db(databases=["default", "replica"], transaction=True),
]


async def issue_access_token(account: User) -> str:
    """Issue the REST-compatible access credential for one account.

    Uses the same primary-backed token adapter as the public REST endpoint so every delivery test
    crosses the production authentication boundary rather than installing test-only scope state.

    Arguments:
        account: Active account receiving the credential.

    Returns:
        Encoded access token accepted as the sole WebSocket subprotocol.

    Raises:
        None.
    """
    refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)

    return str(refresh.access_token)


async def connect_notification_socket(account: User) -> WebsocketCommunicator:
    """Open one authenticated deployed notification socket.

    Presents the account's REST access credential through the configured subprotocol and verifies
    the server echoes it before returning the usable connection.

    Arguments:
        account: Active account that owns the socket.

    Returns:
        Connected communicator authenticated as the account.

    Raises:
        AssertionError: If authentication, routing, Host, or Origin admission fails.
    """
    access = await issue_access_token(account)
    communicator = WebsocketCommunicator(
        application,
        ROUTE,
        headers=HEADERS,
        subprotocols=[access],
    )
    connected, accepted = await communicator.connect()

    assert connected
    assert accepted == access

    return communicator


def open_channel_instance_client() -> aioredis.Redis:
    """Open an independent client to the configured channel instance.

    Provides an external observation seam for exact subscription cleanup rather than reading the
    channel layer's process-local membership bookkeeping.

    Arguments:
        None.

    Returns:
        Redis-compatible client connected to the dedicated channel instance.

    Raises:
        None.
    """
    layer = cast("dict[str, object]", settings.CHANNEL_LAYERS["default"])
    configuration = cast("dict[str, object]", layer["CONFIG"])
    host = cast("list[dict[str, object]]", configuration["hosts"])[0]

    return aioredis.Redis(
        host=cast("str", host["host"]),
        port=cast("int", host["port"]),
        password=cast("str", host["password"]),
    )


async def group_subscriber_count(client: aioredis.Redis, group: str) -> int:
    """Read the dedicated instance's subscriber count for one group.

    Uses the configured layer prefix and pub/sub group convention so the test observes whether the
    server still holds this process's subscription after the final socket disconnects.

    Arguments:
        client: Independent channel-instance client.
        group: Application group whose server subscription is inspected.

    Returns:
        Number of active pub/sub subscribers for the group.

    Raises:
        AssertionError: If the instance omits the requested channel from its response.
    """
    layer = cast("dict[str, object]", settings.CHANNEL_LAYERS["default"])
    configuration = cast("dict[str, object]", layer["CONFIG"])
    channel = f"{configuration['prefix']}__group__{group}"
    counts = await client.pubsub_numsub(channel)

    assert counts

    return counts[0][1]


@pytest.mark.asyncio
async def test_publish_notification_fans_out_once_and_isolates_users(
    django_user_model: type[User],
) -> None:
    """Deliver once to every current socket owned by only the addressed account.

    Opens two connections for one account and one for another, invokes the synchronous public
    publisher, and observes the exact wire envelope plus the absence of duplicate or cross-user
    delivery.

    Arguments:
        django_user_model: Configured account model used to create notification recipients.

    Returns:
        None.

    Raises:
        AssertionError: If fan-out, exact-once delivery, isolation, or payload shape drifts.
    """
    recipient = await database_sync_to_async(django_user_model.objects.create_user)(
        "notification-recipient",
        "notification-recipient@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    other = await database_sync_to_async(django_user_model.objects.create_user)(
        "notification-other",
        "notification-other@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    first = await connect_notification_socket(recipient)
    second = await connect_notification_socket(recipient)
    unrelated = await connect_notification_socket(other)

    try:
        await asyncio.to_thread(
            publish_notification,
            recipient.pk,
            "account.updated",
            {"revision": 2},
        )
        expected = {
            "type": "notification",
            "payload": {
                "event": "account.updated",
                "data": {"revision": 2},
            },
        }

        assert await first.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS) == expected
        assert await second.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS) == expected
        assert await first.receive_nothing(timeout=NO_MESSAGE_TIMEOUT_SECONDS)
        assert await second.receive_nothing(timeout=NO_MESSAGE_TIMEOUT_SECONDS)
        assert await unrelated.receive_nothing(timeout=NO_MESSAGE_TIMEOUT_SECONDS)
    finally:
        await first.disconnect()
        await second.disconnect()
        await unrelated.disconnect()


@pytest.mark.asyncio
async def test_client_cannot_select_another_accounts_group(
    django_user_model: type[User],
) -> None:
    """Keep membership bound to authenticated scope despite a crafted client frame.

    Sends an apparent subscription request naming another account, observes the existing
    unsupported-type response, and proves subsequent delivery remains isolated to the
    server-selected authenticated group.

    Arguments:
        django_user_model: Configured account model used to create both identities.

    Returns:
        None.

    Raises:
        AssertionError: If client input changes membership or closes the recoverable connection.
    """
    attacker = await database_sync_to_async(django_user_model.objects.create_user)(
        "notification-attacker",
        "notification-attacker@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    target = await database_sync_to_async(django_user_model.objects.create_user)(
        "notification-target",
        "notification-target@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    communicator = await connect_notification_socket(attacker)

    try:
        await communicator.send_json_to(
            {
                "type": "subscribe",
                "payload": {"user_id": str(target.pk)},
            }
        )
        rejection = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)
        assert rejection["type"] == "error"
        assert rejection["payload"]["code"] == WebSocketOutcome.PERMISSION_DENIED.code

        await asyncio.to_thread(
            publish_notification,
            target.pk,
            "target.private",
            {},
        )
        assert await communicator.receive_nothing(timeout=NO_MESSAGE_TIMEOUT_SECONDS)

        await asyncio.to_thread(
            publish_notification,
            attacker.pk,
            "attacker.private",
            {},
        )
        assert await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS) == {
            "type": "notification",
            "payload": {
                "event": "attacker.private",
                "data": {},
            },
        }
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
async def test_disconnect_cleans_membership_and_empty_publication_is_safe(
    django_user_model: type[User],
) -> None:
    """Remove the final group subscription and tolerate publication without listeners.

    Observes the dedicated instance before and after disconnect, then publishes both to the former
    recipient and to an identifier that never connected, proving both no-listener paths are safe
    no-ops with no surviving delivery membership.

    Arguments:
        django_user_model: Configured account model used to create the former recipient.

    Returns:
        None.

    Raises:
        AssertionError: If the live subscription is absent or survives disconnect.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "notification-cleanup",
        "notification-cleanup@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    communicator = await connect_notification_socket(account)
    group = notification_group_name(account.pk)
    client = open_channel_instance_client()
    disconnected = False

    try:
        assert await group_subscriber_count(client, group) == 1

        await communicator.disconnect()
        disconnected = True

        loop = asyncio.get_running_loop()
        deadline = loop.time() + CLEANUP_TIMEOUT_SECONDS
        while await group_subscriber_count(client, group) != 0:
            if loop.time() >= deadline:
                pytest.fail("notification group subscription survived disconnect")
            await asyncio.sleep(0.01)

        await asyncio.to_thread(
            publish_notification,
            account.pk,
            "account.disconnected",
            {},
        )
        await asyncio.to_thread(
            publish_notification,
            uuid.uuid4(),
            "account.never_connected",
            {},
        )
    finally:
        await client.aclose()
        if not disconnected:
            await communicator.disconnect()


@pytest.mark.asyncio
async def test_publication_from_another_process_reaches_the_authenticated_socket(
    django_user_model: type[User],
) -> None:
    """Deliver through the public synchronous helper from another operating-system process.

    Starts an independent interpreter configured for the same deployed channel layer, publishes to
    the authenticated account, and verifies the child process identity arrives in the exact public
    envelope.

    Arguments:
        django_user_model: Configured account model used to create the recipient.

    Returns:
        None.

    Raises:
        AssertionError: If the child fails or its notification does not cross the process boundary.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "notification-cross-process",
        "notification-cross-process@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    communicator = await connect_notification_socket(account)
    environment = {
        **os.environ,
        "PYTHONPATH": f"{REPOSITORY_ROOT / 'backend' / 'src'}{os.pathsep}{REPOSITORY_ROOT}",
    }

    try:
        publisher = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", PUBLISHER, str(account.pk)],
            capture_output=True,
            text=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
            timeout=PUBLISHER_TIMEOUT_SECONDS,
            check=False,
        )

        assert publisher.returncode == 0, publisher.stderr
        publisher_pid = int(publisher.stdout.strip())
        assert publisher_pid != os.getpid()
        assert await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS) == {
            "type": "notification",
            "payload": {
                "event": "cross.process",
                "data": {"publisher_pid": publisher_pid},
            },
        }
        assert await communicator.receive_nothing(timeout=NO_MESSAGE_TIMEOUT_SECONDS)
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
async def test_valid_unicode_notification_survives_transport_and_wire(
    django_user_model: type[User],
) -> None:
    """Preserve valid non-ASCII event, key, and value strings end to end.

    Publishes a JSON-native payload containing Unicode across the real MessagePack channel layer
    and WebSocket encoder, proving UTF-8 validation rejects only unencodable surrogate values.

    Arguments:
        django_user_model: Configured account model used to create the recipient.

    Returns:
        None.

    Raises:
        AssertionError: If valid Unicode or another JSON-native value changes during delivery.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "notification-unicode",
        "notification-unicode@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    communicator = await connect_notification_socket(account)
    event = "account.café.更新"
    data: dict[str, JsonValue] = {
        "संदेश": "नमस्ते 🌍",
        "values": [True, None, 1.5],
    }

    try:
        await asyncio.to_thread(
            publish_notification,
            account.pk,
            event,
            data,
        )

        assert await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS) == {
            "type": "notification",
            "payload": {
                "event": event,
                "data": data,
            },
        }
    finally:
        await communicator.disconnect()
