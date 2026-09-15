"""Shared HTTP API contract.

Defines the versioned routing boundary, stable error vocabulary, REST framework exception mapping,
and Django handlers that keep failures outside REST framework in the same correlated JSON shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, JsonResponse
from django.utils.decorators import async_only_middleware
from django.views import csrf, defaults
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
from rest_framework.views import exception_handler as drf_exception_handler

from config.logs import REQUEST_ID_META_KEY, request_identifier

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase
    from django.urls import URLPattern, URLResolver
    from rest_framework.response import Response

API_VERSION = "v1"
API_PREFIX = f"api/{API_VERSION}/"
NON_FIELD_ERRORS = "non_field_errors"
ERROR_ENVELOPE_ATTRIBUTE = "localforge_error_envelope"

app_name = "api"
urlpatterns: list[URLPattern | URLResolver] = []


class ErrorCode(StrEnum):
    """Enumerate every machine-readable HTTP API error code.

    Inherits from ``StrEnum`` so codes serialize as JSON strings while remaining impossible to
    invent as unchecked literals at response call sites.

    Attributes:
        API_ERROR: Framework failure without a more specific public category.
        AUTHENTICATION_FAILED: Credentials were supplied but rejected.
        BAD_REQUEST: Request rejection outside REST framework parsing.
        INTERNAL_SERVER_ERROR: Sanitized unexpected server failure.
        METHOD_NOT_ALLOWED: HTTP method is unsupported for the route.
        NOT_ACCEPTABLE: Requested response representation is unavailable.
        NOT_AUTHENTICATED: Required credentials were not supplied.
        NOT_FOUND: Route or resource was not found.
        PARSE_ERROR: Request body could not be parsed.
        PERMISSION_DENIED: Caller may not perform the operation.
        THROTTLED: Request exceeded an applicable rate limit.
        UNSUPPORTED_MEDIA_TYPE: Request representation is unsupported.
        VALIDATION_ERROR: Submitted fields failed validation.

    Members:
        None beyond those inherited from ``StrEnum``.
    """

    API_ERROR = "api_error"
    AUTHENTICATION_FAILED = "authentication_failed"
    BAD_REQUEST = "bad_request"
    INTERNAL_SERVER_ERROR = "internal_server_error"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    NOT_ACCEPTABLE = "not_acceptable"
    NOT_AUTHENTICATED = "not_authenticated"
    NOT_FOUND = "not_found"
    PARSE_ERROR = "parse_error"
    PERMISSION_DENIED = "permission_denied"
    THROTTLED = "throttled"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    VALIDATION_ERROR = "validation_error"


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """Pair a stable error code with its safe human-readable message.

    Carries the two client-facing values that must change together and prevents exception handlers
    from deriving public text from implementation-specific exception details.

    Attributes:
        code: Stable machine-readable error code.
        message: Safe human-readable explanation.

    Members:
        None.
    """

    code: ErrorCode
    message: str


API_ERROR = ErrorDefinition(
    ErrorCode.API_ERROR,
    "The request could not be completed.",
)
AUTHENTICATION_FAILED = ErrorDefinition(
    ErrorCode.AUTHENTICATION_FAILED,
    "Authentication failed.",
)
BAD_REQUEST = ErrorDefinition(
    ErrorCode.BAD_REQUEST,
    "The request was invalid.",
)
INTERNAL_SERVER_ERROR = ErrorDefinition(
    ErrorCode.INTERNAL_SERVER_ERROR,
    "An unexpected error occurred.",
)
METHOD_NOT_ALLOWED = ErrorDefinition(
    ErrorCode.METHOD_NOT_ALLOWED,
    "The requested method is not allowed.",
)
NOT_ACCEPTABLE = ErrorDefinition(
    ErrorCode.NOT_ACCEPTABLE,
    "The requested response format is not available.",
)
NOT_AUTHENTICATED = ErrorDefinition(
    ErrorCode.NOT_AUTHENTICATED,
    "Authentication credentials were not provided.",
)
NOT_FOUND = ErrorDefinition(
    ErrorCode.NOT_FOUND,
    "The requested resource was not found.",
)
PARSE_ERROR = ErrorDefinition(
    ErrorCode.PARSE_ERROR,
    "The request body could not be parsed.",
)
PERMISSION_DENIED = ErrorDefinition(
    ErrorCode.PERMISSION_DENIED,
    "You do not have permission to perform this action.",
)
THROTTLED = ErrorDefinition(
    ErrorCode.THROTTLED,
    "Too many requests.",
)
UNSUPPORTED_MEDIA_TYPE = ErrorDefinition(
    ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    "The request content type is not supported.",
)
VALIDATION_ERROR = ErrorDefinition(
    ErrorCode.VALIDATION_ERROR,
    "The submitted data is invalid.",
)

STATUS_DEFINITIONS: dict[int, ErrorDefinition] = {
    HTTPStatus.BAD_REQUEST: BAD_REQUEST,
    HTTPStatus.UNAUTHORIZED: NOT_AUTHENTICATED,
    HTTPStatus.FORBIDDEN: PERMISSION_DENIED,
    HTTPStatus.NOT_FOUND: NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: METHOD_NOT_ALLOWED,
    HTTPStatus.NOT_ACCEPTABLE: NOT_ACCEPTABLE,
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: UNSUPPORTED_MEDIA_TYPE,
    HTTPStatus.TOO_MANY_REQUESTS: THROTTLED,
    HTTPStatus.INTERNAL_SERVER_ERROR: INTERNAL_SERVER_ERROR,
}

EXCEPTION_DEFINITIONS: dict[type[Exception], ErrorDefinition] = {
    ValidationError: VALIDATION_ERROR,
    ParseError: PARSE_ERROR,
    AuthenticationFailed: AUTHENTICATION_FAILED,
    NotAuthenticated: NOT_AUTHENTICATED,
    PermissionDenied: PERMISSION_DENIED,
    DjangoPermissionDenied: PERMISSION_DENIED,
    NotFound: NOT_FOUND,
    Http404: NOT_FOUND,
    MethodNotAllowed: METHOD_NOT_ALLOWED,
    NotAcceptable: NOT_ACCEPTABLE,
    UnsupportedMediaType: UNSUPPORTED_MEDIA_TYPE,
    Throttled: THROTTLED,
    APIException: API_ERROR,
}


def _request_identifier(request: HttpRequest) -> str:
    """Read the request identifier established by correlation middleware.

    Prefers request metadata because it survives exception conversion and falls back to the active
    context for direct boundary calls made before metadata is available.

    Arguments:
        request: Incoming Django request.

    Returns:
        Correlation identifier or the outside-request sentinel.

    Raises:
        None.
    """
    identifier = request.META.get(REQUEST_ID_META_KEY)

    if isinstance(identifier, str):
        return identifier

    return request_identifier.get()


def _is_api_request(request: HttpRequest) -> bool:
    """Identify requests owned by the versioned REST API.

    Uses the single routing-prefix constant so Django-level handlers and response middleware agree
    on the boundary where the JSON error contract applies.

    Arguments:
        request: Incoming Django request.

    Returns:
        Whether the request path is beneath the versioned API prefix.

    Raises:
        None.
    """
    return request.path.startswith(f"/{API_PREFIX}")


def _definition_for(exception: Exception) -> ErrorDefinition:
    """Select the public definition for one handled framework exception.

    Reads the central exact-type mapping so specific framework failures retain distinct codes while
    future exception subclasses receive the stable generic fallback.

    Arguments:
        exception: Exception converted to a REST framework response.

    Returns:
        Matching public error definition.

    Raises:
        None.
    """
    return EXCEPTION_DEFINITIONS.get(type(exception), API_ERROR)


def _validation_details(response_data: object) -> dict[str, object]:
    """Normalize validation output into the envelope's per-field mapping.

    Preserves serializer field keys and gives list-shaped validation failures the standard
    non-field key, keeping the envelope stable for both serializer and view validation.

    Arguments:
        response_data: Validation body produced by REST framework.

    Returns:
        Per-field validation detail mapping.

    Raises:
        None.
    """
    if isinstance(response_data, Mapping):
        return {str(key): value for key, value in response_data.items()}

    return {NON_FIELD_ERRORS: response_data}


def _payload(
    request: HttpRequest,
    definition: ErrorDefinition,
    *,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the universal error envelope for one correlated request.

    Serializes only values selected by the central registry and an optional validation mapping, so
    exception text and traceback state cannot enter an unexpected-failure response.

    Arguments:
        request: Incoming request carrying correlation metadata.
        definition: Stable code and safe human-readable message.
        details: Optional per-field validation details.

    Returns:
        JSON-serializable error envelope.

    Raises:
        None.
    """
    return {
        "code": definition.code.value,
        "message": definition.message,
        "details": details or {},
        "request_id": _request_identifier(request),
    }


def api_exception_handler(
    exception: Exception,
    context: dict[str, Any],
) -> Response | None:
    """Convert REST framework exceptions to the universal error envelope.

    Delegates status and protocol headers to REST framework, then replaces its inconsistent body
    shapes while preserving validation fields and any authentication, method, or retry headers
    already attached by REST framework.

    Arguments:
        exception: Exception raised during REST framework request processing.
        context: REST framework handler context containing the incoming request.

    Returns:
        Enveloped REST framework response, or ``None`` for an unhandled exception.

    Raises:
        None.
    """
    response = drf_exception_handler(exception, context)

    if response is None:
        return None

    request = cast("HttpRequest", context["request"])
    definition = _definition_for(exception)
    details = _validation_details(response.data) if isinstance(exception, ValidationError) else None
    response.data = _payload(request, definition, details=details)
    setattr(response, ERROR_ENVELOPE_ATTRIBUTE, True)

    return cast("Response", response)


def _json_error(
    request: HttpRequest,
    definition: ErrorDefinition,
    status: int | HTTPStatus,
) -> JsonResponse:
    """Return a correlated JSON error outside REST framework.

    Uses the same payload builder as framework exceptions so routing, middleware, and unexpected
    failures cannot fall back to Django's HTML error pages.

    Arguments:
        request: Incoming Django request.
        definition: Stable code and safe human-readable message.
        status: HTTP status for the response.

    Returns:
        JSON response carrying the universal error envelope.

    Raises:
        None.
    """
    response = JsonResponse(_payload(request, definition), status=status)
    setattr(response, ERROR_ENVELOPE_ATTRIBUTE, True)

    return response


@async_only_middleware
def api_error_envelope_middleware(
    get_response: Callable[[HttpRequest], Awaitable[HttpResponseBase]],
) -> Callable[[HttpRequest], Awaitable[HttpResponseBase]]:
    """Build middleware that envelopes every versioned API error response.

    Converts debug error pages and manually returned failures after Django finishes processing,
    while leaving successful responses, non-API routes, and already enveloped framework exceptions
    unchanged.

    Arguments:
        get_response: Asynchronous inner middleware chain.

    Returns:
        Asynchronous middleware enforcing the API error contract.

    Raises:
        None.
    """

    async def envelope(request: HttpRequest) -> HttpResponseBase:
        """Enforce the universal shape on one completed response.

        Reuses the request identifier established by the outer correlation middleware and carries
        non-content headers and cookies onto the replacement JSON response.

        Arguments:
            request: Incoming Django request.

        Returns:
            Original response when exempt, otherwise an enveloped JSON replacement.

        Raises:
            None.
        """
        response = await get_response(request)

        if (
            not _is_api_request(request)
            or response.status_code < HTTPStatus.BAD_REQUEST
            or bool(getattr(response, ERROR_ENVELOPE_ATTRIBUTE, False))
        ):
            return response

        status = response.status_code
        definition = STATUS_DEFINITIONS.get(status, API_ERROR)
        enveloped = _json_error(request, definition, status)

        for name, value in response.items():
            if name.lower() not in {"content-length", "content-type"}:
                enveloped[name] = value

        enveloped.cookies.update(response.cookies)

        return enveloped

    return envelope


def api_bad_request(request: HttpRequest, exception: Exception) -> HttpResponseBase:
    """Represent only versioned API bad requests with the shared envelope.

    Handles API rejection outside REST framework while delegating every other path to Django's
    standard bad-request response.

    Arguments:
        request: Incoming Django request.
        exception: Django exception that selected the bad-request handler.

    Returns:
        Correlated API JSON response or Django's standard non-API response.

    Raises:
        None.
    """
    if not _is_api_request(request):
        return defaults.bad_request(request, exception)

    return _json_error(request, BAD_REQUEST, HTTPStatus.BAD_REQUEST)


def api_permission_denied(request: HttpRequest, exception: Exception) -> HttpResponseBase:
    """Represent only versioned API permission failures with the shared envelope.

    Covers API middleware and authorization failures without exposing exception text while
    delegating every other path to Django's standard permission response.

    Arguments:
        request: Incoming Django request.
        exception: Django exception that selected the permission handler.

    Returns:
        Correlated API JSON response or Django's standard non-API response.

    Raises:
        None.
    """
    if not _is_api_request(request):
        return defaults.permission_denied(request, exception)

    return _json_error(request, PERMISSION_DENIED, HTTPStatus.FORBIDDEN)


def api_not_found(request: HttpRequest, exception: Exception) -> HttpResponseBase:
    """Represent only unknown versioned API paths with the shared envelope.

    Replaces API routing responses with stable JSON while delegating misses outside the versioned
    prefix to Django's standard not-found response.

    Arguments:
        request: Incoming Django request.
        exception: Django routing exception that selected the not-found handler.

    Returns:
        Correlated API JSON response or Django's standard non-API response.

    Raises:
        None.
    """
    if not _is_api_request(request):
        return defaults.page_not_found(request, exception)

    return _json_error(request, NOT_FOUND, HTTPStatus.NOT_FOUND)


def api_server_error(request: HttpRequest) -> HttpResponseBase:
    """Represent only unexpected versioned API failures with the shared envelope.

    Returns only the central generic API message and correlation identifier while delegating every
    other path to Django's standard server-error response.

    Arguments:
        request: Incoming Django request.

    Returns:
        Correlated API JSON response or Django's standard non-API response.

    Raises:
        None.
    """
    if not _is_api_request(request):
        return defaults.server_error(request)

    return _json_error(request, INTERNAL_SERVER_ERROR, HTTPStatus.INTERNAL_SERVER_ERROR)


def api_csrf_failure(request: HttpRequest, *, reason: str = "") -> HttpResponseBase:
    """Represent only versioned API CSRF rejection with the shared envelope.

    Excludes Django's diagnostic reason from API responses while delegating every other path to the
    framework's standard CSRF failure view.

    Arguments:
        request: Incoming Django request.
        reason: Internal CSRF rejection reason supplied by Django.

    Returns:
        Correlated API JSON response or Django's standard non-API response.

    Raises:
        None.
    """
    if not _is_api_request(request):
        return csrf.csrf_failure(request, reason=reason)

    return _json_error(request, PERMISSION_DENIED, HTTPStatus.FORBIDDEN)
