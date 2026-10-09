"""Browser session endpoints.

Seeds the CSRF cookie and issues, rotates, and clears the HttpOnly credential cookies, so a
browser holds a session without any token reaching JavaScript, a URL, or a response body.
"""

# mypy: disable-error-code=misc

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, cast, override
from uuid import UUID

from django.db import DatabaseError, transaction
from django.middleware.csrf import get_token, rotate_token
from django.utils.cache import patch_cache_control
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

import accounts.credentials as credential_policy
from accounts.api_throttling import AnonymousApiThrottle, AuthenticationRecoveryThrottle
from accounts.browser_cookies import (
    carries_access_cookie,
    clear_credential_cookies,
    enforce_csrf,
    refresh_cookie_name,
    set_credential_cookies,
)
from accounts.jwt_authentication import PrimaryRefreshToken, rotate_refresh_token
from accounts.token_authentication import (
    ErrorEnvelopeSerializer,
    TokenLoginSerializer,
    TokenLoginThrottle,
    error_example,
    error_response,
)
from accounts.user_profiles import ACCOUNT_CAPABILITIES, UserProfileSerializer
from config.api_errors import (
    AUTHENTICATION_FAILED,
    INTERNAL_SERVER_ERROR,
    METHOD_NOT_ALLOWED,
    NOT_ACCEPTABLE,
    PARSE_ERROR,
    PERMISSION_DENIED,
    REQUEST_TOO_LARGE,
    SERVICE_UNAVAILABLE,
    THROTTLED,
    UNSUPPORTED_MEDIA_TYPE,
    VALIDATION_ERROR,
    ServiceUnavailable,
)
from notifications.delivery import publish_session_revoked

if TYPE_CHECKING:
    from django.http import HttpRequest
    from rest_framework.request import Request
    from rest_framework.throttling import BaseThrottle
    from rest_framework_simplejwt.tokens import Token

logger = logging.getLogger(__name__)

REVOCATION_BROADCAST_FAILED = "session revocation broadcast failed"
CSRF_REJECTED_DESCRIPTION = (
    "The CSRF token or Origin was missing, mismatched, or untrusted. Cookies are left in place so "
    "the client can fetch a fresh token and retry."
)


def refresh_cookie_credential(request: Request) -> str | None:
    """Read the refresh credential carried by the cookie.

    Treats an empty value as absent, so a cleared cookie and a missing cookie behave identically.
    Used by every browser route that must act on the refresh credential.

    Arguments:
        request: Request under inspection.

    Returns:
        The encoded refresh token, or None when the cookie is absent or empty.
    """
    value = request.COOKIES.get(refresh_cookie_name())

    return str(value) if value else None


def revoke_refresh_session(encoded: str) -> UUID | None:
    """Blacklist the refresh token that anchors a browser session.

    Accepts only a currently valid, not-yet-blacklisted token, writes the blacklist row on the
    primary, and treats every other input as an already ended session so logout stays repeatable.

    Arguments:
        encoded: Refresh token read from the cookie.

    Returns:
        The owning account identifier when a live session was revoked, otherwise None.

    Raises:
        ServiceUnavailable: If the authoritative database cannot record the revocation.
    """
    try:
        refresh = PrimaryRefreshToken(cast("Token", encoded))
    except TokenError, TypeError, OverflowError, OSError, ValueError:
        return None

    try:
        with transaction.atomic(using="default"):
            outstanding = (
                OutstandingToken.objects.using("default")
                .select_for_update(of=("self",))
                .filter(jti=refresh[api_settings.JTI_CLAIM])
                .first()
            )
            if outstanding is not None:
                BlacklistedToken.objects.using("default").get_or_create(token=outstanding)
    except DatabaseError as error:
        raise ServiceUnavailable from error

    try:
        return UUID(str(refresh.payload.get(api_settings.USER_ID_CLAIM)))
    except ValueError:
        return None


class BrowserSessionView(APIView):
    """Share the unauthenticated posture of the browser session routes.

    Inherits from DRF's ``APIView`` and opens credential exchange without request authentication,
    because these routes establish or end a session rather than use one.

    Attributes:
        authentication_classes: Empty because the cookie is read explicitly where needed.
        permission_classes: Public access.
        throttle_classes: Anonymous and authentication scopes.

    Members:
        get_authenticate_header: Preserve the bearer challenge on credential failure.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes: tuple[type[BaseThrottle], ...] = (
        AnonymousApiThrottle,
        AuthenticationRecoveryThrottle,
    )

    @override
    def get_authenticate_header(self, request: Request) -> str:
        """Return the bearer challenge for credential failures.

        Keeps rejected credentials at unauthorized while this public view skips authentication.
        Applies to every route in the browser session group.

        Arguments:
            request: Request being challenged.

        Returns:
            Stable bearer scheme name.
        """
        del request

        return "Bearer"


class BrowserCsrfView(BrowserSessionView):
    """Seed the readable CSRF cookie.

    Inherits from ``BrowserSessionView`` and answers a bodyless GET that sets only the CSRF cookie,
    marked uncacheable so no intermediary stores the token.

    Attributes:
        throttle_classes: Anonymous scope only, because the route changes no account state.

    Members:
        get: Set the CSRF cookie and return no body.
    """

    throttle_classes = (AnonymousApiThrottle,)

    @extend_schema(
        operation_id="browser_csrf",
        summary="Seed the CSRF cookie",
        description=(
            "Sets the readable CSRF cookie and returns no body. The browser copies the cookie "
            "value into the `X-CSRFToken` header of every unsafe request."
        ),
        request=None,
        auth=[],
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description="The CSRF cookie was set; the response is uncacheable.",
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The CSRF route accepts only GET.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.TOO_MANY_REQUESTS: error_response(
                "The client address exceeded the anonymous scope.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
        },
    )
    def get(self, request: Request) -> Response:
        """Set the CSRF cookie and return no body.

        Creates or reuses the caller's CSRF secret and lets Django's CSRF middleware write the
        cookie with the fixed attributes configured for it.

        Arguments:
            request: Anonymous REST request.

        Returns:
            Empty successful response that forbids caching.
        """
        get_token(cast("HttpRequest", request))
        response = Response(status=HTTPStatus.NO_CONTENT)
        patch_cache_control(response, no_store=True)

        return response


class BrowserLoginView(BrowserSessionView):
    """Exchange credentials for the HttpOnly credential cookies.

    Inherits from ``BrowserSessionView`` and applies the credential verification, throttling, and
    enumeration resistance of the token routes, requiring a CSRF token so a third-party site
    cannot sign a victim in to an attacker-chosen account.

    Attributes:
        throttle_classes: Strict login admission plus shared anonymous and authentication scopes.

    Members:
        perform_authentication: Enforce CSRF before any throttle is charged.
        post: Verify credentials and set the cookies.
    """

    throttle_classes = (
        TokenLoginThrottle,
        AnonymousApiThrottle,
        AuthenticationRecoveryThrottle,
    )

    @override
    def perform_authentication(self, request: Request) -> None:
        """Enforce CSRF before permissions and throttles run.

        Rejects a forged cross-site request before it can consume a victim's login budget.
        Runs before permissions and throttles, so a forged request is never charged.

        Arguments:
            request: Login request.

        Returns:
            None.

        Raises:
            PermissionDenied: If the CSRF token or Origin is invalid.
        """
        enforce_csrf(request)

    @extend_schema(
        operation_id="browser_login",
        summary="Sign in with HttpOnly cookies",
        description=(
            "Authenticates an active account without revealing whether a rejected username "
            "exists, then sets the HttpOnly access and refresh cookies and rotates the CSRF "
            "cookie. The body is the caller's profile; no token appears in any body or URL. "
            "Requires a matching `X-CSRFToken` header and a trusted `Origin`."
        ),
        request=TokenLoginSerializer,
        auth=[],
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=UserProfileSerializer,
                description="Credentials were accepted; cookies were set.",
                examples=[
                    OpenApiExample(
                        "Signed in",
                        value={
                            "id": "00000000-0000-7000-8000-000000000000",
                            "username": "river",
                            "email": "river@localforge.invalid",
                            "capabilities": list(ACCOUNT_CAPABILITIES),
                        },
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="The request body is malformed or misses a required credential field.",
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Missing username",
                        VALIDATION_ERROR,
                        details={"username": ["This field is required."]},
                    ),
                ],
            ),
            HTTPStatus.UNAUTHORIZED: error_response(
                "Credentials were rejected without disclosing account state.",
                "Authentication failed",
                AUTHENTICATION_FAILED,
            ),
            HTTPStatus.FORBIDDEN: error_response(
                CSRF_REJECTED_DESCRIPTION,
                "CSRF rejected",
                PERMISSION_DENIED,
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The browser login route accepts only POST.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE: error_response(
                "The submitted request representation is unsupported.",
                "Unsupported media type",
                UNSUPPORTED_MEDIA_TYPE,
            ),
            HTTPStatus.TOO_MANY_REQUESTS: error_response(
                "The client address or account identifier exceeded its configured login rate.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account, login-admission, or token-persistence state is "
                "unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Verify credentials, set the session cookies, and return the profile.

        Applies field validation and the bounded password policy before minting credentials, so
        inactive, unknown, and reset-required accounts receive the same generic rejection.

        Arguments:
            request: REST request carrying username and password fields.

        Returns:
            Successful response carrying only the caller's profile.

        Raises:
            AuthenticationFailed: If the submitted credentials cannot authenticate.
            ServiceUnavailable: If authoritative account, throttle, or token state is unavailable.
            ValidationError: If the submitted fields are invalid.
        """
        serializer = TokenLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            snapshot = credential_policy.verify_login_credentials(
                cast("str", serializer.validated_data["username"]),
                cast("str", serializer.validated_data["password"]),
            )
            with transaction.atomic(using="default"):
                account = credential_policy.lock_authenticated_account(snapshot)
                refresh = PrimaryRefreshToken.for_user(account)
        except DatabaseError as error:
            raise ServiceUnavailable from error

        response = Response(UserProfileSerializer(account).data, status=HTTPStatus.OK)
        set_credential_cookies(response, access=str(refresh.access_token), refresh=str(refresh))
        rotate_token(cast("HttpRequest", request))

        return response


class BrowserRefreshView(BrowserSessionView):
    """Rotate the credential cookies from the refresh cookie.

    Inherits from ``BrowserSessionView`` and treats every credential failure as the end of the
    session, clearing all three cookies so the browser cannot keep replaying a dead credential.

    Attributes:
        throttle_classes: Shared anonymous and authentication scopes.

    Members:
        signed_subject_credential: Expose the cookie credential to account throttling.
        perform_authentication: Enforce CSRF when a refresh cookie is present.
        handle_exception: Clear the cookies when authentication fails.
        post: Rotate the pair.
    """

    @staticmethod
    def signed_subject_credential(request: Request) -> str | None:
        """Expose the refresh cookie to account-level throttling.

        Lets the throttle bind the budget to the signed account without reading a request body.
        The throttle validates its signature before trusting the subject.

        Arguments:
            request: Request entering throttling.

        Returns:
            The encoded refresh token, or None.
        """
        return refresh_cookie_credential(request)

    @override
    def perform_authentication(self, request: Request) -> None:
        """Enforce CSRF when the request carries a refresh credential.

        A request without the cookie has nothing to forge and falls through to the unauthorized
        response that clears any stale cookies.

        Arguments:
            request: Refresh request.

        Returns:
            None.

        Raises:
            PermissionDenied: If the CSRF token or Origin is invalid.
        """
        if refresh_cookie_credential(request) is not None:
            enforce_csrf(request)

    @override
    def handle_exception(self, exc: Exception) -> Response:
        """Clear every browser cookie when the session cannot continue.

        Leaves the cookies untouched for CSRF rejection, throttling, and outages, which do not
        prove the session is dead.

        Arguments:
            exc: Exception raised while handling the request.

        Returns:
            Enveloped error response, carrying cookie deletions on unauthorized.
        """
        response = cast("Response", super().handle_exception(exc))
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            clear_credential_cookies(response)

        return response

    @extend_schema(
        operation_id="browser_refresh",
        summary="Rotate the HttpOnly credential cookies",
        description=(
            "Exchanges the refresh cookie for a new access and refresh cookie pair. The presented "
            "refresh token is blacklisted atomically, so replaying it fails. Any unauthorized "
            "outcome clears all three cookies. Requires a matching `X-CSRFToken` header and a "
            "trusted `Origin` whenever the refresh cookie is present."
        ),
        request=None,
        auth=[],
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description="The cookies were rotated; no body is returned.",
            ),
            HTTPStatus.UNAUTHORIZED: error_response(
                "The refresh cookie is absent, expired, malformed, revoked, replayed, or has no "
                "active account. All three cookies are cleared.",
                "Authentication failed",
                AUTHENTICATION_FAILED,
            ),
            HTTPStatus.FORBIDDEN: error_response(
                CSRF_REJECTED_DESCRIPTION,
                "CSRF rejected",
                PERMISSION_DENIED,
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The browser refresh route accepts only POST.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.TOO_MANY_REQUESTS: error_response(
                "The client address or token account exceeded the authentication scope.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account, outstanding-token, or blacklist state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Rotate the refresh cookie into a new credential pair.

        Delegates to the same atomic primary-backed rotation as the bearer route and refreshes the
        CSRF cookie lifetime so it never expires before the credential it protects.

        Arguments:
            request: REST request carrying the refresh cookie.

        Returns:
            Empty successful response carrying the replacement cookies.

        Raises:
            AuthenticationFailed: If no refresh cookie was supplied or it cannot be accepted.
            ServiceUnavailable: If authoritative account or blacklist state is unavailable.
        """
        encoded = refresh_cookie_credential(request)
        if encoded is None:
            raise AuthenticationFailed

        rotated = rotate_refresh_token(encoded)
        response = Response(status=HTTPStatus.NO_CONTENT)
        set_credential_cookies(response, access=rotated["access"], refresh=rotated["refresh"])
        get_token(cast("HttpRequest", request))

        return response


class BrowserLogoutView(BrowserSessionView):
    """End the browser session.

    Inherits from ``BrowserSessionView``, blacklists the refresh token, clears all three cookies,
    and tells the account's open browser sockets to close. Repeating it is harmless.

    Attributes:
        throttle_classes: Shared anonymous and authentication scopes.

    Members:
        signed_subject_credential: Expose the cookie credential to account throttling.
        perform_authentication: Enforce CSRF when any credential cookie is present.
        post: Revoke the session and clear the cookies.
    """

    @staticmethod
    def signed_subject_credential(request: Request) -> str | None:
        """Expose the refresh cookie to account-level throttling.

        Lets the throttle bind the budget to the signed account without reading a request body.
        The throttle validates its signature before trusting the subject.

        Arguments:
            request: Request entering throttling.

        Returns:
            The encoded refresh token, or None.
        """
        return refresh_cookie_credential(request)

    @override
    def perform_authentication(self, request: Request) -> None:
        """Enforce CSRF when the request carries any credential cookie.

        A request with no credential cookie ends nothing, so repeating logout after the cookies
        are gone stays a harmless success instead of a forgery rejection.

        Arguments:
            request: Logout request.

        Returns:
            None.

        Raises:
            PermissionDenied: If the CSRF token or Origin is invalid.
        """
        if refresh_cookie_credential(request) is not None or carries_access_cookie(request):
            enforce_csrf(request)

    @extend_schema(
        operation_id="browser_logout",
        summary="End the browser session",
        description=(
            "Blacklists the refresh token, clears all three cookies, and closes the account's "
            "open cookie-authenticated sockets. Safe to repeat: an absent, expired, or already "
            "revoked session still returns 204. Requires a matching `X-CSRFToken` header and a "
            "trusted `Origin` whenever a credential cookie is present. A previously issued access "
            "token stays valid until it expires."
        ),
        request=None,
        auth=[],
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description="The session ended and the cookies were cleared.",
            ),
            HTTPStatus.FORBIDDEN: error_response(
                CSRF_REJECTED_DESCRIPTION,
                "CSRF rejected",
                PERMISSION_DENIED,
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The browser logout route accepts only POST.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.TOO_MANY_REQUESTS: error_response(
                "The client address or token account exceeded the authentication scope.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative outstanding-token or blacklist state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Revoke the session and clear the cookies.

        Blacklists a live refresh token, broadcasts the revocation to the account's sockets
        without letting a channel outage fail the logout, and always expires the three cookies.

        Arguments:
            request: REST request carrying optional credential cookies.

        Returns:
            Empty successful response carrying the cookie deletions.

        Raises:
            ServiceUnavailable: If the authoritative database cannot record the revocation.
        """
        encoded = refresh_cookie_credential(request)
        revoked = revoke_refresh_session(encoded) if encoded is not None else None
        if revoked is not None:
            try:
                publish_session_revoked(revoked)
            except Exception:  # noqa: BLE001
                logger.warning(REVOCATION_BROADCAST_FAILED)

        response = Response(status=HTTPStatus.NO_CONTENT)
        clear_credential_cookies(response)

        return response
