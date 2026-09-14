"""Unit tests for the server entry points.

Covers the ASGI and WSGI application objects, confirming each module builds the handler type its
protocol requires so a misconfigured entry point fails here rather than at deployment.
"""

import importlib
import os
from unittest.mock import patch

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
