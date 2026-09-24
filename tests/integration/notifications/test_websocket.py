"""Public-seam tests for the notification WebSocket protocol.

Connects through the deployed ASGI application so routing, origin admission, frame decoding,
envelope validation, error responses, and disconnect cleanup are observed together.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from channels.layers import get_channel_layer
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.urls import path
from redis import asyncio as aioredis

from config.channels import (
    ConfirmedRedisPubSubChannelLayer,
    ConfirmingLoopLayer,
    ConfirmingShardConnection,
)
from config.logs import REQUEST_ID_META_KEY
from notifications.delivery import notification_group_name
from notifications.protocol import WebSocketOutcome
from notifications.websocket import (
    ExactWebSocketOriginValidator,
    NotificationConsumer,
    WebSocketFailureBoundary,
)
from tests.websocket import WebsocketCommunicator

ROUTE = "/ws/notifications/"
ALLOWED_ORIGIN = b"http://localhost:8080"
RECEIVE_TIMEOUT_SECONDS = 10
APPLICATION_MESSAGE_LIMIT_BYTES = 64 * 1024
TEST_ACCOUNT_ID = UUID("018f22e2-7d42-7f74-9d8a-123456789abc")

FrameSender = Callable[[WebsocketCommunicator], Awaitable[None]]
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

application = ProtocolTypeRouter(
    {
        "websocket": WebSocketFailureBoundary(
            ExactWebSocketOriginValidator(
                URLRouter([path("ws/notifications/", NotificationConsumer.as_asgi())])
            )
        )
    }
)


def open_channel_instance_client() -> aioredis.Redis:
    """Open an independent client to the configured channel instance.

    Provides an external observation seam for failure-path group cleanup rather than relying on
    consumer attributes after its application task has ended.

    Arguments:
        None.

    Returns:
        Redis-compatible client connected to the dedicated channel instance.
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
    """Read the dedicated instance subscriber count for one notification group.

    Resolves the same prefixed pub/sub channel used by the configured layer, providing external
    evidence that failure and cancellation cleanup reached the server.

    Arguments:
        client: Independent channel-instance client.
        group: Application group whose server subscription is inspected.

    Returns:
        Number of active pub/sub subscribers.

    Raises:
        AssertionError: If the instance omits the requested channel.
    """
    layer = cast("dict[str, object]", settings.CHANNEL_LAYERS["default"])
    configuration = cast("dict[str, object]", layer["CONFIG"])
    channel = f"{configuration['prefix']}__group__{group}"
    counts = await client.pubsub_numsub(channel)

    assert counts

    return counts[0][1]


pytestmark = [
    pytest.mark.integration,
    pytest.mark.services("postgres", "valkey-channels"),
    pytest.mark.django_db(databases=["default", "replica"], transaction=True),
]


async def connect_notification_socket(
    *,
    account_id: UUID = TEST_ACCOUNT_ID,
    origin: bytes = ALLOWED_ORIGIN,
    path: str = ROUTE,
    headers: list[tuple[bytes, bytes]] | None = None,
    subprotocols: list[str] | None = None,
) -> tuple[WebsocketCommunicator, bool, str | int | None]:
    """Open the notification socket through the project ASGI application.

    Supplies one allowed browser origin by default while permitting tests to replace the path or
    complete header list, so every admission case crosses the same deployed routing seam.

    Arguments:
        account_id: Immutable account identifier installed in the consumer scope.
        origin: Origin header value used when no explicit header list is supplied.
        path: WebSocket path to request.
        headers: Complete ASGI header list, or ``None`` to build one origin header.
        subprotocols: Optional WebSocket subprotocol values offered by the client.

    Returns:
        Communicator, handshake acceptance flag, and negotiated subprotocol or rejection code.
    """
    request_headers = (
        headers if headers is not None else [(b"host", b"localhost"), (b"origin", origin)]
    )
    communicator = WebsocketCommunicator(
        application,
        path,
        headers=request_headers,
        subprotocols=subprotocols,
    )
    communicator.scope["user"] = SimpleNamespace(pk=account_id)
    connected, detail = await communicator.connect()

    return communicator, connected, detail


async def send_invalid_json(communicator: WebsocketCommunicator) -> None:
    """Send syntactically invalid JSON.

    Drives the text-frame decoder through a payload that cannot produce any JSON value, separating
    syntax rejection from the structural validation exercised by the other malformed cases.

    Arguments:
        communicator: Connected socket receiving the frame.

    Returns:
        None.
    """
    await communicator.send_to(text_data="{")


async def send_binary_frame(communicator: WebsocketCommunicator) -> None:
    """Send a binary frame.

    Exercises the contract that the notification protocol is text JSON only and must close with a
    defined application code rather than allowing Channels to raise on binary input.

    Arguments:
        communicator: Connected socket receiving the frame.

    Returns:
        None.
    """
    await communicator.send_to(bytes_data=b"{}")


def text_sender(value: str) -> FrameSender:
    """Build a sender for one literal text frame.

    Allows invalid JSON spellings and parser-depth cases to cross the transport unchanged instead
    of being normalized or rejected by a client-side serializer.

    Arguments:
        value: Exact text frame the returned sender will transmit.

    Returns:
        Asynchronous sender bound to the supplied text.
    """

    async def send(communicator: WebsocketCommunicator) -> None:
        """Send the bound text frame.

        Preserves the literal input so the server-side decoder alone decides whether it belongs to
        the JSON protocol.

        Arguments:
            communicator: Connected socket receiving the value.

        Returns:
            None.
        """
        await communicator.send_to(text_data=value)

    return send


def json_sender(value: JsonValue) -> FrameSender:
    """Build a sender for one JSON-serializable value.

    Keeps malformed structural examples as independent literals while sharing only the transport
    action, so expected protocol outcomes do not duplicate implementation validation logic.

    Arguments:
        value: JSON value the returned sender will transmit.

    Returns:
        Asynchronous sender bound to the supplied JSON value.
    """

    async def send(communicator: WebsocketCommunicator) -> None:
        """Send the bound JSON value.

        Uses the communicator's public JSON interface so the application receives the same text
        frame a client library would produce.

        Arguments:
            communicator: Connected socket receiving the value.

        Returns:
            None.
        """
        await communicator.send_json_to(value)

    return send


@pytest.mark.asyncio
async def test_notification_route_accepts_an_allowed_origin() -> None:
    """Reach the exact notification route from an allowed browser origin.

    Confirms the authoritative path is wired through the deployed ASGI router and accepted before
    any later ticket adds credential admission.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the route is absent or the allowed origin is refused.
    """
    communicator, connected, _ = await connect_notification_socket()

    try:
        assert connected
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [(b"host", b"localhost")],
        [(b"host", b"localhost"), (b"origin", b"http://untrusted.invalid")],
        [
            (b"host", b"localhost"),
            (b"origin", ALLOWED_ORIGIN),
            (b"origin", ALLOWED_ORIGIN),
        ],
    ],
    ids=["absent", "unlisted", "repeated"],
)
async def test_untrusted_origin_shapes_close_with_permission_denied(
    headers: list[tuple[bytes, bytes]],
) -> None:
    """Reject missing, unlisted, and ambiguous browser origins uniformly.

    Exercises the existing exact-origin parser at the WebSocket handshake so no browser can use an
    omitted, suffix-matched, or repeated value to bypass the configured allowlist.

    Arguments:
        headers: ASGI headers describing the rejected origin shape.

    Returns:
        None.

    Raises:
        AssertionError: If the handshake succeeds or uses another close code.
    """
    communicator, connected, _ = await connect_notification_socket(headers=headers)

    assert connected
    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["type"] == "websocket.close"
    assert output["code"] == WebSocketOutcome.PERMISSION_DENIED.required_close_code()
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_unknown_message_type_returns_a_correlated_error_and_stays_open() -> None:
    """Answer unsupported message types without dropping the connection.

    Confirms the wire response reuses the REST error payload, carries a valid request identifier,
    and can be repeated on the same socket because the failure is recoverable.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the response shape drifts or the socket closes.
    """
    communicator, connected, _ = await connect_notification_socket()

    try:
        assert connected

        for message_type in ("unsupported.first", "unsupported.second"):
            await communicator.send_json_to({"type": message_type, "payload": {}})
            response = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)

            assert response["type"] == "error"
            assert response["payload"]["code"] == WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.code
            assert response["payload"]["message"] == "The message type is not supported."
            assert response["payload"]["details"] == {
                "type": ["No handler is registered for this message type."]
            }
            UUID(response["payload"]["request_id"])
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
async def test_forbidden_subscription_returns_permission_error_and_stays_open() -> None:
    """Reject client-selected membership with the recoverable authorization frame.

    Sends the prohibited subscription shape, verifies the exact central error payload, and then
    sends an unknown type to prove the connection remains usable after authorization refusal.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If permission denial closes the socket or uses another error outcome.
    """
    communicator, connected, _ = await connect_notification_socket()

    try:
        assert connected
        await communicator.send_json_to(
            {
                "type": "subscribe",
                "payload": {"user_id": str(TEST_ACCOUNT_ID)},
            }
        )
        denied = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)
        await communicator.send_json_to({"type": "unsupported", "payload": {}})
        unknown = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)

        assert denied["type"] == "error"
        assert denied["payload"] == {
            "code": WebSocketOutcome.PERMISSION_DENIED.code,
            "message": WebSocketOutcome.PERMISSION_DENIED.message,
            "details": WebSocketOutcome.PERMISSION_DENIED.details(),
            "request_id": denied["payload"]["request_id"],
        }
        UUID(denied["payload"]["request_id"])
        assert unknown["payload"]["code"] == WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.code
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_frame",
    [
        send_invalid_json,
        send_binary_frame,
        text_sender('{"type":"unsupported","payload":{"value":NaN}}'),
        text_sender('{"type":"unsupported","payload":{"value":Infinity}}'),
        text_sender('{"type":"unsupported","payload":{"value":-Infinity}}'),
        text_sender("\ud800"),
        text_sender('{"type":"first","type":"second","payload":{}}'),
        text_sender('{"type":"unsupported","payload":{"key":1,"key":2}}'),
        text_sender("[" * 20000 + "]" * 20000),
        json_sender(None),
        json_sender([]),
        json_sender({"payload": {}}),
        json_sender({"type": "unsupported"}),
        json_sender({"type": 1, "payload": {}}),
        json_sender({"type": "unsupported", "payload": []}),
        json_sender({"type": "unsupported", "payload": {}, "extra": True}),
    ],
    ids=[
        "invalid-json",
        "binary",
        "nan",
        "positive-infinity",
        "negative-infinity",
        "surrogate-text",
        "duplicate-envelope-field",
        "duplicate-payload-field",
        "parser-depth",
        "null",
        "array",
        "missing-type",
        "missing-payload",
        "non-string-type",
        "non-object-payload",
        "extra-field",
    ],
)
async def test_malformed_frames_close_with_the_defined_code(send_frame: FrameSender) -> None:
    """Close every malformed frame shape with the protocol code.

    Covers transport, JSON syntax, top-level type, exact field set, discriminator type, and payload
    type so none can escape as an unhandled Channels exception.

    Arguments:
        send_frame: Action transmitting one malformed frame.

    Returns:
        None.

    Raises:
        AssertionError: If the socket remains open or closes with another code.
    """
    communicator, connected, _ = await connect_notification_socket()

    assert connected
    await send_frame(communicator)

    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)

    assert output["type"] == "websocket.close"
    assert output["code"] == WebSocketOutcome.MALFORMED_FRAME.required_close_code()
    await communicator.disconnect()


def unknown_message_frame_with_size(size: int) -> str:
    """Build one valid unsupported envelope with an exact UTF-8 byte size.

    Uses ASCII padding so character and encoded byte counts are identical, making the application
    boundary assertions independent of client or serializer normalization.

    Arguments:
        size: Exact frame size in bytes.

    Returns:
        JSON text naming an unsupported message type.

    Raises:
        ValueError: If the requested size cannot hold the envelope.
    """
    prefix = '{"type":"unsupported","payload":{"padding":"'
    suffix = '"}}'
    padding_size = size - len(prefix.encode()) - len(suffix.encode())
    if padding_size < 0:
        message = "requested frame size is smaller than the envelope"
        raise ValueError(message)

    return f"{prefix}{'x' * padding_size}{suffix}"


@pytest.mark.asyncio
async def test_application_message_limit_accepts_exactly_64_kib() -> None:
    """Accept the exact application boundary and keep the socket open.

    Sends a valid unsupported envelope whose UTF-8 representation is exactly 64 KiB, then observes
    two recoverable error frames to prove boundary acceptance and continued connection use.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the exact boundary closes the socket or changes the error contract.
    """
    communicator, connected, _ = await connect_notification_socket()

    try:
        assert connected
        assert settings.WEBSOCKET_APPLICATION_MAX_MESSAGE_BYTES == APPLICATION_MESSAGE_LIMIT_BYTES
        exact_frame = unknown_message_frame_with_size(APPLICATION_MESSAGE_LIMIT_BYTES)
        assert len(exact_frame.encode()) == APPLICATION_MESSAGE_LIMIT_BYTES

        await communicator.send_to(text_data=exact_frame)
        first = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)
        await communicator.send_json_to({"type": "unsupported.again", "payload": {}})
        second = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)

        assert first["payload"]["code"] == WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.code
        assert second["payload"]["code"] == WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.code
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("frame_kind", ["text", "binary"])
async def test_application_message_limit_closes_first_oversized_message(
    frame_kind: str,
) -> None:
    """Close text and binary messages at the first byte above 64 KiB.

    Exercises application counting before JSON or binary-type validation so every complete message
    above the declared boundary receives the dedicated frame-too-large close outcome.

    Arguments:
        frame_kind: Whether to send the oversized message as text or binary bytes.

    Returns:
        None.

    Raises:
        AssertionError: If the application accepts the message or uses another close code.
    """
    communicator, connected, _ = await connect_notification_socket()
    oversized = unknown_message_frame_with_size(APPLICATION_MESSAGE_LIMIT_BYTES + 1)

    assert connected
    if frame_kind == "binary":
        await communicator.send_to(bytes_data=oversized.encode())
    else:
        await communicator.send_to(text_data=oversized)

    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["type"] == "websocket.close"
    assert output["code"] == WebSocketOutcome.FRAME_TOO_LARGE.required_close_code()
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_unhandled_consumer_failure_is_correlated_secret_free_and_cleans_group(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contain one unexpected handler failure and remove its group membership.

    Raises after connection acceptance with credential-shaped text, then verifies close code 4500,
    one UUID-correlated secret-free log, no traceback metadata, and exact subscription cleanup.

    Arguments:
        monkeypatch: Fixture replacing the recoverable error sender with a failure.
        caplog: Fixture capturing application logs.

    Returns:
        None.

    Raises:
        AssertionError: If containment, secrecy, correlation, or cleanup differs.
    """
    sensitive_text = "jwt-secret-credential-value"

    async def fail_error(
        _consumer: NotificationConsumer,
        _outcome: WebSocketOutcome,
    ) -> None:
        """Raise the unexpected failure under test.

        Replaces only recoverable error rendering after connection and group setup, leaving the
        outer containment and cleanup paths on their production implementation.

        Arguments:
            _consumer: Consumer receiving the call.
            _outcome: Recoverable outcome that would otherwise be sent.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always, with text that must never enter logs or the client.
        """
        raise RuntimeError(sensitive_text)

    monkeypatch.setattr(NotificationConsumer, "_send_error", fail_error)
    account_id = uuid4()
    communicator, connected, _ = await connect_notification_socket(account_id=account_id)
    group = notification_group_name(account_id)
    client = open_channel_instance_client()

    try:
        assert connected
        assert await group_subscriber_count(client, group) == 1

        with caplog.at_level(logging.ERROR):
            await communicator.send_json_to({"type": "unsupported", "payload": {}})
            output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)

        assert output["type"] == "websocket.close"
        assert output["code"] == WebSocketOutcome.SERVER_ERROR.required_close_code()
        assert sensitive_text not in repr(output)
        assert "traceback" not in repr(output).lower()
        deadline = asyncio.get_running_loop().time() + RECEIVE_TIMEOUT_SECONDS
        while await group_subscriber_count(client, group) != 0:
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("notification group subscription survived consumer failure")
            await asyncio.sleep(0.01)
    finally:
        await client.aclose()
        await communicator.disconnect()

    records = [
        record for record in caplog.records if record.getMessage() == "websocket connection failed"
    ]
    assert len(records) == 1
    UUID(cast("str", records[0].__dict__[REQUEST_ID_META_KEY]))
    assert records[0].exc_info is None
    assert sensitive_text not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_consumer_cancellation_cleans_group_and_propagates() -> None:
    """Remove group membership when the application task is cancelled.

    Cancels a connected consumer through the public communicator task and verifies cancellation is
    preserved while the consumer's unconditional lifecycle cleanup removes its subscription.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If cancellation is swallowed or group membership survives.
    """
    account_id = uuid4()
    communicator, connected, _ = await connect_notification_socket(account_id=account_id)
    group = notification_group_name(account_id)
    client = open_channel_instance_client()

    try:
        assert connected
        assert await group_subscriber_count(client, group) == 1

        communicator.future.cancel()
        with pytest.raises(asyncio.CancelledError):
            await communicator.future

        deadline = asyncio.get_running_loop().time() + RECEIVE_TIMEOUT_SECONDS
        while await group_subscriber_count(client, group) != 0:
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("notification group subscription survived cancellation")
            await asyncio.sleep(0.01)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cancellation_during_group_join_cleans_local_and_remote_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discard group state when cancellation lands inside group addition.

    Blocks the configured layer only after it records local membership and confirms the remote
    subscription, then cancels the consumer and observes exact cleanup in both places.

    Arguments:
        monkeypatch: Fixture holding the configured group-add await open.

    Returns:
        None.

    Raises:
        AssertionError: If cancellation is swallowed or either membership survives.
    """
    account_id = uuid4()
    group = notification_group_name(account_id)
    outer_layer = cast("ConfirmedRedisPubSubChannelLayer", get_channel_layer())
    loop_layer = outer_layer._get_layer()  # noqa: SLF001
    original_group_add = ConfirmingLoopLayer.group_add
    original_group_discard = ConfirmingLoopLayer.group_discard
    group_add_recorded = asyncio.Event()
    release_group_add = asyncio.Event()
    discard_calls: list[tuple[str, str]] = []

    async def group_add_then_block(
        layer: ConfirmingLoopLayer,
        group_name: str,
        channel_name: str,
    ) -> None:
        """Hold group addition after both observable memberships exist.

        Runs the configured implementation first, then keeps the consumer inside its cancellable
        join await until the test releases it.

        Arguments:
            layer: Configured per-loop channel layer.
            group_name: Notification group being joined.
            channel_name: Consumer-specific channel being added.

        Returns:
            None.
        """
        await original_group_add(layer, group_name, channel_name)
        group_add_recorded.set()
        await release_group_add.wait()

    async def record_group_discard(
        layer: ConfirmingLoopLayer,
        group_name: str,
        channel_name: str,
    ) -> None:
        """Record and execute one configured-layer group discard.

        Preserves the real local and remote cleanup while exposing the exact number of discard
        attempts made by the consumer lifecycle.

        Arguments:
            layer: Configured per-loop channel layer.
            group_name: Notification group being left.
            channel_name: Consumer-specific channel being removed.

        Returns:
            None.
        """
        discard_calls.append((group_name, channel_name))
        await original_group_discard(layer, group_name, channel_name)

    monkeypatch.setattr(ConfirmingLoopLayer, "group_add", group_add_then_block)
    monkeypatch.setattr(ConfirmingLoopLayer, "group_discard", record_group_discard)
    communicator = WebsocketCommunicator(
        application,
        ROUTE,
        headers=[(b"host", b"localhost"), (b"origin", ALLOWED_ORIGIN)],
    )
    communicator.scope["user"] = SimpleNamespace(pk=account_id)
    connect_task = asyncio.create_task(communicator.connect())
    client = open_channel_instance_client()
    group_channel = f"{loop_layer.prefix}__group__{group}"
    recorded_channels: set[str] = set()
    remote_after_cancel = -1
    local_after_cancel = -1

    try:
        await asyncio.wait_for(group_add_recorded.wait(), timeout=RECEIVE_TIMEOUT_SECONDS)
        recorded_channels = set(loop_layer.groups[group_channel])
        assert len(recorded_channels) == 1
        assert await group_subscriber_count(client, group) == 1

        communicator.future.cancel()
        with pytest.raises(asyncio.CancelledError):
            await communicator.future
        with pytest.raises(asyncio.CancelledError):
            await connect_task

        remote_after_cancel = await group_subscriber_count(client, group)
        local_after_cancel = len(loop_layer.groups.get(group_channel, set()))
    finally:
        release_group_add.set()
        for channel_name in set(loop_layer.groups.get(group_channel, set())):
            await original_group_discard(loop_layer, group, channel_name)
        await client.aclose()

    assert discard_calls == [(group, recorded_channels.pop())]
    assert local_after_cancel == 0
    assert remote_after_cancel == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cancellation_point", "local_members_before_cancel"),
    [
        ("before_local_removal", 1),
        ("during_remote_unsubscribe", 0),
    ],
)
async def test_cancellation_during_group_discard_completes_cleanup(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
    cancellation_point: str,
    local_members_before_cancel: int,
) -> None:
    """Finish one discard before propagating cancellation from cleanup.

    Blocks the configured discard before local removal or during its remote unsubscribe, then
    proves cancellation waits for one completed cleanup with no local or Valkey membership left.

    Arguments:
        monkeypatch: Fixture controlling the selected discard await point.
        cancellation_point: Stage at which the consumer task is cancelled.
        local_members_before_cancel: Expected local membership at that stage.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup is duplicated, interrupted, or leaves membership behind.
    """
    account_id = uuid4()
    group = notification_group_name(account_id)
    communicator, connected, _ = await connect_notification_socket(account_id=account_id)
    outer_layer = cast("ConfirmedRedisPubSubChannelLayer", get_channel_layer())
    loop_layer = outer_layer._get_layer()  # noqa: SLF001
    group_channel = f"{loop_layer.prefix}__group__{group}"
    channel_name = next(iter(loop_layer.groups[group_channel]))
    shard = loop_layer._get_shard(group_channel)  # noqa: SLF001
    original_group_discard = ConfirmingLoopLayer.group_discard
    original_unsubscribe = ConfirmingShardConnection.unsubscribe
    discard_started = asyncio.Event()
    release_discard = asyncio.Event()
    discard_completed = asyncio.Event()
    discard_calls: list[tuple[str, str]] = []
    client = open_channel_instance_client()
    remote_after_cancel = -1
    local_after_cancel = -1

    async def controlled_group_discard(
        layer: ConfirmingLoopLayer,
        group_name: str,
        member_channel: str,
    ) -> None:
        """Control cleanup before local membership removal.

        Holds the selected discard boundary while retaining the configured implementation for the
        eventual cleanup.

        Arguments:
            layer: Configured per-loop channel layer.
            group_name: Notification group being left.
            member_channel: Consumer-specific channel being removed.

        Returns:
            None.
        """
        discard_calls.append((group_name, member_channel))
        if cancellation_point == "before_local_removal":
            discard_started.set()
            await release_discard.wait()
        await original_group_discard(layer, group_name, member_channel)
        discard_completed.set()

    async def controlled_unsubscribe(
        connection: ConfirmingShardConnection,
        channel: str,
    ) -> None:
        """Control cleanup after local membership removal.

        Holds the shard after local removal while retaining its real unsubscribe for the eventual
        remote cleanup.

        Arguments:
            connection: Configured shard connection removing its subscription.
            channel: Internal group channel being unsubscribed.

        Returns:
            None.
        """
        if cancellation_point == "during_remote_unsubscribe" and channel == group_channel:
            discard_started.set()
            await release_discard.wait()
        await original_unsubscribe(connection, channel)

    assert connected
    monkeypatch.setattr(ConfirmingLoopLayer, "group_discard", controlled_group_discard)
    monkeypatch.setattr(ConfirmingShardConnection, "unsubscribe", controlled_unsubscribe)

    try:
        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(discard_started.wait(), timeout=RECEIVE_TIMEOUT_SECONDS)
        assert len(loop_layer.groups.get(group_channel, set())) == local_members_before_cancel
        assert await group_subscriber_count(client, group) == 1

        communicator.future.cancel()
        release_discard.set()
        with pytest.raises(asyncio.CancelledError):
            await communicator.future

        remote_after_cancel = await group_subscriber_count(client, group)
        local_after_cancel = len(loop_layer.groups.get(group_channel, set()))
    finally:
        release_discard.set()
        await original_group_discard(loop_layer, group, channel_name)
        await original_unsubscribe(shard, group_channel)
        await client.aclose()

    assert discard_calls == [(group, channel_name)]
    assert discard_completed.is_set()
    assert local_after_cancel == 0
    assert remote_after_cancel == 0
