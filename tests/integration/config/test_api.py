"""Integration tests for API failures outside view exception handling.

Exercises Django routing and exception conversion through the real middleware stack, proving
unknown paths and unexpected failures remain JSON, sanitized, and request-correlated.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest
from django.test import Client, override_settings
from django.urls import path

from config.api import ErrorCode, api_server_error
from config.logs import REQUEST_ID_HEADER

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse, HttpResponseBase
    from django.views import View

PRIVATE_FAILURE = "private-unhandled-failure"
APIView = import_module("rest_framework.views").APIView
Response = import_module("rest_framework.response").Response


def protected_get(_self: object, _request: object) -> HttpResponseBase:
    """Return a successful response after central policy admits the caller.

    Provides a body that is unreachable to an anonymous request, making denial observable through
    the versioned HTTP endpoint rather than through settings inspection alone.

    Arguments:
        _self: Test-only REST framework view instance.
        _request: REST framework request admitted by central policy.

    Returns:
        Successful empty response.

    Raises:
        None.
    """
    return cast("HttpResponseBase", Response(status=HTTPStatus.NO_CONTENT))


DefaultProtectedView = cast(
    "type[View[HttpResponseBase]]",
    type(
        "DefaultProtectedView",
        (APIView,),
        {
            "__doc__": (
                "Expose one test-only view using only central REST framework policy.\n\n"
                "Inherits from APIView without local policy declarations, proving a newly added "
                "route is closed and enveloped by default."
            ),
            "get": protected_get,
        },
    ),
)


def raise_unhandled_error(_request: HttpRequest) -> NoReturn:
    """Raise an exception that no view or REST framework handler contains.

    Supplies the integration seam for Django's production server-error handling without adding a
    diagnostic route to the fixed application surface.

    Arguments:
        _request: Incoming request supplied by Django.

    Returns:
        Never returns.

    Raises:
        RuntimeError: Always, carrying private diagnostic text.
    """
    raise RuntimeError(PRIVATE_FAILURE)


urlpatterns = [
    path(
        "api/v1/protected/",
        DefaultProtectedView.as_view(),
    ),
    path(
        "api/v1/unhandled/",
        cast("Callable[[HttpRequest], HttpResponse]", raise_unhandled_error),
    ),
]
handler500 = api_server_error


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_new_api_view_denies_anonymous_access_by_default(client: Client) -> None:
    """Close a new versioned route unless it explicitly opts out.

    Calls a view with no local policy declarations and verifies the central permission produces the
    shared error envelope before the view body can return success.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If anonymous access reaches the test view or bypasses the envelope.
    """
    with override_settings(ROOT_URLCONF=__name__):
        response = client.get("/api/v1/protected/")

    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert payload == {
        "code": ErrorCode.NOT_AUTHENTICATED,
        "message": "Authentication credentials were not provided.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_unknown_path_returns_a_correlated_json_envelope(
    client: Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Represent a routing miss with the shared JSON contract.

    Requests an unknown production path and compares the body identifier with the response header
    and completion log, proving routing failures preserve exact end-to-end correlation.

    Arguments:
        client: Django test client supplied by the framework.
        caplog: Fixture collecting request completion records.

    Returns:
        None.

    Raises:
        AssertionError: If routing returns HTML, another shape, or mismatched correlation.
    """
    with (
        override_settings(DEBUG=True),
        caplog.at_level(logging.INFO, logger="localforge.request"),
    ):
        response = client.get("/api/v1/missing/")

    payload = cast("dict[str, Any]", response.json())
    completion = next(
        record
        for record in caplog.records
        if record.name == "localforge.request" and record.getMessage() == "request completed"
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["Content-Type"].startswith("application/json")
    assert payload == {
        "code": ErrorCode.NOT_FOUND,
        "message": "The requested resource was not found.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }
    assert completion.__dict__["request_id"] == payload["request_id"]


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_unknown_non_api_path_uses_djangos_standard_not_found_response(client: Client) -> None:
    """Leave routing misses outside the versioned API under Django's standard boundary.

    Requests an unknown non-API path and verifies the root handler preserves Django's HTML response
    while the outer request middleware still supplies correlation metadata.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If a non-API routing miss receives the API JSON envelope.
    """
    response = client.get("/missing-outside-api/")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["Content-Type"].startswith("text/html")
    assert response.headers[REQUEST_ID_HEADER]
    assert b'"request_id"' not in response.content


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_admin_csrf_rejection_uses_djangos_standard_response() -> None:
    """Leave administration CSRF failures under Django's standard boundary.

    Posts to the administration login without a token through a CSRF-enforcing client and verifies
    the middleware returns its normal HTML rejection rather than the versioned API envelope.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the global CSRF failure view envelopes the administration response.
    """
    client = Client(enforce_csrf_checks=True)

    response = client.post(
        "/admin/login/",
        {"username": "nobody", "password": "invalid"},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.headers["Content-Type"].startswith("text/html")
    assert response.headers[REQUEST_ID_HEADER]
    assert b'"request_id"' not in response.content


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_unhandled_exception_returns_a_sanitized_correlated_envelope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contain an unexpected exception at the production HTTP boundary.

    Calls a versioned test-only endpoint with debug disabled and proves the client receives no
    traceback or private diagnostic while body, header, and completion log share one identifier.

    Arguments:
        caplog: Fixture collecting request completion records.

    Returns:
        None.

    Raises:
        AssertionError: If the exception escapes, leaks details, or loses correlation.
    """
    client = Client(raise_request_exception=False)

    with (
        override_settings(DEBUG=True, ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO, logger="localforge.request"),
    ):
        response = client.get("/api/v1/unhandled/")

    payload = cast("dict[str, Any]", response.json())
    completion = next(
        record
        for record in caplog.records
        if record.name == "localforge.request" and record.getMessage() == "request completed"
    )
    rendered = response.content.decode()

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.headers["Content-Type"].startswith("application/json")
    assert payload == {
        "code": ErrorCode.INTERNAL_SERVER_ERROR,
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }
    assert completion.__dict__["request_id"] == payload["request_id"]
    assert PRIVATE_FAILURE not in rendered
    assert "Traceback" not in rendered
