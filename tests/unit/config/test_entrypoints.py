"""Unit tests for the server entry points.

Covers the ASGI and WSGI application objects, confirming each module builds the handler type its
protocol requires so a misconfigured entry point fails here rather than at deployment.
"""

import importlib
import os
from unittest.mock import patch

import pytest
from channels.routing import ProtocolTypeRouter
from django.core.handlers.asgi import ASGIHandler
from django.core.handlers.wsgi import WSGIHandler

from config import asgi, routing, wsgi


@pytest.mark.unit
def test_asgi_module_exposes_an_asgi_handler() -> None:
    """Build an ASGI application object.

    Confirms the HTTP branch of the entry point is a handler of the type an ASGI server expects,
    which is the object the platform serves ordinary requests through.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the exposed application is not an ASGI handler.
    """
    assert isinstance(asgi.django_application, ASGIHandler)


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


@pytest.mark.unit
@pytest.mark.parametrize("module_name", ["config.asgi", "config.wsgi"])
def test_each_entry_point_defaults_to_the_development_settings(module_name: str) -> None:
    """Select the development settings when none is named.

    Reloads the entry point with no settings module selected and confirms it names the development
    module, since the fallback is never exercised inside the suite, where the variable is always
    set already.

    Arguments:
        module_name: The entry point module to reload.

    Returns:
        None.

    Raises:
        AssertionError: If the fallback does not name the development settings module.
    """
    environment = dict(os.environ)
    environment.pop("DJANGO_SETTINGS_MODULE", None)

    with patch.dict(os.environ, environment, clear=True):
        importlib.reload(importlib.import_module(module_name))

        assert os.environ["DJANGO_SETTINGS_MODULE"] == "config.settings.development"


@pytest.mark.unit
def test_the_asgi_application_dispatches_both_protocols() -> None:
    """Serve HTTP and WebSocket from one application.

    Confirms the entry point is a protocol router carrying both branches, because a plain HTTP
    handler would accept no upgrade and the WebSocket surface would fail at connection time rather
    than at startup.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either protocol is unrouted.
    """
    assert isinstance(asgi.application, ProtocolTypeRouter)
    assert set(asgi.application.application_mapping) == {"http", "websocket"}
    assert isinstance(asgi.django_application, ASGIHandler)


@pytest.mark.unit
def test_the_websocket_routing_table_has_one_home() -> None:
    """Keep the socket routes in one place.

    Confirms the WebSocket patterns come from the routing module rather than being built inside the
    entry point, so the table has a single home as routes are added.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the routing table is missing.
    """
    assert isinstance(routing.websocket_urlpatterns, list)
