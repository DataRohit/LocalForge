"""Unit tests for the server entry points.

Covers the ASGI and WSGI application objects, confirming each module builds the handler type its
protocol requires so a misconfigured entry point fails here rather than at deployment.
"""

import pytest
from django.core.handlers.asgi import ASGIHandler
from django.core.handlers.wsgi import WSGIHandler

from config import asgi, wsgi


@pytest.mark.unit
def test_asgi_module_exposes_an_asgi_handler() -> None:
    """Build an ASGI application object.

    Confirms the ASGI entry point exposes a handler of the type an ASGI server expects, which is
    the object the platform serves WebSocket and HTTP traffic through.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the exposed application is not an ASGI handler.
    """
    assert isinstance(asgi.application, ASGIHandler)


@pytest.mark.unit
def test_wsgi_module_exposes_a_wsgi_handler() -> None:
    """Build a WSGI application object.

    Confirms the WSGI entry point exposes a handler of the type a WSGI server expects, retained
    for tooling that does not speak ASGI.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the exposed application is not a WSGI handler.
    """
    assert isinstance(wsgi.application, WSGIHandler)
