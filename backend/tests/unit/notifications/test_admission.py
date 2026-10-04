"""Unit tests for WebSocket connection admission middleware.

Verifies cancellation remains cancellation at the ASGI boundary without touching the shared store
or converting caller shutdown into a client-visible server failure.
"""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from notifications import admission
from notifications.admission import WebSocketConnectionAdmissionMiddleware

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_connection_admission_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate caller cancellation without entering or closing the inner application.

    Replaces shared admission with cancellation and observes the middleware's public ASGI seam,
    proving shutdown is not mislabeled as server failure.

    Arguments:
        monkeypatch: Fixture replacing shared admission.

    Returns:
        None.

    Raises:
        AssertionError: If cancellation is swallowed or any output is emitted.
    """
    inner = AsyncMock()
    receive = AsyncMock()
    send = AsyncMock()
    scope = cast(
        "Scope",
        cast(
            "object",
            {
                "type": "websocket",
                "user": SimpleNamespace(pk=UUID(int=1)),
                "connection_request_id": "00000000-0000-4000-8000-000000000000",
            },
        ),
    )
    monkeypatch.setattr(
        admission,
        "admit_websocket_connection",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    middleware = WebSocketConnectionAdmissionMiddleware(inner)

    with pytest.raises(asyncio.CancelledError):
        await middleware(
            scope,
            cast("ASGIReceiveCallable", receive),
            cast("ASGISendCallable", send),
        )

    inner.assert_not_awaited()
    receive.assert_not_awaited()
    send.assert_not_awaited()
