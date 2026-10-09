"""Browser credential cookies and request forgery defence.

Names the three browser cookies, writes and clears them with one fixed attribute set, enforces the
double-submit CSRF check on cookie-authenticated unsafe requests, and refuses requests that carry
two credential sources.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from rest_framework.exceptions import PermissionDenied

from config.api_errors import AmbiguousCredentials
from config.security import request_origin_from_django

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponseBase
    from rest_framework.request import Request

SAME_SITE: Literal["Strict"] = "Strict"
ROOT_PATH = "/"
REFRESH_PATH = "/api/v1/browser/"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class CsrfCheck(CsrfViewMiddleware):
    """Run Django's CSRF validation outside the middleware chain.

    Inherits from ``CsrfViewMiddleware`` and returns the rejection reason instead of building a
    response, so a REST authenticator can turn it into the shared error envelope.

    Attributes:
        None beyond those inherited from ``CsrfViewMiddleware``.

    Members:
        _reject: Return the rejection reason to the caller instead of building a response.
    """

    def _reject(self, request: HttpRequest, reason: str) -> HttpResponseBase:
        """Return the rejection reason instead of an error response.

        Lets the caller decide how a failed check is rendered while keeping Django's origin,
        referer, and token comparison as the single source of truth.

        Arguments:
            request: Request that failed validation.
            reason: Internal diagnostic naming the failed comparison.

        Returns:
            The reason, typed as a response so the base signature is preserved.
        """
        del request

        return cast("HttpResponseBase", reason)


def access_cookie_name() -> str:
    """Return the configured access cookie name.

    Reads the validated ``__Host-`` name so no caller hardcodes it.
    Keeps the configured name in one place for every writer and reader.

    Arguments:
        None.

    Returns:
        Access cookie name.
    """
    return cast("str", settings.BROWSER_ACCESS_COOKIE_NAME)


def refresh_cookie_name() -> str:
    """Return the configured refresh cookie name.

    Reads the validated ``__Secure-`` name so no caller hardcodes it.
    Keeps the configured name in one place for every writer and reader.

    Arguments:
        None.

    Returns:
        Refresh cookie name.
    """
    return cast("str", settings.BROWSER_REFRESH_COOKIE_NAME)


def csrf_cookie_name() -> str:
    """Return the configured CSRF cookie name.

    Reads the validated ``__Host-`` name so no caller hardcodes it.
    Keeps the configured name in one place for every writer and reader.

    Arguments:
        None.

    Returns:
        CSRF cookie name.
    """
    return cast("str", settings.BROWSER_CSRF_COOKIE_NAME)


def set_credential_cookies(response: HttpResponseBase, *, access: str, refresh: str) -> None:
    """Write the access and refresh cookies with their fixed attributes.

    Marks both ``HttpOnly``, ``Secure``, and ``SameSite=Strict``, scopes the access cookie to the
    whole site and the refresh cookie to the browser route group, and bounds each by its token life.

    Arguments:
        response: Response receiving the cookies.
        access: Encoded access token.
        refresh: Encoded refresh token.

    Returns:
        None.
    """
    response.set_cookie(
        access_cookie_name(),
        access,
        max_age=cast("int", settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS),
        path=ROOT_PATH,
        secure=True,
        httponly=True,
        samesite=SAME_SITE,
    )
    response.set_cookie(
        refresh_cookie_name(),
        refresh,
        max_age=cast("int", settings.JWT_REFRESH_TOKEN_LIFETIME_SECONDS),
        path=REFRESH_PATH,
        secure=True,
        httponly=True,
        samesite=SAME_SITE,
    )


def clear_credential_cookies(response: HttpResponseBase) -> None:
    """Expire all three browser cookies.

    Deletes each cookie on the exact path it was set on, because a browser ignores a deletion
    whose path or prefix attributes differ from the original.

    Arguments:
        response: Response receiving the expiring cookies.

    Returns:
        None.
    """
    response.delete_cookie(access_cookie_name(), path=ROOT_PATH, samesite=SAME_SITE)
    response.delete_cookie(refresh_cookie_name(), path=REFRESH_PATH, samesite=SAME_SITE)
    response.delete_cookie(csrf_cookie_name(), path=ROOT_PATH, samesite=SAME_SITE)


def carries_access_cookie(request: Request) -> bool:
    """Report whether the request carries a non-empty access cookie.

    Reports whether the browser sent a usable access credential.
    Treats an empty value as absent, so a cleared cookie never counts as one.

    Arguments:
        request: Request under inspection.

    Returns:
        Whether the access cookie is present.
    """
    return bool(request.COOKIES.get(access_cookie_name()))


def reject_ambiguous_credentials(request: Request) -> None:
    """Refuse a request that presents an authorization header and the access cookie.

    Exactly one credential source is ever accepted, so a request that carries both is rejected
    with the shared bad-request envelope rather than preferring either.

    Arguments:
        request: Request under inspection.

    Returns:
        None.

    Raises:
        AmbiguousCredentials: If both credential sources are present.
    """
    if request.META.get("HTTP_AUTHORIZATION") and carries_access_cookie(request):
        raise AmbiguousCredentials


def enforce_csrf(request: Request) -> None:
    """Validate the double-submit token and the request origin.

    Skips safe methods, otherwise requires an ``Origin`` header and runs Django's own token and
    origin comparison on the underlying request, so REST framework parsers never read a form body.

    Arguments:
        request: Cookie-authenticated request.

    Returns:
        None.

    Raises:
        PermissionDenied: If the origin is absent or untrusted, or the token is missing or wrong.
    """
    django_request = cast("HttpRequest", request._request)  # noqa: SLF001
    if django_request.method in SAFE_METHODS or getattr(
        django_request, "_dont_enforce_csrf_checks", False
    ):
        return

    if not request_origin_from_django(django_request).supplied:
        raise PermissionDenied

    check = CsrfCheck(HttpResponse)
    check.process_request(django_request)
    reason = check.process_view(django_request, HttpResponse, (), {})
    if reason:
        raise PermissionDenied
