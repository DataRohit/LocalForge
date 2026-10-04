"""Browser-facing response security and cross-origin policy.

Applies the project's content security policy and exact-origin credentialed CORS behavior to every
response, including failures returned by middleware before a Django view runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from ipaddress import ip_address
from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.http import HttpResponse
from django.urls import Resolver404, resolve
from django.utils.cache import patch_vary_headers
from django.utils.decorators import async_only_middleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from asgiref.typing import Scope
    from django.http import HttpRequest
    from django.http.response import HttpResponseBase

CORS_ALLOWED_HEADERS = (
    "Accept",
    "Authorization",
    "Content-Type",
    "X-CSRFToken",
    "X-Request-ID",
)
CORS_EXPOSED_HEADERS = (
    "Retry-After",
    "X-Request-ID",
)


@async_only_middleware
def trusted_proxy_headers_middleware(
    get_response: Callable[[HttpRequest], Awaitable[HttpResponseBase]],
) -> Callable[[HttpRequest], Awaitable[HttpResponseBase]]:
    """Accept forwarded transport headers only from configured proxy networks.

    Removes spoofable forwarded protocol metadata before Django evaluates secure-request settings,
    preserving the edge contract for direct loopback and other untrusted peers.

    Arguments:
        get_response: Asynchronous inner middleware chain.

    Returns:
        Middleware that sanitizes forwarded protocol metadata.
    """

    async def sanitize(request: HttpRequest) -> HttpResponseBase:
        """Sanitize one request's forwarded protocol metadata.

        Removes protocol claims from untrusted peers before downstream middleware sees the request.
        Preserves protocol claims from the configured Traefik network.

        Arguments:
            request: Incoming Django request.

        Returns:
            Response from the inner middleware chain.
        """
        peer = request.META.get("REMOTE_ADDR")
        try:
            peer_address = ip_address(peer) if isinstance(peer, str) else None
        except ValueError:
            peer_address = None
        if peer_address is None or not any(
            peer_address in network for network in settings.TRUSTED_PROXY_NETWORKS
        ):
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
        return await get_response(request)

    return sanitize


@async_only_middleware
def public_cookie_security_middleware(
    get_response: Callable[[HttpRequest], Awaitable[HttpResponseBase]],
) -> Callable[[HttpRequest], Awaitable[HttpResponseBase]]:
    """Mark browser cookies secure only on the canonical HTTPS edge.

    Keeps the local development hostname usable over HTTP while applying secure cookies to public
    responses whose host and trusted proxy transport both match the canonical edge.

    Arguments:
        get_response: Asynchronous inner middleware chain.

    Returns:
        Middleware that applies the public cookie transport policy.
    """

    async def secure_cookies(request: HttpRequest) -> HttpResponseBase:
        """Apply secure attributes to cookies on one public HTTPS response.

        Limits secure attributes to the canonical host and trusted HTTPS transport.
        This preserves local HTTP administration while protecting public browser credentials.

        Arguments:
            request: Incoming Django request.

        Returns:
            Response with secure cookie attributes when the canonical public transport applies.
        """
        response = await get_response(request)
        if request.get_host() == "localforge.datarohit.com" and request.is_secure():
            for cookie in response.cookies.values():
                cookie["secure"] = True
        return response

    return secure_cookies


@dataclass(frozen=True, slots=True)
class RequestOrigin:
    """Represent safe origin reflection separately from header presence.

    Stores one value only when transport supplied exactly one unambiguous Origin while retaining
    whether any Origin header was present so ambiguous requests still receive cache variance.

    Attributes:
        value: Single origin safe to compare with the allowlist, or ``None``.
        supplied: Whether transport supplied one or more Origin headers.

    Members:
        None.
    """

    value: str | None
    supplied: bool


def request_origin_from_scope(scope: Scope | Mapping[str, object]) -> RequestOrigin:
    """Parse Origin presence and one safe value from an ASGI scope.

    Treats repeated Origin fields as supplied but ambiguous, preventing reflection while preserving
    the required ``Vary: Origin`` contract for every browser-origin request.

    Arguments:
        scope: Incoming ASGI connection scope.

    Returns:
        Origin presence and optional unambiguous value.
    """
    values = [
        value
        for name, value in cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        if name.lower() == b"origin"
    ]

    return RequestOrigin(
        value=values[0].decode("latin-1") if len(values) == 1 else None,
        supplied=bool(values),
    )


def request_origin_from_django(request: HttpRequest) -> RequestOrigin:
    """Parse Origin presence and one safe value from a Django request.

    Uses the original ASGI scope when available so repeated fields remain distinguishable, then
    falls back to Django metadata for direct WSGI and test-client calls.

    Arguments:
        request: Incoming Django request.

    Returns:
        Origin presence and optional unambiguous value.
    """
    scope = getattr(request, "scope", None)
    if isinstance(scope, Mapping):
        return request_origin_from_scope(scope)

    supplied = "HTTP_ORIGIN" in request.META
    raw = request.META.get("HTTP_ORIGIN")
    value = raw if isinstance(raw, str) and "," not in raw else None

    return RequestOrigin(value=value, supplied=supplied)


def browser_security_headers() -> dict[str, str]:
    """Return every project-owned browser hardening header.

    Reads the explicit Django settings so ordinary middleware responses and ASGI-level failures can
    share one content, frame, referrer, and content-security-policy contract.

    Arguments:
        None.

    Returns:
        Header names mapped to their configured values.
    """
    return {
        "Content-Security-Policy": settings.CONTENT_SECURITY_POLICY,
        "Referrer-Policy": settings.SECURE_REFERRER_POLICY,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": settings.X_FRAME_OPTIONS,
    }


def allowed_cors_origin(origin: str | None) -> str | None:
    """Select one exact configured origin.

    Compares one supplied origin byte-for-byte against the environment-derived tuple and returns
    nothing for missing or unlisted values, never reflecting a wildcard or suffix match.

    Arguments:
        origin: Browser origin value, or ``None`` when absent.

    Returns:
        Exact allowed origin, or ``None``.
    """
    return (
        origin
        if isinstance(origin, str)
        and origin in cast("tuple[str, ...]", settings.CORS_ALLOWED_ORIGINS)
        else None
    )


def apply_response_security(
    response: HttpResponseBase,
    request_origin: RequestOrigin,
) -> HttpResponseBase:
    """Attach browser security and exact-origin CORS headers.

    Sets project-owned hardening defaults on every response and adds credentialed cross-origin
    headers only when the request origin exactly matches the configured allowlist.

    Arguments:
        response: Response returned by the inner middleware chain.
        request_origin: Parsed origin presence and optional safe reflection value.

    Returns:
        Response carrying the applicable headers.
    """
    for name, value in browser_security_headers().items():
        response.headers.setdefault(name, value)

    origin = allowed_cors_origin(request_origin.value)
    if origin is not None:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Expose-Headers"] = ", ".join(CORS_EXPOSED_HEADERS)
        if settings.CORS_ALLOW_CREDENTIALS:
            response.headers["Access-Control-Allow-Credentials"] = "true"

    if request_origin.supplied:
        patch_vary_headers(response, ("Origin",))

    return response


def _resolved_api_methods(request: HttpRequest) -> tuple[str, ...] | None:
    """Return methods implemented by one resolvable versioned API operation.

    Resolves the normal Django route and inspects its API view class, excluding unknown,
    administration, health, and any other route outside the versioned API namespace.

    Arguments:
        request: Incoming preflight request.

    Returns:
        Sorted concrete methods including automatic HEAD and OPTIONS, or ``None``.
    """
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None

    if "api-v1" not in match.namespaces:
        return None

    view_class = getattr(match.func, "view_class", None)
    if view_class is None:
        return None

    methods = {
        method.upper()
        for method in cast("tuple[str, ...]", view_class.http_method_names)
        if hasattr(view_class, method)
    }
    if "GET" in methods:
        methods.add("HEAD")
    methods.add("OPTIONS")

    return tuple(sorted(methods))


@async_only_middleware
def browser_security_middleware(
    get_response: Callable[[HttpRequest], Awaitable[HttpResponseBase]],
) -> Callable[[HttpRequest], Awaitable[HttpResponseBase]]:
    """Build the browser security and CORS response boundary.

    Answers allowed browser preflights before authentication because preflight carries no
    credential, then applies the same hardening and origin policy to every ordinary response.

    Arguments:
        get_response: Asynchronous inner middleware chain.

    Returns:
        Asynchronous middleware applying browser response security.
    """

    async def secure(request: HttpRequest) -> HttpResponseBase:
        """Apply browser policy to one request.

        Returns a bodyless preflight response for one exact allowed origin and otherwise delegates
        to the application before attaching the common security headers.

        Arguments:
            request: Incoming Django request.

        Returns:
            Secured preflight or application response.
        """
        request_origin = request_origin_from_django(request)
        origin = allowed_cors_origin(request_origin.value)
        requested_method = request.META.get("HTTP_ACCESS_CONTROL_REQUEST_METHOD")
        methods = _resolved_api_methods(request)

        if (
            request.method == "OPTIONS"
            and origin is not None
            and isinstance(requested_method, str)
            and methods is not None
        ):
            response: HttpResponseBase = HttpResponse(status=HTTPStatus.NO_CONTENT)
            response.headers["Access-Control-Allow-Methods"] = ", ".join(methods)
            response.headers["Access-Control-Allow-Headers"] = ", ".join(CORS_ALLOWED_HEADERS)
        else:
            response = await get_response(request)

        return apply_response_security(response, request_origin)

    return secure
