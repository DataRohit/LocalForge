"""Notification WebSocket protocol and connection lifecycle.

Validates browser origins and the exact JSON envelope before dispatch, containing malformed input
and unsupported message types behind one asynchronous consumer interface.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any, cast, override

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.http.request import split_domain_port, validate_host

from config.logs import request_identifier
from config.security import allowed_cors_origin, request_origin_from_scope
from notifications.delivery import notification_group_name

if TYPE_CHECKING:
    from contextvars import Token

    from asgiref.typing import (
        ASGI3Application,
        ASGIReceiveCallable,
        ASGISendCallable,
        ASGISendEvent,
        Scope,
    )

    from accounts.models import User
    from notifications.delivery import NotificationChannelEvent

MALFORMED_FRAME_CLOSE_CODE = 4400
PERMISSION_DENIED_CLOSE_CODE = 4406
ENVELOPE_FIELDS = frozenset({"type", "payload"})
JSON_FRAME_ERRORS = (json.JSONDecodeError, RecursionError, ValueError)
UNKNOWN_MESSAGE_TYPE_CODE = "unknown_message_type"
UNKNOWN_MESSAGE_TYPE_MESSAGE = "The message type is not supported."
UNKNOWN_MESSAGE_TYPE_DETAILS = {
    "type": ["No handler is registered for this message type."],
}


def _reject_json_constant(value: str) -> None:
    """Reject a non-standard numeric token accepted by Python's decoder.

    Raises for NaN and infinity spellings so parsing follows the JSON standard rather than the
    decoder's JavaScript-compatible extension.

    Arguments:
        value: Non-standard token the decoder encountered.

    Returns:
        None.

    Raises:
        ValueError: Always, because the token is not valid JSON.
    """
    message = f"{value} is not valid JSON"
    raise ValueError(message)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object only when every member name is unique.

    Rejects ambiguous wire objects before dictionary construction can overwrite an earlier member,
    preserving the contract's exact-field guarantee for the envelope and nested payload objects.

    Arguments:
        pairs: Ordered member names and decoded values from one JSON object.

    Returns:
        Dictionary containing each unique member.

    Raises:
        ValueError: If any member name appears more than once.
    """
    decoded: dict[str, object] = {}
    for name, value in pairs:
        if name in decoded:
            message = f"duplicate JSON member {name!r}"
            raise ValueError(message)
        decoded[name] = value

    return decoded


async def close_rejected_handshake(
    receive: ASGIReceiveCallable,
    send: ASGISendCallable,
    code: int,
) -> None:
    """Complete only enough handshake to deliver an application close code.

    Waits for the connection event, accepts no protected application behavior, and closes
    immediately so a real WebSocket client can observe the documented private-use rejection code.

    Arguments:
        receive: Callable yielding the initial connection event.
        send: Callable emitting handshake and close events.
        code: Application close code the client must receive.

    Returns:
        None.
    """
    await receive()
    await send(cast("ASGISendEvent", {"type": "websocket.accept"}))
    await send(cast("ASGISendEvent", {"type": "websocket.close", "code": code}))


class ExactWebSocketOriginValidator:
    """Admit WebSocket handshakes from one exact configured browser origin.

    Wraps an ASGI application and reuses the project's origin parser and allowlist, rejecting
    missing, repeated, and unlisted values before a consumer can accept the connection.

    Attributes:
        application: Inner WebSocket router reached after origin admission.

    Members:
        __call__: Validate one connection scope and delegate or close it.
    """

    def __init__(self, application: ASGI3Application) -> None:
        """Store the WebSocket application protected by origin admission.

        Keeps policy separate from routing so every current and future socket receives the same
        exact-origin check without repeating it in each consumer.

        Arguments:
            application: Inner ASGI application to call after successful admission.

        Returns:
            None.
        """
        self.application = application

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Validate the connection origin before reaching the router.

        Requires one unambiguous configured origin and otherwise emits the protocol's authorization
        close code, preventing missing and duplicated headers from becoming implicit trust.

        Arguments:
            scope: Incoming ASGI WebSocket scope.
            receive: Callable yielding connection events.
            send: Callable emitting connection events.

        Returns:
            None.
        """
        origin = request_origin_from_scope(scope)
        if allowed_cors_origin(origin.value) is None:
            await close_rejected_handshake(receive, send, PERMISSION_DENIED_CLOSE_CODE)
            return

        await self.application(scope, receive, send)


class ExactWebSocketHostValidator:
    """Admit WebSocket handshakes addressed to one allowed Django host.

    Wraps an ASGI application and applies Django's host-pattern semantics to one unambiguous Host
    header before origin or credential processing can reach the protected router.

    Attributes:
        application: Inner WebSocket admission stack reached after host validation.

    Members:
        __call__: Validate one connection host and delegate or close it.
    """

    def __init__(self, application: ASGI3Application) -> None:
        """Store the WebSocket application protected by host admission.

        Keeps the host policy at one outer seam so every socket follows the same configured
        ``ALLOWED_HOSTS`` contract as HTTP without duplicating checks in consumers.

        Arguments:
            application: Inner ASGI application to call after successful admission.

        Returns:
            None.
        """
        self.application = application

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Validate the connection host before origin and authentication.

        Requires one syntactically valid Host header matching Django's configured allowlist and
        otherwise delivers the authorization close code without decoding a credential.

        Arguments:
            scope: Incoming ASGI WebSocket scope.
            receive: Callable yielding connection events.
            send: Callable emitting connection events.

        Returns:
            None.
        """
        values = [
            value
            for name, value in cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
            if name.lower() == b"host"
        ]
        host = values[0].decode("latin-1") if len(values) == 1 else ""
        domain, _ = split_domain_port(host.lower())
        allowed_hosts = cast("list[str]", settings.ALLOWED_HOSTS)
        if not domain or not validate_host(domain, allowed_hosts):
            await close_rejected_handshake(receive, send, PERMISSION_DENIED_CLOSE_CODE)
            return

        await self.application(scope, receive, send)


class NotificationConsumer(AsyncWebsocketConsumer):  # type: ignore[misc]
    """Serve the notification protocol over one asynchronous WebSocket connection.

    Inherits from ``AsyncWebsocketConsumer`` and owns correlation, exact frame validation,
    recoverable error responses, and cleanup shared by every notification connection.

    Attributes:
        connection_request_id: Correlation identifier carried by error frames.
        notification_group: Server-selected group for the authenticated account.
        request_identifier_token: Context token used to restore the previous correlation value.

    Members:
        connect: Correlate and accept the connection.
        disconnect: Restore correlation when the connection ends.
        notification_message: Deliver one user-targeted notification.
        receive: Validate and dispatch one incoming frame.
    """

    connection_request_id: str
    notification_group: str
    request_identifier_token: Token[str]

    @override
    async def connect(self) -> None:
        """Correlate and accept one notification connection.

        Creates correlation, derives membership only from the authenticated immutable account
        identifier, and completes the group join before accepting so immediate publication is safe.

        Arguments:
            None.

        Returns:
            None.
        """
        self.connection_request_id = str(uuid.uuid4())
        self.request_identifier_token = request_identifier.set(self.connection_request_id)
        user = cast("User", self.scope["user"])
        self.notification_group = notification_group_name(user.pk)
        await self.channel_layer.group_add(self.notification_group, self.channel_name)
        accepted_subprotocol = self.scope.get("accepted_subprotocol")
        await self.accept(
            subprotocol=accepted_subprotocol if isinstance(accepted_subprotocol, str) else None
        )

    @override
    async def disconnect(self, code: int) -> None:
        """Restore the prior correlation context after disconnect.

        Removes the server-selected membership before ending the connection-owned context, leaving
        neither delivery state nor a request identifier for another task sharing the process.

        Arguments:
            code: Close code supplied by the WebSocket transport.

        Returns:
            None.
        """
        del code
        try:
            await self.channel_layer.group_discard(
                self.notification_group,
                self.channel_name,
            )
        finally:
            request_identifier.reset(self.request_identifier_token)

    async def notification_message(self, event: NotificationChannelEvent) -> None:
        """Deliver one channel-layer notification through the public wire envelope.

        Maps the internal dispatch event to exactly the versioned notification shape and performs
        one socket send for each group delivery received by this connection.

        Arguments:
            event: User-targeted channel event from the synchronous publisher.

        Returns:
            None.

        Raises:
            None.
        """
        await self.send(
            text_data=json.dumps(
                {
                    "type": "notification",
                    "payload": {
                        "event": event["event"],
                        "data": event["data"],
                    },
                },
                allow_nan=False,
                separators=(",", ":"),
            )
        )

    @override
    async def receive(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,
        **kwargs: object,
    ) -> None:
        """Validate and dispatch one text JSON frame.

        Rejects binary data, invalid JSON, non-object values, and envelopes whose exact field or
        value types drift, while unsupported valid message types receive a recoverable error frame.

        Arguments:
            text_data: Decoded text frame, or ``None`` for a binary frame.
            bytes_data: Binary frame bytes, or ``None`` for a text frame.
            **kwargs: Additional values supplied by Channels.

        Returns:
            None.
        """
        del kwargs
        if bytes_data is not None or text_data is None:
            await self.close(code=MALFORMED_FRAME_CLOSE_CODE)
            return

        try:
            decoded = json.loads(
                text_data,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except JSON_FRAME_ERRORS:
            await self.close(code=MALFORMED_FRAME_CLOSE_CODE)
            return

        if not self._is_valid_envelope(decoded):
            await self.close(code=MALFORMED_FRAME_CLOSE_CODE)
            return

        await self._send_unknown_message_type()

    @staticmethod
    def _is_valid_envelope(decoded: object) -> bool:
        """Recognize the exact notification message envelope.

        Keeps structural validation in one predicate while leaving message-type dispatch separate,
        so adding a supported type cannot weaken the top-level protocol shape.

        Arguments:
            decoded: JSON value produced from the incoming text frame.

        Returns:
            True only for an object with string ``type`` and object ``payload`` fields.
        """
        if not isinstance(decoded, dict) or frozenset(decoded) != ENVELOPE_FIELDS:
            return False

        envelope = cast("dict[str, Any]", decoded)

        return isinstance(envelope["type"], str) and isinstance(envelope["payload"], dict)

    async def _send_unknown_message_type(self) -> None:
        """Return the recoverable unsupported-message response.

        Reuses the REST error payload fields and keeps the socket open, allowing a client to correct
        its next frame without reconnecting.

        Arguments:
            None.

        Returns:
            None.
        """
        await self.send(
            text_data=json.dumps(
                {
                    "type": "error",
                    "payload": {
                        "code": UNKNOWN_MESSAGE_TYPE_CODE,
                        "message": UNKNOWN_MESSAGE_TYPE_MESSAGE,
                        "details": UNKNOWN_MESSAGE_TYPE_DETAILS,
                        "request_id": self.connection_request_id,
                    },
                },
                separators=(",", ":"),
            )
        )
