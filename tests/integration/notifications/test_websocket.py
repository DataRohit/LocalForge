"""Public-seam tests for the notification WebSocket protocol.

Connects through the deployed ASGI application so routing, origin admission, frame decoding,
envelope validation, error responses, and disconnect cleanup are observed together.
"""

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.testing import WebsocketCommunicator
from django.urls import path

from notifications.websocket import ExactWebSocketOriginValidator, NotificationConsumer

ROUTE = "/ws/notifications/"
ALLOWED_ORIGIN = b"http://localhost:8080"
RECEIVE_TIMEOUT_SECONDS = 10
MALFORMED_FRAME_CLOSE_CODE = 4400
PERMISSION_DENIED_CLOSE_CODE = 4406
TEST_ACCOUNT_ID = UUID("018f22e2-7d42-7f74-9d8a-123456789abc")

FrameSender = Callable[[WebsocketCommunicator], Awaitable[None]]
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

application = ProtocolTypeRouter(
    {
        "websocket": ExactWebSocketOriginValidator(
            URLRouter([path("ws/notifications/", NotificationConsumer.as_asgi())])
        )
    }
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.services("postgres", "valkey-channels"),
    pytest.mark.django_db(databases=["default", "replica"], transaction=True),
]


async def connect_notification_socket(
    *,
    origin: bytes = ALLOWED_ORIGIN,
    path: str = ROUTE,
    headers: list[tuple[bytes, bytes]] | None = None,
    subprotocols: list[str] | None = None,
) -> tuple[WebsocketCommunicator, bool, str | int | None]:
    """Open the notification socket through the project ASGI application.

    Supplies one allowed browser origin by default while permitting tests to replace the path or
    complete header list, so every admission case crosses the same deployed routing seam.

    Arguments:
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
    communicator.scope["user"] = SimpleNamespace(pk=TEST_ACCOUNT_ID)
    connected, detail = cast("tuple[bool, str | int | None]", await communicator.connect())

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
    assert output["code"] == PERMISSION_DENIED_CLOSE_CODE
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
            assert response["payload"]["code"] == "unknown_message_type"
            assert response["payload"]["message"] == "The message type is not supported."
            assert response["payload"]["details"] == {
                "type": ["No handler is registered for this message type."]
            }
            UUID(response["payload"]["request_id"])
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
    assert output["code"] == MALFORMED_FRAME_CLOSE_CODE
    await communicator.disconnect()
