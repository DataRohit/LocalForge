"""Unit tests for WebSocket routing composition.

Verifies the notification path and complete middleware wrapper exist in the dedicated routing
module rather than the ASGI entry point.
"""

import pytest

from config.routing import websocket_application, websocket_urlpatterns
from notifications.websocket import WebSocketFailureBoundary


@pytest.mark.unit
def test_websocket_route_table_and_failure_boundary_are_fixed() -> None:
    """Keep the single notification route behind the outer failure boundary.

    Compares the route path and name and verifies the composed application starts with the
    contract-owning containment wrapper.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If routing or containment changes.
    """
    assert [(str(route.pattern), route.name) for route in websocket_urlpatterns] == [
        ("ws/notifications/", "notification-websocket")
    ]
    assert isinstance(websocket_application, WebSocketFailureBoundary)
