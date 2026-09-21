"""Unit tests for the ASGI entry point.

Verifies the deployed application exposes HTTP and WebSocket branches while retaining the project
HTTP wrapper stack.
"""

import pytest
from channels.routing import ProtocolTypeRouter
from django.core.handlers.asgi import ASGIHandler

from config import asgi


@pytest.mark.unit
def test_asgi_entry_point_routes_http_and_websocket_protocols() -> None:
    """Expose both fixed transport branches from one application.

    Asserts the public entry object and its HTTP handler type without opening a socket.
    Both protocols must remain available from one server application.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either protocol branch disappears.
    """
    assert isinstance(asgi.application, ProtocolTypeRouter)
    assert set(asgi.application.application_mapping) == {"http", "websocket"}
    assert isinstance(asgi.django_application, ASGIHandler)
