"""Notification WebSocket protocol and connection lifecycle.

Validates browser origins and the exact JSON envelope before dispatch, containing malformed input
and unsupported message types behind one asynchronous consumer interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, cast, override

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.http.request import split_domain_port, validate_host

from config.logs import REQUEST_ID_META_KEY, request_identifier
from config.security import allowed_cors_origin, request_origin_from_scope
from notifications.delivery import notification_group_name

if TYPE_CHECKING:
    from asgiref.typing import (
        ASGI3Application,
        ASGIReceiveCallable,
        ASGIReceiveEvent,
        ASGISendCallable,
        ASGISendEvent,
        Scope,
    )

    from accounts.models import User
    from notifications.delivery import NotificationChannelEvent

from notifications.protocol import (
    WebSocketOutcome,
    close_rejected_handshake,
    websocket_error_frame,
)

logger = logging.getLogger(__name__)

ENVELOPE_FIELDS = frozenset({"type", "payload"})
JSON_FRAME_ERRORS = (json.JSONDecodeError, RecursionError, ValueError)


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
            await close_rejected_handshake(receive, send, WebSocketOutcome.PERMISSION_DENIED)
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
            await close_rejected_handshake(receive, send, WebSocketOutcome.PERMISSION_DENIED)
            return

        await self.application(scope, receive, send)


class WebSocketFailureBoundary:
    """Contain unexpected WebSocket failures with correlation and a stable close code.

    Wraps the complete admission stack, owns one request identifier for the connection, propagates
    cancellation, and converts other exceptions into a secret-free log plus the server-error close.

    Attributes:
        application: Inner WebSocket admission and consumer application.

    Members:
        __call__: Correlate, delegate, contain failures, and restore context.
    """

    def __init__(self, application: ASGI3Application) -> None:
        """Store the protected WebSocket application.

        Keeps correlation and containment outside every admission layer and consumer so no
        application failure bypasses the same boundary.

        Arguments:
            application: Inner application whose failures are contained.

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
        """Run one connection inside the correlated failure boundary.

        Tracks handshake and disconnect state so an unexpected failure receives the server-error
        close only while a client can still observe it, without turning cancellation into failure.

        Arguments:
            scope: Incoming ASGI WebSocket scope.
            receive: Callable yielding connection events.
            send: Callable emitting connection events.

        Returns:
            None.

        Raises:
            CancelledError: If the caller cancels the connection application task.
        """
        identifier = str(uuid.uuid4())
        correlated_scope = dict(scope)
        correlated_scope["connection_request_id"] = identifier
        identifier_token = request_identifier.set(identifier)
        accepted = False
        closed = False
        connect_received = False
        disconnected = False

        async def tracked_receive() -> ASGIReceiveEvent:
            """Track whether connection or disconnect input has arrived.

            Delegates the next event unchanged while retaining only lifecycle state needed to decide
            whether a later server-error close can still be delivered.

            Arguments:
                None.

            Returns:
                Next ASGI receive event.
            """
            nonlocal connect_received, disconnected
            event = await receive()
            event_type = event["type"]
            connect_received = connect_received or event_type == "websocket.connect"
            disconnected = disconnected or event_type == "websocket.disconnect"

            return event

        async def tracked_send(event: ASGISendEvent) -> None:
            """Track accepted and closed output before delegating it.

            Records lifecycle state before sending so even a transport error leaves containment with
            the most conservative view of what the client may have observed.

            Arguments:
                event: ASGI WebSocket output event.

            Returns:
                None.
            """
            nonlocal accepted, closed
            accepted = accepted or event["type"] == "websocket.accept"
            closed = closed or event["type"] == "websocket.close"
            await send(event)

        try:
            await self.application(
                cast("Scope", correlated_scope),
                cast("ASGIReceiveCallable", tracked_receive),
                tracked_send,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.log(
                logging.ERROR,
                "websocket connection failed",
                extra={REQUEST_ID_META_KEY: identifier},
            )
            if not closed and not disconnected:
                try:
                    if not connect_received:
                        await tracked_receive()
                    if not accepted:
                        await tracked_send(cast("ASGISendEvent", {"type": "websocket.accept"}))
                    await tracked_send(
                        cast(
                            "ASGISendEvent",
                            {
                                "type": "websocket.close",
                                "code": WebSocketOutcome.SERVER_ERROR.required_close_code(),
                            },
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.log(
                        logging.ERROR,
                        "websocket server-error close could not be sent",
                        extra={REQUEST_ID_META_KEY: identifier},
                    )
        finally:
            request_identifier.reset(identifier_token)


class NotificationConsumer(AsyncWebsocketConsumer):  # type: ignore[misc]
    """Serve the notification protocol over one asynchronous WebSocket connection.

    Inherits from ``AsyncWebsocketConsumer`` and owns correlation, exact frame validation,
    recoverable error responses, and cleanup shared by every notification connection.

    Attributes:
        connection_request_id: Correlation identifier carried by error frames.
        notification_group_joined: Whether this connection currently owns group membership.
        notification_group: Server-selected group for the authenticated account.

    Members:
        __call__: Guarantee group cleanup on every lifecycle exit.
        connect: Correlate and accept the connection.
        disconnect: Remove group membership when the connection ends.
        notification_message: Deliver one user-targeted notification.
        receive: Validate and dispatch one incoming frame.
    """

    connection_request_id: str
    notification_group: str
    notification_group_joined: bool

    @override
    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Run one consumer instance with unconditional membership cleanup.

        Delegates the normal Channels lifecycle and discards any joined group when disconnect,
        cancellation, or an unexpected handler exception ends the application task.

        Arguments:
            scope: Correlated authenticated WebSocket scope.
            receive: Callable yielding connection events.
            send: Callable emitting connection events.

        Returns:
            None.
        """
        self.notification_group_joined = False
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._leave_notification_group()

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
        self.connection_request_id = cast("str", self.scope["connection_request_id"])
        user = cast("User", self.scope["user"])
        self.notification_group = notification_group_name(user.pk)
        self.notification_group_joined = True
        await self.channel_layer.group_add(self.notification_group, self.channel_name)
        accepted_subprotocol = self.scope.get("accepted_subprotocol")
        await self.accept(
            subprotocol=accepted_subprotocol if isinstance(accepted_subprotocol, str) else None
        )

    @override
    async def disconnect(self, code: int) -> None:
        """Restore the prior correlation context after disconnect.

        Removes the server-selected membership without interpreting the peer's close code, leaving
        no delivery state behind for another connection.

        Arguments:
            code: Close code supplied by the WebSocket transport.

        Returns:
            None.
        """
        del code
        await self._leave_notification_group()

    async def _leave_notification_group(self) -> None:
        """Discard this connection's server-selected group exactly once.

        Shields one discard to completion before relinquishing cleanup ownership or propagating
        cancellation, while keeping final ``__call__`` cleanup harmless after a successful leave.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            CancelledError: After an in-flight discard completes successfully.
        """
        if not self.notification_group_joined:
            return

        discard_task = asyncio.create_task(
            self.channel_layer.group_discard(
                self.notification_group,
                self.channel_name,
            )
        )
        cancellation: asyncio.CancelledError | None = None
        while not discard_task.done():
            try:
                await asyncio.shield(discard_task)
            except asyncio.CancelledError as error:
                cancellation = error

        discard_task.result()
        self.notification_group_joined = False
        if cancellation is not None:
            raise cancellation

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
        try:
            message_size = (
                len(bytes_data)
                if bytes_data is not None
                else len(text_data.encode("utf-8"))
                if text_data is not None
                else 0
            )
        except UnicodeEncodeError:
            await self.close(code=WebSocketOutcome.MALFORMED_FRAME.required_close_code())
            return

        if message_size > settings.WEBSOCKET_APPLICATION_MAX_MESSAGE_BYTES:
            await self.close(code=WebSocketOutcome.FRAME_TOO_LARGE.required_close_code())
            return

        if bytes_data is not None or text_data is None:
            await self.close(code=WebSocketOutcome.MALFORMED_FRAME.required_close_code())
            return

        try:
            decoded = json.loads(
                text_data,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except JSON_FRAME_ERRORS:
            await self.close(code=WebSocketOutcome.MALFORMED_FRAME.required_close_code())
            return

        if not self._is_valid_envelope(decoded):
            await self.close(code=WebSocketOutcome.MALFORMED_FRAME.required_close_code())
            return

        envelope = cast("dict[str, Any]", decoded)
        outcome = (
            WebSocketOutcome.PERMISSION_DENIED
            if envelope["type"] == "subscribe"
            else WebSocketOutcome.UNKNOWN_MESSAGE_TYPE
        )
        await self._send_error(outcome)

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

    async def _send_error(self, outcome: WebSocketOutcome) -> None:
        """Return one recoverable application error response.

        Renders the central error contract and keeps the socket open, allowing a client to correct
        authorization or message type without reconnecting.

        Arguments:
            outcome: Recoverable failure contract member to send.

        Returns:
            None.

        Raises:
            ValueError: If the selected outcome does not support an error frame.
        """
        await self.send(
            text_data=json.dumps(
                websocket_error_frame(
                    outcome,
                    self.connection_request_id,
                ),
                separators=(",", ":"),
            )
        )
