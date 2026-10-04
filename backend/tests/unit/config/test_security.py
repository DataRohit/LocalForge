"""Unit tests for browser response security.

Exercises exact Origin parsing, allowlisting, hardening headers, credential reflection, and cache
variance without dispatching the application.
"""

from http import HTTPStatus
from ipaddress import ip_network
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from config.security import (
    RequestOrigin,
    allowed_cors_origin,
    apply_response_security,
    browser_security_middleware,
    public_cookie_security_middleware,
    request_origin_from_django,
    request_origin_from_scope,
    trusted_proxy_headers_middleware,
)

if TYPE_CHECKING:
    from django.http import HttpRequest


@pytest.mark.unit
def test_repeated_origins_are_ambiguous_but_still_vary_the_response() -> None:
    """Refuse repeated Origin reflection while preserving cache variance.

    Parses duplicate ASGI headers and applies security to prove no origin is reflected and the
    response still varies on Origin.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If duplicate origins are reflected or ignored for caching.
    """
    origin = request_origin_from_scope(
        {"headers": [(b"origin", b"http://localhost:8080"), (b"origin", b"http://evil.invalid")]}
    )
    response = apply_response_security(HttpResponse(), origin)

    assert origin == RequestOrigin(value=None, supplied=True)
    assert "Access-Control-Allow-Origin" not in response.headers
    assert response.headers["Vary"] == "Origin"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.unit
@override_settings(CORS_ALLOWED_ORIGINS=("http://localhost:8080",))
def test_cors_origin_requires_an_exact_allowlist_match() -> None:
    """Reflect one configured origin and refuse suffix or missing values.

    Compares exact literals so wildcard or suffix matching cannot enter the credentialed policy.
    Missing origins remain unreflected as well.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an unlisted origin is accepted.
    """
    assert allowed_cors_origin("http://localhost:8080") == "http://localhost:8080"
    assert allowed_cors_origin("http://localhost:8080.evil.invalid") is None
    assert allowed_cors_origin(None) is None


@pytest.mark.unit
def test_django_origin_parsing_uses_scope_then_safe_wsgi_fallback() -> None:
    """Preserve repeated-header detection and reject ambiguous WSGI metadata.

    Exercises the public request parser through an ASGI-backed request and a direct WSGI-style
    request carrying a comma-joined ambiguous origin.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If origin presence or safe reflection differs.
    """
    factory = RequestFactory()
    scoped = cast(
        "HttpRequest",
        SimpleNamespace(
            scope={
                "headers": [
                    (b"origin", b"http://localhost:8080"),
                    (b"origin", b"http://other.invalid"),
                ]
            },
            META={},
        ),
    )
    wsgi = factory.get("/", HTTP_ORIGIN="http://first.invalid,http://second.invalid")

    assert request_origin_from_django(scoped) == RequestOrigin(value=None, supplied=True)
    assert request_origin_from_django(wsgi) == RequestOrigin(value=None, supplied=True)


@pytest.mark.unit
@pytest.mark.asyncio
@override_settings(CORS_ALLOWED_ORIGINS=("http://localhost:8080",))
async def test_security_middleware_short_circuits_only_resolvable_api_preflight() -> None:
    """Answer one allowed API preflight and delegate a non-API OPTIONS request.

    Runs the public middleware callable and verifies route-aware method advertisement, bodyless
    success, origin reflection, and ordinary delegation outside the fixed API.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If preflight routing or response security differs.
    """
    delegated: list[str] = []

    async def inner(request: HttpRequest) -> HttpResponse:
        """Record one delegated request.

        Returns an ordinary response so the middleware can attach its common security headers.
        The path records whether preflight short-circuited.

        Arguments:
                request: Incoming request object.

        Returns:
            Successful response.

        Raises:
            None.
        """
        delegated.append(request.path)
        return HttpResponse(status=200)

    middleware = browser_security_middleware(inner)
    factory = RequestFactory()
    api_request = factory.options(
        "/api/v1/token/login/",
        HTTP_ORIGIN="http://localhost:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    api_response = await middleware(api_request)
    non_api_request = factory.options(
        "/health/",
        HTTP_ORIGIN="http://localhost:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
    )
    non_api_response = await middleware(non_api_request)

    assert api_response.status_code == HTTPStatus.NO_CONTENT
    assert cast("HttpResponse", api_response).content == b""
    assert api_response.headers["Access-Control-Allow-Origin"] == "http://localhost:8080"
    assert "POST" in api_response.headers["Access-Control-Allow-Methods"]
    assert delegated == ["/health/"]
    assert non_api_response.status_code == HTTPStatus.OK
    assert non_api_response.headers["Content-Security-Policy"]


@pytest.mark.unit
@pytest.mark.asyncio
@override_settings(
    ALLOWED_HOSTS=("localforge.datarohit.com", "localforge.localhost"),
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
)
async def test_public_cookie_security_is_host_and_transport_scoped() -> None:
    """Secure cookies on the public edge without breaking local HTTP administration.

    Sends equivalent cookie responses through the canonical public host and local host to prove
    only the trusted HTTPS edge receives the secure attribute.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If local cookies become secure or public cookies remain transportable.
    """

    async def inner(_request: HttpRequest) -> HttpResponse:
        """Return one response carrying a session cookie.

        Uses identical cookies for public and local requests to isolate transport policy.
        The response is deliberately minimal so only the cookie security attribute differs.

        Arguments:
            _request: Incoming request object.

        Returns:
            Response with a session cookie.
        """
        response = HttpResponse(status=200)
        response.set_cookie("sessionid", "value")
        return response

    middleware = public_cookie_security_middleware(inner)
    factory = RequestFactory()
    public_response = await middleware(
        factory.get(
            "/admin/",
            HTTP_HOST="localforge.datarohit.com",
            HTTP_X_FORWARDED_PROTO="https",
        )
    )
    local_response = await middleware(factory.get("/admin/", HTTP_HOST="localforge.localhost"))

    assert public_response.cookies["sessionid"]["secure"] is True
    assert not local_response.cookies["sessionid"]["secure"]


@pytest.mark.unit
@pytest.mark.asyncio
@override_settings(
    ALLOWED_HOSTS=("localforge.datarohit.com",),
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    TRUSTED_PROXY_NETWORKS=(ip_network("10.89.2.0/24"),),
)
async def test_forwarded_protocol_requires_trusted_proxy_peer() -> None:
    """Strip forwarded protocol claims from direct peers.

    Compares a Traefik edge address with a loopback direct address to prove only the configured
    proxy network can make Django treat the canonical request as secure.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an untrusted peer can spoof HTTPS with a forwarded header.
    """

    async def inner(request: HttpRequest) -> HttpResponse:
        """Report Django's secure-request decision.

        Returns a small body so trusted and untrusted peer outcomes can be compared directly.
        The request host remains canonical in both cases.

        Arguments:
            request: Incoming request object.

        Returns:
            Response containing the secure-request decision.
        """
        return HttpResponse("secure" if request.is_secure() else "plain")

    middleware = trusted_proxy_headers_middleware(inner)
    factory = RequestFactory()
    trusted = await middleware(
        factory.get(
            "/health/",
            HTTP_HOST="localforge.datarohit.com",
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="10.89.2.9",
        )
    )
    direct = await middleware(
        factory.get(
            "/health/",
            HTTP_HOST="localforge.datarohit.com",
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="127.0.0.1",
        )
    )
    malformed = await middleware(
        factory.get(
            "/health/",
            HTTP_HOST="localforge.datarohit.com",
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="not-an-ip",
        )
    )

    assert cast("HttpResponse", trusted).content == b"secure"
    assert cast("HttpResponse", direct).content == b"plain"
    assert cast("HttpResponse", malformed).content == b"plain"
