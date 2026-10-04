"""Unit tests for the server entry points.

Covers the ASGI and WSGI application objects, confirming each module builds the handler type its
protocol requires so a misconfigured entry point fails here rather than at deployment.
"""

import importlib
import os
import subprocess
import sys
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

import pytest
from channels.routing import ProtocolTypeRouter
from django.core.handlers.asgi import ASGIHandler
from django.core.handlers.wsgi import WSGIHandler
from django.http import Http404
from django.test import RequestFactory, override_settings

from config import asgi, routing, wsgi

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


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
    assert asgi.application.application_mapping["http"] is asgi.http_application


@pytest.mark.unit
def test_documentation_flag_wraps_the_existing_http_stack_for_static_files() -> None:
    """Add static interception outside the established application wrappers.

    Reloads the entry point with documentation enabled and verifies the static handler delegates
    non-static traffic to the already composed logging, throttle, body-limit, and Django stack.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If static interception is absent or replaces the HTTP application.
    """
    try:
        with override_settings(API_DOCUMENTATION_ENABLED=True):
            reloaded = importlib.reload(asgi)

            assert isinstance(reloaded.http_application, asgi.DocumentationStaticFilesHandler)
            assert reloaded.http_application.application is not reloaded.django_application
    finally:
        importlib.reload(asgi)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_documentation_static_handler_applies_browser_security() -> None:
    """Serve one sidecar resource with static and browser response semantics.

    Requests an installed asset directly from the handler and observes its content type,
    conditional-cache metadata, and project-owned security headers.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the handler omits static metadata or browser hardening.
    """
    handler = asgi.DocumentationStaticFilesHandler(asgi.django_application)
    request = RequestFactory().get("/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui.css")

    response = await handler.get_response_async(request)

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Content-Type"] == "text/css"
    assert "Last-Modified" in response.headers
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    response.close()


@pytest.mark.unit
def test_documentation_static_handler_hides_unsafe_finder_paths() -> None:
    """Return not found for a traversal attempt beneath the static prefix.

    Sends an unsafe relative finder path through the same handler method Uvicorn reaches and
    verifies it cannot escape the installed static resource roots or become an internal error.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an unsafe path resolves or raises a non-HTTP exception.
    """
    handler = asgi.DocumentationStaticFilesHandler(asgi.django_application)
    request = RequestFactory().get("/static/../manage.py")

    with pytest.raises(Http404):
        handler.serve(request)


@pytest.mark.unit
def test_asgi_cold_import_populates_apps_before_rest_framework_models() -> None:
    """Start the ASGI entry point in a fresh interpreter.

    Imports the production module before any test harness calls ``django.setup()``, reproducing
    Uvicorn worker startup and proving REST authentication models load only after app population.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a cold Uvicorn-style import fails.
    """
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.testing",
        "PYTHONPATH": f"{REPOSITORY_ROOT / 'backend' / 'src'}{os.pathsep}{REPOSITORY_ROOT}",
    }
    completed = subprocess.run(
        [sys.executable, "-c", "import config.asgi"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


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
    assert [str(pattern.pattern) for pattern in routing.websocket_urlpatterns] == [
        "ws/notifications/"
    ]
