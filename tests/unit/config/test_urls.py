"""Unit tests for the root URL configuration.

Covers the routes the project exposes, confirming each one resolves to the expected view so a
change to the URL table is deliberate rather than accidental.
"""

from __future__ import annotations

import importlib
import sys
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import pytest
from django.conf import settings
from django.urls import Resolver404, URLResolver, get_resolver, resolve

from config.api import API_PREFIX, ErrorCode
from config.logs import REQUEST_ID_HEADER

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from django.test import Client

    class SchemaGeneratorProtocol(Protocol):
        """Describe the schema generator operation used by the URL test.

        Inherits from ``Protocol`` and exposes only document generation.
        Avoids coupling strict test typing to the untyped schema library.

        Attributes:
            None.

        Members:
            get_schema: Generate the current OpenAPI document.
        """

        def get_schema(self, *, request: None, public: bool) -> dict[str, object]:
            """Generate the current OpenAPI document.

            Builds the route document without an incoming HTTP request.
            Includes every public operation when requested by the caller.

            Arguments:
                request: Optional schema request, unused by this test.
                public: Whether all public operations should be included.

            Returns:
                Generated OpenAPI document.

            Raises:
                None.
            """
            ...


@pytest.mark.unit
def test_admin_route_resolves() -> None:
    """Resolve the administration route.

    Confirms the admin is mounted at its expected prefix, which is the only route the project
    exposes before the application surface is built.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the admin route does not resolve to its expected pattern.
    """
    assert resolve("/admin/").route == "admin/"


@pytest.mark.unit
def test_api_is_mounted_under_the_versioned_prefix() -> None:
    """Mount future application routes beneath a URL-carried version.

    Inspects the root resolver and verifies its API child owns the accepted ``api/v1`` prefix,
    leaving health and administration at their established unversioned paths.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the API prefix is absent, unversioned, or moved.
    """
    api_resolver = next(
        pattern
        for pattern in get_resolver().url_patterns
        if isinstance(pattern, URLResolver) and pattern.namespace == "api-v1"
    )

    assert API_PREFIX == "api/v1/"
    assert str(api_resolver.pattern) == API_PREFIX
    assert resolve("/health/").route == "health/"


@pytest.mark.unit
def test_token_authentication_exposes_only_its_two_versioned_routes() -> None:
    """Resolve the fixed token login and logout surface.

    Confirms both token operations live beneath the URL-carried API version and that neither is
    exposed at an unversioned application path.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either route is absent, renamed, or reachable without the API prefix.
    """
    assert resolve("/api/v1/token/login/").view_name == "api-v1:accounts:token-login"
    assert resolve("/api/v1/token/logout/").view_name == "api-v1:accounts:token-logout"

    with pytest.raises(Resolver404):
        resolve("/token/login/")

    with pytest.raises(Resolver404):
        resolve("/token/logout/")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "path_value"),
    [
        pytest.param("get", "/api/v1/token/login", id="login-get"),
        pytest.param("post", "/api/v1/token/login", id="login-post"),
        pytest.param("get", "/api/v1/token/logout", id="logout-get"),
        pytest.param("post", "/api/v1/token/logout", id="logout-post"),
    ],
)
def test_slashless_api_token_routes_return_correlated_not_found(
    client: Client,
    method: str,
    path_value: str,
) -> None:
    """Reject slashless API token paths without redirecting clients.

    Sends both read and write methods through the public middleware stack and proves API routing
    owns the response as a correlated JSON not-found envelope rather than CommonMiddleware HTML.

    Arguments:
        client: Django test client supplied by the framework.
        method: Lowercase client method to invoke.
        path_value: Slashless versioned token path.

    Returns:
        None.

    Raises:
        AssertionError: If the path redirects or loses its API envelope.
    """
    response = getattr(client, method)(
        path_value,
        data="{}" if method == "post" else None,
        content_type="application/json",
    )
    body = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "Location" not in response.headers
    assert body == {
        "code": ErrorCode.NOT_FOUND,
        "message": "The requested resource was not found.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }


@pytest.mark.unit
def test_non_api_append_slash_redirects_remain_enabled(client: Client) -> None:
    """Preserve append-slash behavior outside the versioned API.

    Requests the slashless administration prefix and verifies CommonMiddleware still redirects to
    Django's canonical route, limiting the API exception to its intended boundary.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If the non-API redirect disappears or changes destination.
    """
    response = client.get("/admin")

    assert response.status_code == HTTPStatus.MOVED_PERMANENTLY
    assert response.headers["Location"] == "/admin/"


@pytest.mark.unit
def test_metrics_route_resolves_to_the_instrumentation_exporter() -> None:
    """Resolve the internal metrics endpoint.

    Confirms the exporter is mounted at the path Prometheus scrapes, rather than merely installing
    instrumentation that has no reachable collection endpoint.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the metrics route is absent or moved.
    """
    match = resolve("/metrics", urlconf="config.metrics_urls")

    assert match.route == "metrics"
    assert match.url_name == "prometheus-django-metrics"

    with pytest.raises(Resolver404):
        resolve("/metrics")


@pytest.mark.unit
def test_metrics_asgi_application_uses_only_the_internal_url_table() -> None:
    """Build the ASGI handler for the isolated metrics listener.

    Imports the listener module with a controlled URL setting and restores the test process
    afterward, proving its application is callable and selects the metrics-only routes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the listener does not build or selects another URL table.
    """
    original_urlconf = settings.ROOT_URLCONF
    sys.modules.pop("config.metrics_asgi", None)

    try:
        module = importlib.import_module("config.metrics_asgi")

        assert callable(module.application)
        assert settings.ROOT_URLCONF == "config.metrics_urls"
    finally:
        settings.ROOT_URLCONF = original_urlconf
        sys.modules.pop("config.metrics_asgi", None)


@pytest.mark.unit
def test_health_route_schema_documents_every_response_shape() -> None:
    """Document readiness states and framework error envelopes.

    Generates the OpenAPI document directly and verifies the health operation carries every
    reachable status plus exact envelope examples for method and representation failures.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a response status or framework error example is absent or stale.
    """
    generator_factory = cast(
        "Callable[[], SchemaGeneratorProtocol]",
        importlib.import_module("drf_spectacular.generators").SchemaGenerator,
    )
    schema = generator_factory().get_schema(request=None, public=True)
    operation = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", schema["paths"])["/health/"]["get"],
    )

    responses = cast("dict[str, Any]", operation["responses"])
    error_examples = {
        status: next(
            iter(
                cast(
                    "dict[str, Any]",
                    cast("dict[str, Any]", response["content"])["application/json"]["examples"],
                ).values()
            )
        )["value"]
        for status, response in responses.items()
        if status in {"405", "406"}
    }

    assert set(responses) == {"200", "405", "406", "503"}
    assert error_examples == {
        "405": {
            "code": ErrorCode.METHOD_NOT_ALLOWED,
            "message": "The requested method is not allowed.",
            "details": {},
            "request_id": "00000000-0000-4000-8000-000000000000",
        },
        "406": {
            "code": ErrorCode.NOT_ACCEPTABLE,
            "message": "The requested response format is not available.",
            "details": {},
            "request_id": "00000000-0000-4000-8000-000000000000",
        },
    }
