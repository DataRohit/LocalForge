"""Integration tests for offline API documentation.

Exercises both documented authentication schemes against the profile endpoint and loads each UI
plus every referenced sidecar asset over a real loopback HTTP server.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import uuid
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

import pytest
from rest_framework.authtoken.models import Token

from accounts.jwt_authentication import PrimaryRefreshToken

if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.test import Client

    from accounts.models import User

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[4]
SERVER_START_TIMEOUT_SECONDS = 20.0
REQUEST_TIMEOUT_SECONDS = 10.0
DOCUMENTATION_PAGE_COUNT = 2
DOCUMENTATION_PATHS: Final = (
    "/api/schema/",
    "/api/schema/swagger-ui/",
    "/api/schema/redoc/",
)
SIDECAR_ASSET_CONTENT_TYPES: Final = {
    "/static/drf_spectacular_sidecar/redoc/bundles/redoc.standalone.js": frozenset(
        {"application/javascript", "text/javascript"}
    ),
    "/static/drf_spectacular_sidecar/swagger-ui-dist/favicon-32x32.png": frozenset({"image/png"}),
    "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js": frozenset(
        {"application/javascript", "text/javascript"}
    ),
    "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-standalone-preset.js": (
        frozenset({"application/javascript", "text/javascript"})
    ),
    "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui.css": frozenset({"text/css"}),
}
EXPECTED_BROWSER_SECURITY_HEADERS: Final = {
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    ),
    "referrer-policy": "same-origin",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
}


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_documented_authentication_schemes_call_the_profile_endpoint(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Call one authenticated operation with each scheme Swagger presents.

    Creates one active account, sends the documented Token and Bearer authorization forms through
    the public profile route, and verifies both resolve the same authenticated representation.

    Arguments:
        client: Django client issuing the authenticated requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If either documented scheme cannot authenticate the request.
    """
    username = f"api-docs-auth-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        "Ticket37StrongPassword!",
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    access = PrimaryRefreshToken.for_user(account).access_token

    token_response = client.get(
        "/api/v1/users/me/",
        headers={"authorization": f"Token {token.key}"},
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    jwt_response = client.get(
        "/api/v1/users/me/",
        headers={"authorization": f"Bearer {access}"},
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )

    assert token_response.status_code == HTTPStatus.OK
    assert jwt_response.status_code == HTTPStatus.OK
    assert token_response.json()["username"] == username
    assert jwt_response.json()["username"] == username


def _available_loopback_port() -> int:
    """Reserve and release one currently available loopback port.

    Lets the real-server test avoid fixed-port collisions while accepting the small unavoidable
    bind race between releasing the probe socket and starting Django.

    Arguments:
        None.

    Returns:
        Available TCP port number selected by the operating system.

    Raises:
        OSError: If a loopback socket cannot be created or bound.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return cast("int", probe.getsockname()[1])


def _read_local_url(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Read one local documentation resource over HTTP.

    Uses the standard library client so the test observes the same network seam as a browser while
    keeping the dependency surface limited to the platform's existing runtime. Optional request
    headers exercise the static handler's conditional response behavior.

    Arguments:
        url: Loopback URL to request.
        headers: Optional HTTP request headers.

    Returns:
        HTTP status, response headers, and response body.

    Raises:
        AssertionError: If the URL does not target the loopback server.
        OSError: If the local server is unavailable.
    """
    parsed = urlsplit(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None
    connection = HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", parsed.path, headers={} if headers is None else dict(headers))
        response = connection.getresponse()
        return (
            response.status,
            {name.lower(): value for name, value in response.headers.items()},
            response.read(),
        )
    finally:
        connection.close()


def _start_uvicorn(*, documentation_enabled: bool) -> tuple[subprocess.Popen[str], str]:
    """Start the deployed ASGI application on one available loopback port.

    Runs the same Uvicorn target as the development entry point with an explicit documentation
    flag so enabled and headless startup behavior can be observed independently.

    Arguments:
        documentation_enabled: Whether documentation routes and static assets are exposed.

    Returns:
        Running server process and its loopback base URL.

    Raises:
        OSError: If the process cannot be started.
    """
    port = _available_loopback_port()
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "config.settings.testing"
    environment["DJANGO_API_DOCUMENTATION_ENABLED"] = str(documentation_enabled).lower()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(REPOSITORY_ROOT / "backend" / "src"), str(REPOSITORY_ROOT))
    )
    server = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "config.asgi:application",
            "--app-dir",
            str(REPOSITORY_ROOT / "backend" / "src"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    return server, f"http://127.0.0.1:{port}"


def _stop_server(server: subprocess.Popen[str]) -> None:
    """Stop one loopback Uvicorn process completely.

    Requests graceful termination and escalates after five seconds so failed assertions never
    leave an application process running in the shared development environment.

    Arguments:
        server: Running Uvicorn child process.

    Returns:
        None.
    """
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.timeout(30)
def test_documentation_pages_and_sidecar_assets_load_over_deployed_asgi() -> None:
    """Load both documentation applications and every asset over the deployed ASGI entry point.

    Starts Uvicorn against ``config.asgi:application`` with documentation explicitly enabled,
    rejects remote dependencies, and verifies safe static responses and conditional caching.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the server fails, a remote reference appears, or a local asset fails.
    """
    server, base_url = _start_uvicorn(documentation_enabled=True)

    try:
        deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
        while True:
            try:
                schema_status, schema_headers, _schema = _read_local_url(f"{base_url}/api/schema/")
                break
            except OSError:
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

        pages = {route: _read_local_url(f"{base_url}{route}") for route in DOCUMENTATION_PATHS[1:]}
        markup = [
            body.decode() for status, _headers, body in pages.values() if status == HTTPStatus.OK
        ]
        assets = sorted(
            {value for html in markup for value in re.findall(r'(?:src|href)="([^"]+)"', html)}
        )
        asset_responses = {asset: _read_local_url(f"{base_url}{asset}") for asset in assets}

        assert schema_status == HTTPStatus.OK
        assert schema_headers["content-type"] == "application/vnd.oai.openapi; charset=utf-8"
        assert len(markup) == DOCUMENTATION_PAGE_COUNT
        assert assets == sorted(SIDECAR_ASSET_CONTENT_TYPES)
        assert all(
            remote_reference not in html
            for html in markup
            for remote_reference in ("http://", "https://", 'src="//', 'href="//')
        )
        for asset, (status, headers, body) in asset_responses.items():
            assert status == HTTPStatus.OK
            assert headers["content-type"] in SIDECAR_ASSET_CONTENT_TYPES[asset]
            assert "last-modified" in headers
            assert body
            assert {
                name: headers[name] for name in EXPECTED_BROWSER_SECURITY_HEADERS
            } == EXPECTED_BROWSER_SECURITY_HEADERS
            cached_status, _cached_headers, cached_body = _read_local_url(
                f"{base_url}{asset}",
                headers={"If-Modified-Since": headers["last-modified"]},
            )
            assert cached_status == HTTPStatus.NOT_MODIFIED
            assert cached_body == b""
        assert _read_local_url(f"{base_url}/static/unknown.js")[0] == HTTPStatus.NOT_FOUND
        assert _read_local_url(f"{base_url}/static/%2e%2e/manage.py")[0] == HTTPStatus.NOT_FOUND
    finally:
        _stop_server(server)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.timeout(30)
def test_headless_asgi_excludes_documentation_routes_and_static_assets() -> None:
    """Keep testing startup free of documentation routes and static interception.

    Starts the deployed ASGI application with the testing flag value and verifies every
    documentation URL plus a known sidecar resource resolves as an ordinary not-found response.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any optional documentation resource remains exposed.
    """
    server, base_url = _start_uvicorn(documentation_enabled=False)

    try:
        deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
        while True:
            try:
                schema_response = _read_local_url(f"{base_url}{DOCUMENTATION_PATHS[0]}")
                break
            except OSError:
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

        responses = {
            path: _read_local_url(f"{base_url}{path}")
            for path in (
                *DOCUMENTATION_PATHS,
                next(iter(SIDECAR_ASSET_CONTENT_TYPES)),
            )
        }

        assert schema_response[0] == HTTPStatus.NOT_FOUND
        assert {path: response[0] for path, response in responses.items()} == dict.fromkeys(
            responses,
            HTTPStatus.NOT_FOUND,
        )
    finally:
        _stop_server(server)
