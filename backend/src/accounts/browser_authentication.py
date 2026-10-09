"""Cookie-based REST authentication.

Authenticates the browser access cookie against authoritative primary account state through the
same validation as bearer tokens, and enforces CSRF on every unsafe request it admits.
"""

# mypy: disable-error-code=misc

from __future__ import annotations

from typing import TYPE_CHECKING, cast, override

from django.conf import settings
from django.db import DatabaseError
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import (
    AuthenticationFailed as SimpleJWTAuthenticationFailed,
)

from accounts.authentication import JWTAuthentication
from accounts.browser_cookies import access_cookie_name, enforce_csrf
from config.api_errors import ServiceUnavailable

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework_simplejwt.tokens import Token as JWTToken

    from accounts.models import User


class CookieJWTAuthentication(JWTAuthentication):
    """Authenticate the browser access cookie.

    Inherits from the project's ``JWTAuthentication`` so token validation, primary account lookup,
    and revocation checks are identical to the bearer path, and reads the credential from the
    access cookie instead of the authorization header.

    Attributes:
        None beyond those inherited from ``JWTAuthentication``.

    Members:
        authenticate: Validate the access cookie and enforce CSRF on unsafe methods.
    """

    @override
    def authenticate(  # type: ignore[override]
        self,
        request: Request,
    ) -> tuple[User, JWTToken] | None:
        """Authenticate the access cookie without exposing rejection details.

        Returns nothing when the cookie is absent so anonymous routes stay anonymous, converts
        every token and account failure into the generic error, and applies the CSRF check only
        after the credential proves valid.

        Arguments:
            request: REST request carrying an optional access cookie.

        Returns:
            Active account and validated access token, or None when no cookie was supplied.

        Raises:
            AuthenticationFailed: If the cookie or its account is invalid.
            PermissionDenied: If an unsafe request fails the CSRF check.
            ServiceUnavailable: If authoritative account state cannot be read.
        """
        raw = request.COOKIES.get(access_cookie_name())
        if not raw:
            return None

        try:
            validated = self.get_validated_token(raw.encode())
            user = self.get_user(validated)
        except SimpleJWTAuthenticationFailed as error:
            raise AuthenticationFailed from error
        except DatabaseError as error:
            raise ServiceUnavailable from error

        enforce_csrf(request)

        return user, validated


class CookieJWTAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    """Declare the browser cookie security scheme.

    Inherits from drf-spectacular's ``OpenApiAuthenticationExtension`` and maps the cookie
    authenticator to an API key carried in the access cookie.

    Attributes:
        target_class: Cookie authenticator represented by this extension.
        name: Stable OpenAPI security scheme name.

    Members:
        get_security_definition: Build the cookie scheme document.
    """

    target_class = "accounts.browser_authentication.CookieJWTAuthentication"
    name = "browserCookieAuth"

    @override
    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        """Build the OpenAPI definition for the browser access cookie.

        Documents the cookie name and the CSRF obligation of unsafe requests so generated clients
        and reviewers see the same contract the runtime enforces.

        Arguments:
            auto_schema: Schema generator requesting the security definition.

        Returns:
            OpenAPI API-key scheme carried in a cookie.
        """
        del auto_schema

        return {
            "type": "apiKey",
            "in": "cookie",
            "name": cast("str", settings.BROWSER_ACCESS_COOKIE_NAME),
            "description": (
                "HttpOnly access cookie issued by the browser login route. Unsafe requests must "
                "also send a matching `X-CSRFToken` header and a trusted `Origin`. A request "
                "carrying both this cookie and an `Authorization` header is rejected."
            ),
        }
