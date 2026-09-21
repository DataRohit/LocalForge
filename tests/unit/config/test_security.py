"""Unit tests for browser response security.

Exercises exact Origin parsing, allowlisting, hardening headers, credential reflection, and cache
variance without dispatching the application.
"""

from http import HTTPStatus
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
    request_origin_from_django,
    request_origin_from_scope,
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
