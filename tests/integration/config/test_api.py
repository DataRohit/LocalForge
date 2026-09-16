"""Integration tests for API failures outside view exception handling.

Exercises Django routing and exception conversion through the real middleware stack, proving
unknown paths and unexpected failures remain JSON, sanitized, and request-correlated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import path

from config.api import ERROR_STATUS_REGISTRY, ErrorCode, api_server_error
from config.asgi import application as asgi_application
from config.logs import REQUEST_ID_HEADER

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Protocol

    from asgiref.typing import ASGIReceiveEvent, ASGISendEvent, HTTPScope
    from django.http import HttpRequest, HttpResponseBase
    from django.views import View

    from accounts.models import User

    class ClientResponse(Protocol):
        """Describe the public response values asserted by contract tests.

        Inherits from ``Protocol`` and exposes only status, headers, and rendered body, avoiding
        dependence on the test client's private response subclass.

        Attributes:
            status_code: Observed HTTP status.
            headers: Public response header mapping.
            content: Rendered response bytes.

        Members:
            None.
        """

        status_code: int
        headers: Mapping[str, str]
        content: bytes


PRIVATE_FAILURE = "private-unhandled-failure"
ASGI_REQUEST_TIMEOUT_SECONDS = 5
APIView = import_module("rest_framework.views").APIView
Response = import_module("rest_framework.response").Response
BasicAuthentication = import_module("rest_framework.authentication").BasicAuthentication
SessionAuthentication = import_module("rest_framework.authentication").SessionAuthentication
BaseThrottle = import_module("rest_framework.throttling").BaseThrottle


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


def accept_json_write(_self: object, request: object) -> HttpResponseBase:
    """Echo one JSON write after forcing request parsing.

    Reads the REST framework request body so malformed and unsupported representations cross the
    same parser boundary future write endpoints use, then returns the parsed representation.

    Arguments:
        _self: Test-only REST framework view instance.
        request: REST framework request carrying the submitted representation.

    Returns:
        Successful response carrying the parsed representation.

    Raises:
        None.
    """
    return cast(
        "HttpResponseBase",
        Response(cast("Any", request).data, status=HTTPStatus.OK),
    )


JsonWriteView = cast(
    "type[View[HttpResponseBase]]",
    type(
        "JsonWriteView",
        (APIView,),
        {
            "__doc__": (
                "Expose one test-only JSON write boundary.\n\n"
                "Inherits from APIView and opens access explicitly so parser and middleware "
                "rejections can be observed independently of authentication policy."
            ),
            "authentication_classes": [],
            "permission_classes": [],
            "post": accept_json_write,
        },
    ),
)


def return_empty_success(_self: object, _request: object) -> HttpResponseBase:
    """Return success if authentication permits view execution.

    Supplies a reachable body for framework stages that are expected to reject first, making an
    unexpected pass visible as a success status rather than an implementation-side observation.

    Arguments:
        _self: Test-only REST framework view instance.
        _request: REST framework request admitted by authentication.

    Returns:
        Successful empty response.

    Raises:
        None.
    """
    return cast("HttpResponseBase", Response(status=HTTPStatus.NO_CONTENT))


BasicProtectedView = cast(
    "type[View[HttpResponseBase]]",
    type(
        "BasicProtectedView",
        (APIView,),
        {
            "__doc__": (
                "Expose one test-only explicitly authenticated route.\n\n"
                "Inherits from APIView and selects basic authentication so missing credentials "
                "carry a challenge and therefore produce the unauthorized status."
            ),
            "authentication_classes": [BasicAuthentication],
            "get": return_empty_success,
        },
    ),
)


def fail_if_view_executes(_self: object, _request: object) -> NoReturn:
    """Fail if a framework stage admits the request to the view.

    Makes negotiation and throttling ordering observable through the HTTP seam because those
    framework stages must return their own response before dispatch reaches this callable.

    Arguments:
        _self: Test-only REST framework view instance.
        _request: REST framework request that should have been rejected.

    Returns:
        Never returns.

    Raises:
        AssertionError: Always, because reaching the view violates the test contract.
    """
    message = "framework rejection did not happen before view execution"
    raise AssertionError(message)


NegotiationProbeView = cast(
    "type[View[HttpResponseBase]]",
    type(
        "NegotiationProbeView",
        (APIView,),
        {
            "__doc__": (
                "Expose one test-only content-negotiated route.\n\n"
                "Inherits from APIView and opens access explicitly so renderer selection is the "
                "only stage that can reject an unacceptable representation."
            ),
            "authentication_classes": [],
            "permission_classes": [],
            "get": fail_if_view_executes,
        },
    ),
)


def reject_every_request(_self: object, _request: object, _view: object) -> bool:
    """Reject every request at the throttling boundary.

    Supplies a deterministic test throttle so the public route exercises REST framework's real
    throttling response, protocol header, and exception conversion without shared mutable state.

    Arguments:
        _self: Test-only throttle instance.
        _request: REST framework request being checked.
        _view: REST framework view owning the throttle.

    Returns:
        Always ``False``.

    Raises:
        None.
    """
    return False


def throttle_wait(_self: object) -> float:
    """Return the deterministic retry delay for the test throttle.

    Gives REST framework a positive wait so it emits the ``Retry-After`` header that clients need
    to schedule a later attempt.

    Arguments:
        _self: Test-only throttle instance.

    Returns:
        Nine seconds.

    Raises:
        None.
    """
    return 9.0


AlwaysRejectThrottle = type(
    "AlwaysRejectThrottle",
    (BaseThrottle,),
    {
        "__doc__": (
            "Reject every request with a deterministic retry delay.\n\n"
            "Inherits from BaseThrottle and provides the smallest public throttle needed to "
            "exercise framework throttling without a cache or cross-test state."
        ),
        "allow_request": reject_every_request,
        "wait": throttle_wait,
    },
)

ThrottledProbeView = cast(
    "type[View[HttpResponseBase]]",
    type(
        "ThrottledProbeView",
        (APIView,),
        {
            "__doc__": (
                "Expose one test-only throttled route.\n\n"
                "Inherits from APIView and opens access explicitly so the deterministic throttle "
                "owns rejection before the view body can execute."
            ),
            "authentication_classes": [],
            "permission_classes": [],
            "throttle_classes": [AlwaysRejectThrottle],
            "get": fail_if_view_executes,
        },
    ),
)


def return_unavailable(_self: object, _request: object) -> HttpResponseBase:
    """Return a dependency-unavailable response from a versioned route.

    Supplies the status a future API operation can produce when a required service is down, leaving
    the middleware boundary to replace the empty framework body with the shared envelope.

    Arguments:
        _self: Test-only REST framework view instance.
        _request: REST framework request admitted to the view.

    Returns:
        Empty service-unavailable response.

    Raises:
        None.
    """
    return cast("HttpResponseBase", Response(status=HTTPStatus.SERVICE_UNAVAILABLE))


UnavailableProbeView = cast(
    "type[View[HttpResponseBase]]",
    type(
        "UnavailableProbeView",
        (APIView,),
        {
            "__doc__": (
                "Expose one test-only unavailable API operation.\n\n"
                "Inherits from APIView and opens access explicitly so response middleware proves "
                "a versioned service failure receives the shared envelope."
            ),
            "authentication_classes": [],
            "permission_classes": [],
            "get": return_unavailable,
        },
    ),
)


SessionWriteView = cast(
    "type[View[HttpResponseBase]]",
    type(
        "SessionWriteView",
        (APIView,),
        {
            "__doc__": (
                "Expose one test-only session-authenticated write.\n\n"
                "Inherits from APIView, selects SessionAuthentication, and removes permission "
                "classes so CSRF enforcement is the only rejection before view execution."
            ),
            "authentication_classes": [SessionAuthentication],
            "permission_classes": [],
            "post": fail_if_view_executes,
        },
    ),
)


def accept_non_api_write(_request: HttpRequest) -> HttpResponseBase:
    """Return success after a non-API request body is received.

    Provides a test-only route outside the versioned prefix so the ASGI byte boundary can prove its
    exemption without changing the production route surface.

    Arguments:
        _request: Incoming Django request.

    Returns:
        Successful empty response.

    Raises:
        None.
    """
    return HttpResponse(status=HTTPStatus.NO_CONTENT)


urlpatterns = [
    path(
        "api/v1/protected/",
        DefaultProtectedView.as_view(),
    ),
    path(
        "api/v1/unhandled/",
        cast("Callable[[HttpRequest], HttpResponse]", raise_unhandled_error),
    ),
    path(
        "api/v1/write/",
        JsonWriteView.as_view(),
    ),
    path(
        "api/v1/basic-protected/",
        BasicProtectedView.as_view(),
    ),
    path(
        "api/v1/negotiated/",
        NegotiationProbeView.as_view(),
    ),
    path(
        "api/v1/throttled/",
        ThrottledProbeView.as_view(),
    ),
    path(
        "api/v1/unavailable/",
        UnavailableProbeView.as_view(),
    ),
    path(
        "api/v1/session-write/",
        SessionWriteView.as_view(),
    ),
    path(
        "outside-api/",
        accept_non_api_write,
    ),
]
handler500 = api_server_error


def assert_error_envelope(
    response: object,
    status: HTTPStatus,
    code: ErrorCode,
    message: str,
) -> dict[str, object]:
    """Assert one HTTP response matches the shared correlated error envelope.

    Parses only the public response body and checks the stable code, safe message, empty details,
    and request identifier shared with the response header.

    Arguments:
        response: HTTP response observed through the Django client.
        status: Expected HTTP status.
        code: Expected stable error code.
        message: Expected safe human-readable message.

    Returns:
        Parsed response payload for scenario-specific assertions.

    Raises:
        AssertionError: If any envelope field or correlation value differs.
    """
    observed = cast("ClientResponse", response)
    payload = cast("dict[str, object]", json.loads(observed.content))

    assert observed.status_code == status
    assert observed.headers["Content-Type"].startswith("application/json")
    assert payload == {
        "code": code,
        "message": message,
        "details": {},
        "request_id": observed.headers[REQUEST_ID_HEADER],
    }

    return payload


def asgi_post_scope(
    content_length: bytes | None = None,
    *,
    path_value: str = "/api/v1/write/",
) -> HTTPScope:
    """Build an ASGI POST scope for the parser-backed API route.

    Supplies content type and optional declared length while leaving request-body delivery to the
    receive channel, matching chunked HTTP/1.1 and HTTP/2 transport behavior.

    Arguments:
        content_length: Raw content-length header value, or ``None`` when transport omits it.
        path_value: Request path exposed to the ASGI application.

    Returns:
        Complete HTTP scope accepted by the project ASGI application.

    Raises:
        None.
    """
    headers = [(b"host", b"testserver"), (b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", content_length))

    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.5"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path_value,
        "raw_path": path_value.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
        "extensions": {},
    }


async def invoke_asgi_write(
    chunks: tuple[bytes, ...],
    *,
    content_length: bytes | None = None,
    path_value: str = "/api/v1/write/",
) -> list[ASGISendEvent]:
    """Send one multi-chunk request through the public ASGI application.

    Delivers each body fragment as a separate transport event and then blocks the disconnect
    listener until Django completes the response, preserving normal ASGI receive ownership.

    Arguments:
        chunks: Body fragments delivered in transport order.
        content_length: Raw content-length header value, or ``None`` when omitted.
        path_value: Request path exposed to the ASGI application.

    Returns:
        Response events emitted by the application.

    Raises:
        TimeoutError: If the application does not complete promptly.
    """
    requests = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    events: list[ASGISendEvent] = []
    disconnected = asyncio.Event()

    async def receive() -> ASGIReceiveEvent:
        """Deliver body chunks and then wait for request-task cancellation.

        Removes one prepared event per call and blocks after the terminal request body.
        Django cancels that final wait when response processing completes first.

        Arguments:
            None.

        Returns:
            Next request event while body data remains.

        Raises:
            asyncio.CancelledError: When Django cancels its disconnect listener.
        """
        if requests:
            return cast("ASGIReceiveEvent", requests.pop(0))

        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Capture one public response event.

        Retains events in transport order so callers can inspect response metadata and body.
        No event is transformed or withheld from the test observation.

        Arguments:
            message: Response event emitted by the application.

        Returns:
            None.

        Raises:
            None.
        """
        events.append(message)

    with override_settings(ROOT_URLCONF=__name__, API_REQUEST_BODY_MAX_BYTES=8):
        async with asyncio.timeout(ASGI_REQUEST_TIMEOUT_SECONDS):
            await asgi_application(
                asgi_post_scope(content_length, path_value=path_value),
                receive,
                send,
            )

    return events


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_new_api_view_denies_anonymous_access_by_default(client: Client) -> None:
    """Close a new versioned route unless it explicitly opts out.

    Calls a view with no local policy declarations and verifies the central permission produces the
    shared unauthorized envelope and primary Bearer challenge before the view body can return
    success.

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

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert payload == {
        "code": ErrorCode.NOT_AUTHENTICATED,
        "message": "Authentication credentials were not provided.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="api"'


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_challenge_authentication_returns_unauthorized_envelope(client: Client) -> None:
    """Return unauthorized when the selected authenticator can challenge.

    Calls a real route using basic authentication and verifies missing credentials retain the
    shared envelope plus the challenge header that distinguishes 401 from permission denial.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If authentication loses its status, envelope, or challenge header.
    """
    with override_settings(ROOT_URLCONF=__name__):
        response = client.get("/api/v1/basic-protected/")

    assert_error_envelope(
        response,
        HTTPStatus.UNAUTHORIZED,
        ErrorCode.NOT_AUTHENTICATED,
        "Authentication credentials were not provided.",
    )
    assert response.headers["WWW-Authenticate"].startswith("Basic")


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
        override_settings(DEBUG=False, ROOT_URLCONF=__name__),
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
    assert "raise_unhandled_error" not in rendered
    assert "test_api.py" not in rendered
    assert "DJANGO_SECRET_KEY" not in rendered
    assert settings.SECRET_KEY not in rendered


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_malformed_json_returns_bad_request_envelope(client: Client) -> None:
    """Return bad request when a write body is not valid JSON.

    Posts malformed JSON through the real parser-backed route and verifies parsing fails safely
    instead of reaching the view's success response or becoming an unhandled server error.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If malformed JSON returns another status or response shape.
    """
    with override_settings(ROOT_URLCONF=__name__):
        response = client.post(
            "/api/v1/write/",
            data='{"value":',
            content_type="application/json",
        )

    assert_error_envelope(
        response,
        HTTPStatus.BAD_REQUEST,
        ErrorCode.PARSE_ERROR,
        "The request body could not be parsed.",
    )


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_unacceptable_representation_is_rejected_before_view_execution(client: Client) -> None:
    """Reject an unacceptable response representation during negotiation.

    Requests a renderer the route does not provide and receives not acceptable; the route body
    raises if called, proving content negotiation completed the response before view execution.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If negotiation admits the request or loses the error contract.
    """
    with override_settings(ROOT_URLCONF=__name__):
        response = client.get(
            "/api/v1/negotiated/",
            headers={"accept": "text/plain"},
        )

    assert_error_envelope(
        response,
        HTTPStatus.NOT_ACCEPTABLE,
        ErrorCode.NOT_ACCEPTABLE,
        "The requested response format is not available.",
    )


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_unsupported_write_content_type_returns_media_type_envelope(client: Client) -> None:
    """Reject a write representation no configured parser supports.

    Posts plain text through the real parser-backed route and verifies REST framework returns the
    unsupported-media-type response instead of treating the body as valid or raising a server error.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If unsupported content reaches success or loses the shared envelope.
    """
    with override_settings(ROOT_URLCONF=__name__):
        response = client.post(
            "/api/v1/write/",
            data="plain text",
            content_type="text/plain",
        )

    assert_error_envelope(
        response,
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        "The request content type is not supported.",
    )


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_unsupported_method_returns_allowed_methods_with_envelope(client: Client) -> None:
    """Return method not allowed with the route's permitted methods.

    Sends an unsupported method to the real write route and verifies the framework preserves its
    discovery header when the response body is replaced by the shared envelope.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If the status, envelope, or permitted method set changes.
    """
    with override_settings(ROOT_URLCONF=__name__):
        response = client.put(
            "/api/v1/write/",
            data="{}",
            content_type="application/json",
        )

    assert_error_envelope(
        response,
        HTTPStatus.METHOD_NOT_ALLOWED,
        ErrorCode.METHOD_NOT_ALLOWED,
        "The requested method is not allowed.",
    )
    assert {method.strip() for method in response.headers["Allow"].split(",")} == {
        "OPTIONS",
        "POST",
    }


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_throttled_route_returns_retry_delay_with_envelope(client: Client) -> None:
    """Return too many requests through REST framework's throttle stage.

    Calls a route whose stateless throttle always rejects and verifies both the correlated envelope
    and retry delay survive framework exception conversion.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If throttling reaches the view or loses its protocol header.
    """
    with override_settings(ROOT_URLCONF=__name__):
        response = client.get("/api/v1/throttled/")

    assert_error_envelope(
        response,
        HTTPStatus.TOO_MANY_REQUESTS,
        ErrorCode.THROTTLED,
        "Too many requests.",
    )
    assert response.headers["Retry-After"] == "9"


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_session_authenticated_csrf_failure_uses_middleware_backed_authentication(
    django_user_model: type[User],
) -> None:
    """Reject a session write through middleware-backed authentication CSRF.

    Authenticates a client through a database-backed session and posts without a token through
    DRF's SessionAuthentication with no permission classes, proving its CSRF path owns rejection.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the request reaches the view or bypasses the shared envelope.
    """
    user = django_user_model.objects.create_user(
        username="csrf-contract",
        email="csrf-contract@example.invalid",
        password=secrets.token_urlsafe(24),
        is_active=True,
    )

    with override_settings(
        ROOT_URLCONF=__name__,
        SESSION_ENGINE="django.contrib.sessions.backends.db",
    ):
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.post(
            "/api/v1/session-write/",
            data="{}",
            content_type="application/json",
        )

    assert_error_envelope(
        response,
        HTTPStatus.FORBIDDEN,
        ErrorCode.PERMISSION_DENIED,
        "You do not have permission to perform this action.",
    )


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_versioned_service_failure_returns_unavailable_envelope(client: Client) -> None:
    """Envelope a service-unavailable response from a versioned operation.

    Calls a real API route returning an empty dependency failure and verifies response middleware
    supplies the stable unavailable code without changing the established health endpoint contract.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If a versioned 503 remains empty or receives a generic code.
    """
    with override_settings(ROOT_URLCONF=__name__):
        response = client.get("/api/v1/unavailable/")

    assert_error_envelope(
        response,
        HTTPStatus.SERVICE_UNAVAILABLE,
        ErrorCode.SERVICE_UNAVAILABLE,
        "A required service is unavailable.",
    )


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_oversized_api_request_is_rejected_before_the_view_consumes_it(client: Client) -> None:
    """Reject a request whose declared body exceeds the configured API limit.

    Posts valid JSON through the real middleware stack and expects a correlated content-too-large
    envelope instead of the success the parser-backed view would return after consuming the body.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If an oversized body reaches the view or returns another contract.
    """
    with override_settings(ROOT_URLCONF=__name__, API_REQUEST_BODY_MAX_BYTES=8):
        response = client.post(
            "/api/v1/write/",
            data='{"value":"too large"}',
            content_type="application/json",
        )

    assert_error_envelope(
        response,
        HTTPStatus.CONTENT_TOO_LARGE,
        ErrorCode.REQUEST_TOO_LARGE,
        "The request body is too large.",
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.services("postgres")
@pytest.mark.parametrize(
    "content_length",
    [
        pytest.param(None, id="absent-content-length"),
        pytest.param(b"not-a-length", id="invalid-content-length"),
        pytest.param(b"2", id="understated-content-length"),
    ],
)
async def test_asgi_rejects_oversized_multichunk_body_from_actual_bytes(
    content_length: bytes | None,
) -> None:
    """Reject actual request bytes regardless of transport declaration.

    Streams an oversized JSON document through separate ASGI receive events and verifies the public
    application rejects absent, malformed, and understated lengths before REST parsing can succeed.

    Arguments:
        content_length: Missing, malformed, or understated transport declaration under test.

    Returns:
        None.

    Raises:
        AssertionError: If actual byte counting is bypassed or correlation is lost.
    """
    events = await invoke_asgi_write(
        (b'{"value":', b'"too large"}'),
        content_length=content_length,
    )
    start = next(event for event in events if event["type"] == "http.response.start")
    body = b"".join(
        event.get("body", b"") for event in events if event["type"] == "http.response.body"
    )
    headers = {name.lower(): value for name, value in start["headers"]}
    payload = cast("dict[str, object]", json.loads(body))

    assert start["status"] == HTTPStatus.CONTENT_TOO_LARGE
    assert payload == {
        "code": ErrorCode.REQUEST_TOO_LARGE,
        "message": "The request body is too large.",
        "details": {},
        "request_id": headers[b"x-request-id"].decode(),
    }


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.services("postgres")
async def test_asgi_rejects_declared_oversized_body_before_receiving_transport_data() -> None:
    """Reject an oversized declaration without reading its body.

    Supplies a receive callable that fails if invoked and verifies the public ASGI application
    returns the same correlated content-too-large envelope from metadata alone.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If declared-length rejection reads transport data or loses its contract.
    """
    events: list[ASGISendEvent] = []

    async def receive() -> ASGIReceiveEvent:
        """Fail if the application asks transport for an already-rejected body.

        Makes pre-receive rejection observable without supplying any body event.
        Any call proves the declared-length fast path ran too late.

        Arguments:
            None.

        Returns:
            Never returns.

        Raises:
            AssertionError: Always, because declared size must reject first.
        """
        message = "declared oversized body was read"
        raise AssertionError(message)

    async def send(message: ASGISendEvent) -> None:
        """Capture one rejection response event.

        Retains the start and terminal body events exactly as the application emits them.
        The test then compares their public correlation and error fields.

        Arguments:
            message: Response event emitted by the application.

        Returns:
            None.

        Raises:
            None.
        """
        events.append(message)

    with override_settings(ROOT_URLCONF=__name__, API_REQUEST_BODY_MAX_BYTES=8):
        async with asyncio.timeout(ASGI_REQUEST_TIMEOUT_SECONDS):
            await asgi_application(asgi_post_scope(b"9"), receive, send)

    start = next(event for event in events if event["type"] == "http.response.start")
    body = b"".join(
        event.get("body", b"") for event in events if event["type"] == "http.response.body"
    )
    headers = {name.lower(): value for name, value in start["headers"]}
    payload = cast("dict[str, object]", json.loads(body))

    assert start["status"] == HTTPStatus.CONTENT_TOO_LARGE
    assert payload["request_id"] == headers[b"x-request-id"].decode()
    assert payload["code"] == ErrorCode.REQUEST_TOO_LARGE


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.services("postgres")
@pytest.mark.parametrize(
    "content_length",
    [
        pytest.param(None, id="absent-content-length"),
        pytest.param(b"not-a-length", id="invalid-content-length"),
        pytest.param(b"2", id="valid-content-length"),
    ],
)
async def test_asgi_preserves_accepted_multichunk_body_stream(
    content_length: bytes | None,
) -> None:
    """Pass accepted body chunks through unchanged.

    Splits valid JSON across receive events and verifies missing, invalid, and valid declared
    lengths all reach REST parsing normally when actual bytes remain within the ceiling.

    Arguments:
        content_length: Transport declaration variant under test.

    Returns:
        None.

    Raises:
        AssertionError: If accepted transport data is altered or rejected.
    """
    events = await invoke_asgi_write((b"{", b"}"), content_length=content_length)
    start = next(event for event in events if event["type"] == "http.response.start")
    body = b"".join(
        event.get("body", b"") for event in events if event["type"] == "http.response.body"
    )

    assert start["status"] == HTTPStatus.OK
    assert json.loads(body) == {}


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.services("postgres")
async def test_asgi_body_limit_exempts_non_api_routes() -> None:
    """Leave bodies outside the versioned API unrestricted.

    Sends more bytes than the API ceiling to a test-only non-API route and verifies Django's CSRF
    boundary receives the request instead of the API body limiter returning its JSON envelope.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the API ceiling replaces non-API Django behavior.
    """
    events = await invoke_asgi_write(
        (b"outside", b"-api-limit"),
        path_value="/outside-api/",
    )
    start = next(event for event in events if event["type"] == "http.response.start")

    headers = {name.lower(): value for name, value in start["headers"]}

    assert start["status"] == HTTPStatus.FORBIDDEN
    assert headers[b"content-type"].startswith(b"text/html")


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_route_contract_covers_every_registered_error_status() -> None:
    """Keep the route-level contract aligned with the durable status registry.

    Names every status exercised by this module and compares that set with the production registry,
    giving Ticket 36 one stable matrix to compare with generated schema responses.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a governing status lacks a real-route test or an extra status appears.
    """
    route_tested_statuses = {
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.METHOD_NOT_ALLOWED,
        HTTPStatus.NOT_ACCEPTABLE,
        HTTPStatus.CONTENT_TOO_LARGE,
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.SERVICE_UNAVAILABLE,
    }

    assert route_tested_statuses == set(ERROR_STATUS_REGISTRY)
