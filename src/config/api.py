"""Shared HTTP API boundary.

Defines versioned routing, REST framework exception conversion, request-size enforcement, and
Django handlers that keep failures outside REST framework in the same correlated JSON shape.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from http import HTTPStatus
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, cast, override

from django.conf import settings
from django.http import JsonResponse
from django.middleware.common import CommonMiddleware
from django.urls import include, path
from django.utils.decorators import async_only_middleware
from django.views import csrf, defaults
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

from accounts.api_throttling import boundary_address_admission
from accounts.request_throttling import trusted_client_address_from_scope
from config.api_errors import (
    API_ERROR,
    BAD_REQUEST,
    EXCEPTION_DEFINITIONS,
    INTERNAL_SERVER_ERROR,
    NOT_FOUND,
    PERMISSION_DENIED,
    REQUEST_TOO_LARGE,
    STATUS_DEFINITIONS,
    THROTTLED,
    ErrorDefinition,
)
from config.api_errors import ERROR_STATUS_REGISTRY as _ERROR_STATUS_REGISTRY
from config.api_errors import ErrorCode as _ErrorCode
from config.logs import REQUEST_ID_META_KEY, request_identifier
from config.security import RequestOrigin, apply_response_security, request_origin_from_scope

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
    from django.urls import URLPattern, URLResolver
    from rest_framework.response import Response

API_VERSION = "v1"
API_PREFIX = f"api/{API_VERSION}/"
API_BOUNDARY_ADMISSION_WORKERS = 5
BOUNDARY_ADMISSION_SIGNAL_INTERVAL_SECONDS = 60.0
BOUNDARY_ADMISSION_SATURATED = (
    "general API admission capacity exhausted; serving without cache admission"
)
NON_FIELD_ERRORS = "non_field_errors"
ERROR_ENVELOPE_ATTRIBUTE = "localforge_error_envelope"
ERROR_STATUS_REGISTRY = _ERROR_STATUS_REGISTRY
ErrorCode = _ErrorCode
Arguments = ParamSpec("Arguments")
Result = TypeVar("Result")
logger = logging.getLogger(__name__)

app_name = "api"
urlpatterns: list[URLPattern | URLResolver] = [
    path("", include("accounts.urls")),
]


class BoundaryAdmissionCapacityError(RuntimeError):
    """Report exhausted general-admission execution capacity.

    Inherits from ``RuntimeError`` and marks the bounded fail-open condition separately from cache
    errors returned by the admission operation itself.

    Attributes:
        None beyond those the base exception defines.

    Members:
        None.
    """


class BoundaryAdmissionExecutor(ThreadPoolExecutor):
    """Run synchronous general admission with bounded dedicated capacity.

    Inherits from ``ThreadPoolExecutor`` and admits at most one submitted operation per worker,
    preventing slow Valkey calls from creating an executor queue or occupying unrelated workers.

    Attributes:
        None beyond those the base executor defines.

    Members:
        submit: Schedule one context-preserving admission operation without blocking for capacity.
    """

    def __init__(self, *, max_workers: int) -> None:
        """Create an executor with equal worker and admission limits.

        Uses one semaphore slot per worker so accepting a submission guarantees bounded executor
        occupancy without waiting for capacity.

        Arguments:
            max_workers: Positive maximum concurrent admission operations.

        Returns:
            None.

        Raises:
            ValueError: If the worker count is not positive.
        """
        super().__init__(max_workers=max_workers)
        self._admission = BoundedSemaphore(max_workers)

    @override
    def submit(
        self,
        fn: Callable[Arguments, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Future[Result]:
        """Submit one operation only when a worker slot is available.

        Captures the caller's context for correlated cache diagnostics and returns an already-failed
        future on saturation so the asynchronous boundary can apply fail-open behavior immediately.

        Arguments:
            fn: Callable to execute.
            *args: Positional arguments for the callable.
            **kwargs: Keyword arguments for the callable.

        Returns:
            Future representing submitted work or immediate capacity exhaustion.

        Raises:
            RuntimeError: If the executor has already shut down.
        """
        if not self._admission.acquire(blocking=False):
            failed: Future[Result] = Future()
            failed.set_exception(
                BoundaryAdmissionCapacityError("boundary admission capacity exhausted")
            )

            return failed

        context = copy_context()
        try:
            future = super().submit(lambda: context.run(fn, *args, **kwargs))
        except RuntimeError:
            self._admission.release()
            raise
        future.add_done_callback(lambda _future: self._admission.release())

        return future


class OperationalSignal:
    """Rate-limit one fixed operational warning.

    Stores no request data and serializes emission so concurrent saturation can produce at most one
    fixed warning per interval without enlarging the admission queue.

    Attributes:
        interval_seconds: Minimum duration between emitted warnings.

    Members:
        emit: Log one fixed warning when the interval has elapsed.
    """

    def __init__(self, *, interval_seconds: float) -> None:
        """Initialize one rate-limited signal.

        Starts with no prior emission and uses a lock to serialize concurrent interval checks.
        The signal stores no request-derived values.

        Arguments:
            interval_seconds: Positive minimum duration between warnings.

        Returns:
            None.

        Raises:
            ValueError: If the interval is not positive.
        """
        if interval_seconds <= 0:
            message = "operational signal interval must be positive"
            raise ValueError(message)

        self.interval_seconds = interval_seconds
        self._last_emitted = float("-inf")
        self._lock = Lock()

    def emit(self, message: str) -> None:
        """Emit one fixed warning when its interval permits.

        Suppresses calls inside the configured interval and logs only the supplied constant after
        the interval has elapsed.

        Arguments:
            message: Constant redacted operational message.

        Returns:
            None.
        """
        now = monotonic()
        with self._lock:
            if now - self._last_emitted < self.interval_seconds:
                return
            self._last_emitted = now

        logger.warning(message)


boundary_admission_executor = BoundaryAdmissionExecutor(max_workers=API_BOUNDARY_ADMISSION_WORKERS)
boundary_admission_saturation_signal = OperationalSignal(
    interval_seconds=BOUNDARY_ADMISSION_SIGNAL_INTERVAL_SECONDS
)


class ApiCommonMiddleware(CommonMiddleware):
    """Preserve canonical redirects outside the versioned API.

    Inherits from Django's ``CommonMiddleware`` and suppresses append-slash redirects only beneath
    the API prefix, allowing the routing error boundary to return its correlated JSON envelope.

    Attributes:
        None beyond those inherited from ``CommonMiddleware``.

    Members:
        should_redirect_with_slash: Decide whether a slashless request may redirect.
    """

    @override
    def should_redirect_with_slash(self, request: HttpRequest) -> bool:
        """Decide whether Django may append a slash to one request.

        Rejects redirects for all versioned API paths while delegating non-API behavior unchanged
        to Django, including administration and other framework routes.

        Arguments:
            request: Incoming Django request.

        Returns:
            Whether CommonMiddleware should redirect to a slash-appended path.
        """
        return not _is_api_request(request) and super().should_redirect_with_slash(request)


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


def add_throttle_response_headers(
    result: dict[str, Any],
    generator: object,
    request: object,
    public: object,
) -> dict[str, Any]:
    """Attach exact protocol headers to every documented throttle response.

    Walks generated operations centrally so every current and future route that documents 429 also
    declares the retry delay and correlated request identifier clients receive at runtime.

    Arguments:
        result: Generated OpenAPI document.
        generator: Schema generator invoking the post-processing hook.
        request: Optional schema-generation request.
        public: Whether the schema was generated without request-specific filtering.

    Returns:
        Generated document with exact 429 response headers.
    """
    del generator, request, public
    retry_headers = {
        "Retry-After": {
            "description": "Whole seconds until the fixed or rolling admission window recovers.",
            "schema": {"type": "integer", "minimum": 1},
        },
        "X-Request-ID": {
            "description": "Request correlation identifier echoed by the error envelope.",
            "schema": {"type": "string", "format": "uuid"},
        },
    }
    paths = result.get("paths", {})
    if not isinstance(paths, Mapping):
        return result

    for path_item in paths.values():
        if not isinstance(path_item, Mapping):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            throttled = responses.get("429")
            if isinstance(throttled, dict):
                throttled["headers"] = retry_headers

    return result


def _request_content_length(request: HttpRequest) -> int | None:
    """Read a valid non-negative request body length.

    Treats an absent or malformed server value as unknown so this preflight never consumes the
    request stream or invents a size the transport did not provide.

    Arguments:
        request: Incoming Django request.

    Returns:
        Declared body length, or ``None`` when no usable value exists.

    Raises:
        None.
    """
    raw_length = request.META.get("CONTENT_LENGTH")

    return int(raw_length) if isinstance(raw_length, str) and raw_length.isdecimal() else None


def _scope_content_length(scope: Scope) -> int | None:
    """Read one valid non-negative ASGI content length.

    Accepts exactly one decimal header and treats missing, duplicated, non-ASCII, or malformed
    values as unknown so actual receive events remain authoritative.

    Arguments:
        scope: Incoming ASGI connection scope.

    Returns:
        Declared body length, or ``None`` when no usable value exists.

    Raises:
        None.
    """
    values = [
        value
        for name, value in cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        if name.lower() == b"content-length"
    ]

    if len(values) != 1 or not values[0].isdigit():
        return None

    return int(values[0])


def _scope_without_invalid_content_length(scope: Scope, declared_length: int | None) -> Scope:
    """Remove malformed content-length metadata before Django middleware sees it.

    Preserves absent and valid declarations while treating invalid transport metadata as unknown,
    allowing the byte-counting receive boundary to decide from actual body events.

    Arguments:
        scope: Incoming ASGI connection scope.
        declared_length: Valid parsed length, or ``None`` when no usable value exists.

    Returns:
        Original scope when metadata is absent or valid, otherwise a copy without invalid headers.

    Raises:
        None.
    """
    headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
    has_content_length = any(name.lower() == b"content-length" for name, _value in headers)

    if declared_length is not None or not has_content_length:
        return scope

    return cast(
        "Scope",
        {
            **scope,
            "headers": [
                (name, value) for name, value in headers if name.lower() != b"content-length"
            ],
        },
    )


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
    return _error_payload(
        _request_identifier(request),
        definition,
        details=details,
    )


def _error_payload(
    identifier: str,
    definition: ErrorDefinition,
    *,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the universal error envelope from transport-level correlation.

    Supports boundaries that reject before Django can construct an ``HttpRequest`` while retaining
    the same stable code, safe message, detail mapping, and request identifier.

    Arguments:
        identifier: Correlation identifier established at the ASGI boundary.
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
        "request_id": identifier,
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


def api_request_body_limit_asgi(application: ASGI3Application) -> ASGI3Application:
    """Build an ASGI boundary that enforces the API request-body ceiling.

    Rejects oversized declarations before reading transport data and counts actual HTTP body events
    when length is absent, invalid, understated, or delivered across multiple chunks.

    Arguments:
        application: Django-compatible ASGI application receiving accepted requests.

    Returns:
        ASGI application enforcing the versioned API byte ceiling.

    Raises:
        None.
    """

    async def enforce_limit(
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Enforce the body ceiling for one ASGI request.

        Passes non-API scopes and accepted receive events through unchanged, while presenting
        Django with a disconnect at the first chunk crossing the limit so it closes its body spool.

        Arguments:
            scope: Incoming ASGI connection scope.
            receive: Callable yielding inbound ASGI events.
            send: Callable accepting outbound ASGI events.

        Returns:
            None.

        Raises:
            BaseException: Any accepted application or transport failure.
        """
        if scope["type"] != "http" or not scope.get("path", "").startswith(f"/{API_PREFIX}"):
            await application(scope, receive, send)
            return

        limit = cast("int", settings.API_REQUEST_BODY_MAX_BYTES)
        declared_length = _scope_content_length(scope)

        if declared_length is not None and declared_length > limit:
            await _send_asgi_request_too_large(send, request_origin_from_scope(scope))
            return

        accepted_scope = _scope_without_invalid_content_length(scope, declared_length)
        received_bytes = 0
        limit_exceeded = False

        async def count_bytes() -> ASGIReceiveEvent:
            """Count one inbound request event before passing it to Django.

            Measures only HTTP body bytes and leaves accepted event dictionaries unchanged.
            Crossing the ceiling becomes a disconnect so Django closes its open request spool.

            Arguments:
                None.

            Returns:
                Original event within the ceiling, or a disconnect when its body crosses it.

            Raises:
                BaseException: Any receive failure from the transport.
            """
            nonlocal limit_exceeded, received_bytes
            event = await receive()

            if event["type"] == "http.request":
                received_bytes += len(event.get("body", b""))
                if received_bytes > limit:
                    limit_exceeded = True
                    return {"type": "http.disconnect"}

            return event

        await application(accepted_scope, count_bytes, send)

        if limit_exceeded:
            await _send_asgi_request_too_large(send, request_origin_from_scope(scope))

    return enforce_limit


async def _send_asgi_request_too_large(
    send: ASGISendCallable,
    origin: RequestOrigin,
) -> None:
    """Send a correlated request-too-large envelope from the ASGI boundary.

    Serializes through Django's JSON response implementation so transport-level rejection matches
    every framework-level error body without constructing a request or entering REST parsing.

    Arguments:
        send: Callable accepting outbound ASGI events.
        origin: Parsed Origin presence and optional safe reflection value.

    Returns:
        None.

    Raises:
        BaseException: Any response transport failure.
    """
    response = JsonResponse(
        _error_payload(request_identifier.get(), REQUEST_TOO_LARGE),
        status=HTTPStatus.CONTENT_TOO_LARGE,
    )
    apply_response_security(response, origin)
    headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in response.items()
    ]
    await send(
        cast(
            "ASGISendEvent",
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": headers,
                "trailers": False,
            },
        )
    )
    await send(
        cast(
            "ASGISendEvent",
            {
                "type": "http.response.body",
                "body": response.content,
                "more_body": False,
            },
        )
    )


def api_boundary_throttle_asgi(application: ASGI3Application) -> ASGI3Application:
    """Build the outer ASGI source-admission boundary.

    Charges every versioned API HTTP request before body-size enforcement and Django processing,
    while running the synchronous Valkey client in a dedicated bounded worker pool.

    Arguments:
        application: Body-limit and Django application receiving admitted requests.

    Returns:
        ASGI application enforcing broad address admission.
    """

    async def admit_source(
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Admit one versioned API request before every later HTTP boundary.

        Exempts non-HTTP and non-API scopes, resolves the trusted source directly from transport
        metadata, and emits a secured correlated rejection without entering Django.

        Arguments:
            scope: Incoming ASGI connection scope.
            receive: Callable yielding inbound ASGI events.
            send: Callable accepting outbound ASGI events.

        Returns:
            None.

        Raises:
            BaseException: Any accepted application or transport failure.
        """
        if scope["type"] != "http" or not scope.get("path", "").startswith(f"/{API_PREFIX}"):
            await application(scope, receive, send)
            return

        address = trusted_client_address_from_scope(scope)
        loop = asyncio.get_running_loop()
        try:
            decision = await loop.run_in_executor(
                boundary_admission_executor,
                boundary_address_admission,
                address,
            )
        except BoundaryAdmissionCapacityError:
            boundary_admission_saturation_signal.emit(BOUNDARY_ADMISSION_SATURATED)
            await application(scope, receive, send)
            return
        if decision.admitted:
            await application(scope, receive, send)
            return

        response = JsonResponse(
            _error_payload(request_identifier.get(), THROTTLED),
            status=HTTPStatus.TOO_MANY_REQUESTS,
        )
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
        apply_response_security(response, request_origin_from_scope(scope))
        headers = [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in response.items()
        ]
        await send(
            cast(
                "ASGISendEvent",
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": headers,
                    "trailers": False,
                },
            )
        )
        await send(
            cast(
                "ASGISendEvent",
                {
                    "type": "http.response.body",
                    "body": response.content,
                    "more_body": False,
                },
            )
        )

    return admit_source


@async_only_middleware
def api_request_body_limit_middleware(
    get_response: Callable[[HttpRequest], Awaitable[HttpResponseBase]],
) -> Callable[[HttpRequest], Awaitable[HttpResponseBase]]:
    """Build middleware that rejects oversized versioned API requests.

    Compares the transport-declared length with the environment-derived ceiling before any parser
    or view reads the body, while leaving health, administration, and other non-API routes alone.

    Arguments:
        get_response: Asynchronous inner middleware chain.

    Returns:
        Asynchronous middleware enforcing the API request body ceiling.

    Raises:
        None.
    """

    async def enforce_limit(request: HttpRequest) -> HttpResponseBase:
        """Reject one oversized API request before passing control inward.

        Reads request metadata only, preserving the unread body for requests within the configured
        ceiling and avoiding allocation by application parsers for requests beyond it.

        Arguments:
            request: Incoming Django request.

        Returns:
            Content-too-large envelope or the inner response.

        Raises:
            None.
        """
        content_length = _request_content_length(request)
        limit = cast("int", settings.API_REQUEST_BODY_MAX_BYTES)

        if _is_api_request(request) and content_length is not None and content_length > limit:
            return _json_error(request, REQUEST_TOO_LARGE, HTTPStatus.CONTENT_TOO_LARGE)

        return await get_response(request)

    return enforce_limit


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
