"""Unit tests for the outer WebSocket failure boundary.

Exercises handshake-state branches that are difficult to induce through a real consumer while
keeping cancellation, existing closure, disconnect, and failed close delivery deterministic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from notifications.protocol import WebSocketOutcome
from notifications.websocket import WebSocketFailureBoundary

if TYPE_CHECKING:
    from asgiref.typing import (
        ASGI3Application,
        ASGIReceiveCallable,
        ASGISendCallable,
        ASGISendEvent,
        Scope,
    )

pytestmark = pytest.mark.unit


def websocket_scope() -> Scope:
    """Build the minimum scope consumed by the failure boundary.

    Supplies only transport type, path, and headers because the boundary adds correlation before
    delegating and the synthetic applications require no authentication state.

    Arguments:
        None.

    Returns:
        Synthetic WebSocket scope.
    """
    return cast(
        "Scope",
        cast(
            "object",
            {
                "type": "websocket",
                "path": "/ws/notifications/",
                "headers": [],
            },
        ),
    )


@pytest.mark.asyncio
async def test_failure_before_receive_accepts_and_closes_with_server_error() -> None:
    """Deliver the private server-error code when inner startup raises immediately.

    Confirms the boundary first consumes the pending connect event, then performs the minimum
    accepted handshake required for a client to observe the private close code.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If ordering or close code differs.
    """

    async def fail(
        _scope: Scope,
        _receive: ASGIReceiveCallable,
        _send: ASGISendCallable,
    ) -> None:
        """Raise before reading or sending any ASGI event.

        Forces containment to perform both initial receive and minimum accepted
        close handling.

        Arguments:
            _scope: Incoming scope.
            _receive: Incoming event callable.
            _send: Outgoing event callable.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError

    receive = AsyncMock(return_value={"type": "websocket.connect"})
    send = AsyncMock()
    boundary = WebSocketFailureBoundary(cast("ASGI3Application", fail))

    await boundary(
        websocket_scope(),
        cast("ASGIReceiveCallable", receive),
        cast("ASGISendCallable", send),
    )

    assert [call.args[0]["type"] for call in send.await_args_list] == [
        "websocket.accept",
        "websocket.close",
    ]
    assert send.await_args_list[-1].args[0]["code"] == (
        WebSocketOutcome.SERVER_ERROR.required_close_code()
    )


@pytest.mark.asyncio
async def test_failure_after_existing_close_sends_no_second_close() -> None:
    """Preserve a close frame already emitted before an inner exception.

    Exercises the tracked closed branch so containment logs the failure without appending a second
    protocol close that could race or replace the original outcome.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the boundary emits additional output.
    """

    async def close_then_fail(
        _scope: Scope,
        _receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Emit one close and then raise.

        Marks the connection closed before failure so the boundary must preserve
        existing output.

        Arguments:
            _scope: Incoming scope.
            _receive: Incoming event callable.
            send: Outgoing event callable.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always after the close.
        """
        await send(
            cast(
                "ASGISendEvent",
                {
                    "type": "websocket.close",
                    "code": WebSocketOutcome.MALFORMED_FRAME.required_close_code(),
                },
            )
        )
        raise RuntimeError

    receive = AsyncMock()
    send = AsyncMock()
    boundary = WebSocketFailureBoundary(cast("ASGI3Application", close_then_fail))

    await boundary(
        websocket_scope(),
        cast("ASGIReceiveCallable", receive),
        cast("ASGISendCallable", send),
    )

    assert send.await_count == 1
    receive.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_after_peer_disconnect_sends_no_output() -> None:
    """Avoid sending a server-error close after the peer has disconnected.

    Exercises the tracked disconnect branch, where no client remains to receive another event and
    attempting one would create a secondary transport failure.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the boundary emits output after disconnect.
    """

    async def disconnect_then_fail(
        _scope: Scope,
        receive: ASGIReceiveCallable,
        _send: ASGISendCallable,
    ) -> None:
        """Consume peer disconnect and then raise.

        Marks the client unavailable before failure so containment cannot attempt
        any response.

        Arguments:
            _scope: Incoming scope.
            receive: Incoming event callable.
            _send: Outgoing event callable.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always after disconnect.
        """
        await receive()
        raise RuntimeError

    receive = AsyncMock(return_value={"type": "websocket.disconnect", "code": 1000})
    send = AsyncMock()
    boundary = WebSocketFailureBoundary(cast("ASGI3Application", disconnect_then_fail))

    await boundary(
        websocket_scope(),
        cast("ASGIReceiveCallable", receive),
        cast("ASGISendCallable", send),
    )

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_server_error_close_is_logged_and_contained(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contain a transport failure while sending the final server-error close.

    Allows the synthetic accept but raises on close, proving the boundary records the secondary
    failure without leaking it back to the ASGI server.

    Arguments:
        caplog: Fixture capturing the fixed secondary failure record.

    Returns:
        None.

    Raises:
        AssertionError: If close delivery escapes or is not logged.
    """

    async def fail(
        _scope: Scope,
        _receive: ASGIReceiveCallable,
        _send: ASGISendCallable,
    ) -> None:
        """Raise before handshake output.

        Drives the same pre-handshake containment branch used by the close-delivery
        failure test.

        Arguments:
            _scope: Incoming scope.
            _receive: Incoming event callable.
            _send: Outgoing event callable.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError

    async def fail_close(event: ASGISendEvent) -> None:
        """Accept successfully and fail the following close.

        Models a transport that accepts the handshake but becomes unavailable
        before final close.

        Arguments:
            event: Outgoing ASGI event.

        Returns:
            None.

        Raises:
            RuntimeError: If the event is the server-error close.
        """
        if event["type"] == "websocket.close":
            raise RuntimeError

    receive = AsyncMock(return_value={"type": "websocket.connect"})
    boundary = WebSocketFailureBoundary(cast("ASGI3Application", fail))

    await boundary(
        websocket_scope(),
        cast("ASGIReceiveCallable", receive),
        cast("ASGISendCallable", fail_close),
    )

    assert "websocket server-error close could not be sent" in caplog.text
