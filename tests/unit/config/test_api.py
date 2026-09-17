"""Unit tests for the shared API error boundary.

Exercises every framework exception mapped by the public handler and the central code registry, so
all API failures retain one client-facing shape without exposing implementation-specific details.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, JsonResponse
from django.test import RequestFactory, override_settings
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    MethodNotAllowed,
    NotAcceptable,
    NotAuthenticated,
    NotFound,
    ParseError,
    PermissionDenied,
    Throttled,
    UnsupportedMediaType,
    ValidationError,
)

from config.api import (
    ERROR_STATUS_REGISTRY,
    ErrorCode,
    api_bad_request,
    api_csrf_failure,
    api_error_envelope_middleware,
    api_exception_handler,
    api_not_found,
    api_permission_denied,
    api_request_body_limit_asgi,
    api_server_error,
)
from config.logs import REQUEST_ID_META_KEY, request_identifier

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from asgiref.typing import (
        ASGI3Application,
        ASGIReceiveCallable,
        ASGIReceiveEvent,
        ASGISendCallable,
        ASGISendEvent,
        Scope,
    )
    from django.http import HttpRequest
    from django.http.response import HttpResponseBase
    from rest_framework.response import Response

REQUEST_IDENTIFIER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

MAPPED_EXCEPTIONS = (
    pytest.param(
        ValidationError({"email": ["Enter a valid email address."]}),
        HTTPStatus.BAD_REQUEST,
        ErrorCode.VALIDATION_ERROR,
        {"email": ["Enter a valid email address."]},
        None,
        id="validation-error",
    ),
    pytest.param(
        ParseError(),
        HTTPStatus.BAD_REQUEST,
        ErrorCode.PARSE_ERROR,
        {},
        None,
        id="parse-error",
    ),
    pytest.param(
        AuthenticationFailed(),
        HTTPStatus.UNAUTHORIZED,
        ErrorCode.AUTHENTICATION_FAILED,
        {},
        None,
        id="authentication-failed",
    ),
    pytest.param(
        NotAuthenticated(),
        HTTPStatus.UNAUTHORIZED,
        ErrorCode.NOT_AUTHENTICATED,
        {},
        None,
        id="not-authenticated",
    ),
    pytest.param(
        PermissionDenied(),
        HTTPStatus.FORBIDDEN,
        ErrorCode.PERMISSION_DENIED,
        {},
        None,
        id="permission-denied",
    ),
    pytest.param(
        DjangoPermissionDenied(),
        HTTPStatus.FORBIDDEN,
        ErrorCode.PERMISSION_DENIED,
        {},
        None,
        id="django-permission-denied",
    ),
    pytest.param(
        NotFound(),
        HTTPStatus.NOT_FOUND,
        ErrorCode.NOT_FOUND,
        {},
        None,
        id="not-found",
    ),
    pytest.param(
        Http404(),
        HTTPStatus.NOT_FOUND,
        ErrorCode.NOT_FOUND,
        {},
        None,
        id="django-http404",
    ),
    pytest.param(
        MethodNotAllowed("POST"),
        HTTPStatus.METHOD_NOT_ALLOWED,
        ErrorCode.METHOD_NOT_ALLOWED,
        {},
        None,
        id="method-not-allowed",
    ),
    pytest.param(
        NotAcceptable(),
        HTTPStatus.NOT_ACCEPTABLE,
        ErrorCode.NOT_ACCEPTABLE,
        {},
        None,
        id="not-acceptable",
    ),
    pytest.param(
        UnsupportedMediaType("text/plain"),
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        {},
        None,
        id="unsupported-media-type",
    ),
    pytest.param(
        Throttled(wait=3),
        HTTPStatus.TOO_MANY_REQUESTS,
        ErrorCode.THROTTLED,
        {},
        ("Retry-After", "3"),
        id="throttled",
    ),
    pytest.param(
        APIException(),
        HTTPStatus.INTERNAL_SERVER_ERROR,
        ErrorCode.API_ERROR,
        {},
        None,
        id="generic-api-exception",
    ),
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_asgi_body_limit_preserves_disconnect_events() -> None:
    """Pass a transport disconnect through the API body boundary.

    Invokes the public wrapper with an API scope whose first inbound event is a disconnect.
    The accepted application observes that event unchanged and no rejection response is emitted.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a non-body event is altered or produces a response.
    """
    observed: list[ASGIReceiveEvent] = []
    emitted: list[ASGISendEvent] = []

    async def application(
        _scope: Scope,
        receive: ASGIReceiveCallable,
        _send: ASGISendCallable,
    ) -> None:
        """Receive one event through the body-limit wrapper.

        Records the event supplied by the wrapped receive callable without producing a response.
        This isolates transparent receive behavior from Django's disconnect handling.

        Arguments:
            _scope: Incoming API scope, unused by the accepted application.
            receive: Wrapped receive callable supplied by the body boundary.
            _send: Wrapped send callable, unused because disconnect produces no response.

        Returns:
            None.

        Raises:
            BaseException: Any receive failure from the wrapper.
        """
        observed.append(await receive())

    async def receive() -> ASGIReceiveEvent:
        """Return one client disconnect event.

        Supplies a non-body ASGI event so byte counting must leave it untouched.
        No later event is requested by the accepted application.

        Arguments:
            None.

        Returns:
            Client disconnect event.

        Raises:
            None.
        """
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Record an unexpected response event.

        Retains any event so the assertion can prove disconnect forwarding emits nothing.
        The helper does not otherwise interpret transport output.

        Arguments:
            message: Outbound event emitted by the wrapper.

        Returns:
            None.

        Raises:
            None.
        """
        emitted.append(message)

    scope = cast(
        "Scope",
        {
            "type": "http",
            "path": "/api/v1/probe/",
            "headers": [],
        },
    )
    wrapped = api_request_body_limit_asgi(cast("ASGI3Application", application))

    with override_settings(API_REQUEST_BODY_MAX_BYTES=8):
        await wrapped(scope, receive, send)

    assert observed == [{"type": "http.disconnect"}]
    assert emitted == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exception", "status", "code", "details", "expected_header"),
    MAPPED_EXCEPTIONS,
)
def test_framework_exceptions_use_the_shared_envelope(
    exception: Exception,
    status: HTTPStatus,
    code: ErrorCode,
    details: dict[str, object],
    expected_header: tuple[str, str] | None,
) -> None:
    """Map one framework exception to the public error contract.

    Calls the exception boundary with a correlated request and verifies status, headers, stable
    code, human message, validation detail, and request identifier through its public response.

    Arguments:
        exception: Framework exception under test.
        status: Expected HTTP status.
        code: Expected stable error code.
        details: Expected per-field validation details.
        expected_header: Framework response header that must survive envelope replacement.

    Returns:
        None.

    Raises:
        AssertionError: If the exception does not retain the shared contract.
    """
    request = RequestFactory().get("/api/v1/probe/")
    request.META[REQUEST_ID_META_KEY] = REQUEST_IDENTIFIER

    response = cast(
        "Response",
        api_exception_handler(exception, {"request": request}),
    )
    payload = cast("dict[str, Any]", response.data)

    assert response.status_code == status
    assert payload["code"] == code
    assert isinstance(payload["message"], str)
    assert payload["message"]
    assert payload["details"] == details
    assert payload["request_id"] == REQUEST_IDENTIFIER

    if expected_header is not None:
        name, value = expected_header
        assert response[name] == value


@pytest.mark.unit
def test_non_framework_exceptions_remain_for_the_server_error_boundary() -> None:
    """Leave an unhandled exception outside the REST framework mapping.

    Confirms the handler returns no response for an unknown exception, allowing Django's sanitized
    server-error boundary to own failures the REST framework cannot safely describe.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an unknown exception is represented as a framework error.
    """
    request = RequestFactory().get("/api/v1/probe/")
    request.META[REQUEST_ID_META_KEY] = REQUEST_IDENTIFIER

    assert api_exception_handler(RuntimeError("private failure"), {"request": request}) is None


@pytest.mark.unit
def test_list_validation_errors_use_the_non_field_key() -> None:
    """Represent list-shaped validation failures in the field-detail object.

    Calls the public exception boundary with view-level validation output and verifies clients can
    parse it through the same mapping used for serializer field failures.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If list validation changes the envelope shape.
    """
    request = RequestFactory().get("/api/v1/probe/")
    request.META[REQUEST_ID_META_KEY] = REQUEST_IDENTIFIER

    response = cast(
        "Response",
        api_exception_handler(ValidationError(["Invalid request."]), {"request": request}),
    )
    payload = cast("dict[str, Any]", response.data)

    assert payload["details"] == {"non_field_errors": ["Invalid request."]}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("handler", "status", "code"),
    [
        pytest.param(
            api_bad_request,
            HTTPStatus.BAD_REQUEST,
            ErrorCode.BAD_REQUEST,
            id="bad-request",
        ),
        pytest.param(
            api_permission_denied,
            HTTPStatus.FORBIDDEN,
            ErrorCode.PERMISSION_DENIED,
            id="permission-denied",
        ),
        pytest.param(
            api_not_found,
            HTTPStatus.NOT_FOUND,
            ErrorCode.NOT_FOUND,
            id="not-found",
        ),
    ],
)
def test_django_error_handlers_use_the_shared_envelope(
    handler: Callable[[HttpRequest, Exception], JsonResponse],
    status: HTTPStatus,
    code: ErrorCode,
) -> None:
    """Convert one Django error response to the shared JSON contract.

    Exercises the public handlers used by root URL resolution and middleware so responses outside
    REST framework retain stable codes and request correlation.

    Arguments:
        handler: Django error handler under test.
        status: Expected HTTP status.
        code: Expected stable error code.

    Returns:
        None.

    Raises:
        AssertionError: If a Django boundary returns another shape or status.
    """
    request = RequestFactory().get("/api/v1/probe/")
    request.META[REQUEST_ID_META_KEY] = REQUEST_IDENTIFIER
    response = handler(request, Exception("private detail"))
    payload = cast("dict[str, Any]", json.loads(response.content))

    assert response.status_code == status
    assert payload["code"] == code
    assert payload["request_id"] == REQUEST_IDENTIFIER
    assert "private detail" not in response.content.decode()


@pytest.mark.unit
def test_server_error_uses_the_active_identifier_before_request_metadata_exists() -> None:
    """Correlate a server error through the active request context fallback.

    Omits request metadata and verifies the public server-error handler uses the identifier already
    owned by the request-context middleware rather than generating or exposing another value.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the fallback loses the active request identifier.
    """
    request = RequestFactory().get("/api/v1/probe/")
    token = request_identifier.set(REQUEST_IDENTIFIER)

    try:
        response = cast("JsonResponse", api_server_error(request))
    finally:
        request_identifier.reset(token)

    payload = cast("dict[str, Any]", json.loads(response.content))

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert payload["request_id"] == REQUEST_IDENTIFIER


@pytest.mark.unit
def test_csrf_failure_discards_the_framework_reason() -> None:
    """Sanitize CSRF middleware diagnostics in the shared envelope.

    Calls the configured middleware failure view and verifies its internal reason never enters the
    permission-denied response returned to the client.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the CSRF reason leaks or the status changes.
    """
    request = RequestFactory().post("/api/v1/probe/")
    request.META[REQUEST_ID_META_KEY] = REQUEST_IDENTIFIER
    response = cast(
        "JsonResponse",
        api_csrf_failure(request, reason="private csrf diagnostic"),
    )
    payload = cast("dict[str, Any]", json.loads(response.content))

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert payload["code"] == ErrorCode.PERMISSION_DENIED
    assert "private csrf diagnostic" not in response.content.decode()


@pytest.mark.unit
def test_non_api_permission_failure_uses_djangos_standard_response() -> None:
    """Delegate non-API permission failures to Django.

    Calls the configured root handler for an administration path and verifies it returns the
    framework's HTML response rather than the versioned API envelope.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a non-API permission failure is represented as API JSON.
    """
    request = RequestFactory().get("/admin/probe/")

    response = api_permission_denied(request, DjangoPermissionDenied())

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.headers["Content-Type"].startswith("text/html")


@pytest.mark.unit
def test_non_api_server_failure_uses_djangos_standard_response() -> None:
    """Delegate non-API unexpected failures to Django.

    Calls the configured root handler for an administration path and verifies it returns the
    framework's sanitized HTML response rather than the versioned API envelope.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a non-API server failure is represented as API JSON.
    """
    request = RequestFactory().get("/admin/probe/")

    response = api_server_error(request)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.headers["Content-Type"].startswith("text/html")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status"),
    [
        pytest.param("/health/", HTTPStatus.NOT_FOUND, id="non-api"),
        pytest.param("/api/v1/probe/", HTTPStatus.OK, id="successful-api"),
    ],
)
async def test_envelope_middleware_leaves_exempt_responses_unchanged(
    path: str,
    status: HTTPStatus,
) -> None:
    """Leave successful API and non-API responses untouched.

    Passes completed responses through the public middleware boundary and verifies its exemptions
    preserve health behavior and successful versioned endpoint bodies.

    Arguments:
        path: Request path selecting the exemption.
        status: Response status selecting the exemption.

    Returns:
        None.

    Raises:
        AssertionError: If middleware replaces an exempt response.
    """
    request = RequestFactory().get(path)
    response = JsonResponse({"status": "original"}, status=status)
    get_response = AsyncMock(return_value=response)
    middleware = api_error_envelope_middleware(
        cast("Callable[[HttpRequest], Awaitable[HttpResponseBase]]", get_response)
    )

    assert await middleware(request) is response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_envelope_middleware_wraps_manual_errors_and_preserves_protocol_headers() -> None:
    """Envelope a manually returned versioned API failure.

    Supplies an otherwise inconsistent error response and verifies middleware replaces its body
    while retaining the status, method-discovery header, and cookie attached by inner processing.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the wrapper loses protocol metadata or retains the old body.
    """
    request = RequestFactory().get("/api/v1/probe/")
    request.META[REQUEST_ID_META_KEY] = REQUEST_IDENTIFIER
    response = JsonResponse({"detail": "old shape"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
    response["Allow"] = "GET"
    response.set_cookie("probe", "retained")
    get_response = AsyncMock(return_value=response)
    middleware = api_error_envelope_middleware(
        cast("Callable[[HttpRequest], Awaitable[HttpResponseBase]]", get_response)
    )

    enveloped = cast("JsonResponse", await middleware(request))
    payload = cast("dict[str, Any]", json.loads(enveloped.content))

    assert enveloped.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert enveloped["Allow"] == "GET"
    assert enveloped.cookies["probe"].value == "retained"
    assert payload == {
        "code": ErrorCode.METHOD_NOT_ALLOWED,
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": REQUEST_IDENTIFIER,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_envelope_middleware_uses_the_generic_code_for_an_unknown_error_status() -> None:
    """Envelope an unregistered error status without inventing a code.

    Sends a valid but unmapped HTTP failure through middleware and verifies the central generic code
    is used while the original status remains intact.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an unknown status escapes the envelope or changes status.
    """
    request = RequestFactory().get("/api/v1/probe/")
    request.META[REQUEST_ID_META_KEY] = REQUEST_IDENTIFIER
    response = JsonResponse({"detail": "old shape"}, status=HTTPStatus.IM_A_TEAPOT)
    get_response = AsyncMock(return_value=response)
    middleware = api_error_envelope_middleware(
        cast("Callable[[HttpRequest], Awaitable[HttpResponseBase]]", get_response)
    )

    enveloped = cast("JsonResponse", await middleware(request))
    payload = cast("dict[str, Any]", json.loads(enveloped.content))

    assert enveloped.status_code == HTTPStatus.IM_A_TEAPOT
    assert payload["code"] == ErrorCode.API_ERROR


@pytest.mark.unit
def test_error_codes_are_declared_in_one_enumeration() -> None:
    """Keep every client-facing error code in one registry.

    Asserts the complete Ticket 27 vocabulary as independent literals, so a call site cannot add a
    new branchable value without changing the central enumeration and its contract test.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a code is missing, renamed, or added outside the accepted vocabulary.
    """
    assert {code.value for code in ErrorCode} == {
        "api_error",
        "activation_token_expired",
        "activation_token_foreign",
        "activation_token_malformed",
        "activation_token_used",
        "authentication_failed",
        "bad_request",
        "internal_server_error",
        "method_not_allowed",
        "not_acceptable",
        "not_authenticated",
        "not_found",
        "parse_error",
        "password_reset_token_expired",
        "password_reset_token_foreign",
        "password_reset_token_malformed",
        "password_reset_token_used",
        "permission_denied",
        "request_too_large",
        "service_unavailable",
        "throttled",
        "unsupported_media_type",
        "username_reset_token_expired",
        "username_reset_token_foreign",
        "username_reset_token_malformed",
        "username_reset_token_used",
        "validation_error",
    }


@pytest.mark.unit
def test_error_status_registry_exposes_the_complete_governing_matrix() -> None:
    """Expose every governing HTTP status and its permitted stable codes.

    Compares the durable registry with independent status and code literals so Ticket 36 can use
    the same source to detect undocumented responses without deriving expectations from handlers.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a governing status or permitted code is missing or silently added.
    """
    expected = {
        HTTPStatus.BAD_REQUEST: frozenset(
            {
                ErrorCode.ACTIVATION_TOKEN_EXPIRED,
                ErrorCode.ACTIVATION_TOKEN_FOREIGN,
                ErrorCode.ACTIVATION_TOKEN_MALFORMED,
                ErrorCode.ACTIVATION_TOKEN_USED,
                ErrorCode.BAD_REQUEST,
                ErrorCode.PARSE_ERROR,
                ErrorCode.PASSWORD_RESET_TOKEN_EXPIRED,
                ErrorCode.PASSWORD_RESET_TOKEN_FOREIGN,
                ErrorCode.PASSWORD_RESET_TOKEN_MALFORMED,
                ErrorCode.PASSWORD_RESET_TOKEN_USED,
                ErrorCode.USERNAME_RESET_TOKEN_EXPIRED,
                ErrorCode.USERNAME_RESET_TOKEN_FOREIGN,
                ErrorCode.USERNAME_RESET_TOKEN_MALFORMED,
                ErrorCode.USERNAME_RESET_TOKEN_USED,
                ErrorCode.VALIDATION_ERROR,
            }
        ),
        HTTPStatus.UNAUTHORIZED: frozenset(
            {
                ErrorCode.AUTHENTICATION_FAILED,
                ErrorCode.NOT_AUTHENTICATED,
            }
        ),
        HTTPStatus.FORBIDDEN: frozenset(
            {
                ErrorCode.NOT_AUTHENTICATED,
                ErrorCode.PERMISSION_DENIED,
            }
        ),
        HTTPStatus.NOT_FOUND: frozenset({ErrorCode.NOT_FOUND}),
        HTTPStatus.METHOD_NOT_ALLOWED: frozenset({ErrorCode.METHOD_NOT_ALLOWED}),
        HTTPStatus.NOT_ACCEPTABLE: frozenset({ErrorCode.NOT_ACCEPTABLE}),
        HTTPStatus.CONTENT_TOO_LARGE: frozenset({ErrorCode.REQUEST_TOO_LARGE}),
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: frozenset({ErrorCode.UNSUPPORTED_MEDIA_TYPE}),
        HTTPStatus.TOO_MANY_REQUESTS: frozenset({ErrorCode.THROTTLED}),
        HTTPStatus.INTERNAL_SERVER_ERROR: frozenset(
            {
                ErrorCode.API_ERROR,
                ErrorCode.INTERNAL_SERVER_ERROR,
            }
        ),
        HTTPStatus.SERVICE_UNAVAILABLE: frozenset({ErrorCode.SERVICE_UNAVAILABLE}),
    }

    assert expected == ERROR_STATUS_REGISTRY
