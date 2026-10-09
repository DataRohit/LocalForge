"""Unit tests for cookie-based REST authentication.

Exercises the absent, malformed, and outage paths of the access-cookie authenticator and the
published security scheme without reading real account state.
"""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import DatabaseError
from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from accounts.browser_authentication import CookieJWTAuthentication, CookieJWTAuthenticationScheme
from config.api_errors import ServiceUnavailable


def _request(cookie: str | None) -> Request:
    """Build a safe REST request carrying an optional access cookie.

    Wraps a synthetic Django GET so the authenticator sees only the cookie under test.
    Leaves the header untouched.

    Arguments:
        cookie: Access cookie value, or None for none.

    Returns:
        A REST request wrapping a synthetic Django request.
    """
    request = Request(RequestFactory().get("/api/v1/users/me/"))
    if cookie is not None:
        request.COOKIES[settings.BROWSER_ACCESS_COOKIE_NAME] = cookie

    return request


@pytest.mark.unit
@pytest.mark.parametrize("cookie", [None, ""])
def test_a_missing_or_empty_cookie_leaves_the_request_anonymous(cookie: str | None) -> None:
    """Return nothing so anonymous routes stay anonymous.

    Covers an absent cookie and an empty one, which a cleared cookie leaves behind.
    Neither may authenticate.

    Arguments:
        cookie: Absent or empty cookie value.

    Returns:
        None.

    Raises:
        AssertionError: If an authentication result is produced.
    """
    assert CookieJWTAuthentication().authenticate(_request(cookie)) is None


@pytest.mark.unit
def test_a_malformed_cookie_is_the_generic_authentication_failure() -> None:
    """Convert package token errors into the project exception.

    The central handler maps exceptions by exact type, so a subclass would degrade to a generic
    error code.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a different exception type escapes.
    """
    with pytest.raises(AuthenticationFailed) as failure:
        CookieJWTAuthentication().authenticate(_request("not-a-token"))

    assert type(failure.value) is AuthenticationFailed


@pytest.mark.unit
def test_a_primary_outage_is_service_unavailable() -> None:
    """Report authoritative-store loss distinctly from a bad credential.

    Forces the token validation step to raise a database error and expects the shared
    service-unavailable exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the outage is reported as an authentication failure.
    """
    authentication = CookieJWTAuthentication()

    with (
        patch.object(authentication, "get_validated_token", side_effect=DatabaseError),
        pytest.raises(ServiceUnavailable),
    ):
        authentication.authenticate(_request("token"))


@pytest.mark.unit
def test_the_security_scheme_documents_the_cookie_and_its_obligations() -> None:
    """Publish an API-key scheme naming the configured cookie.

    Reads the definition directly and checks the transport, the cookie name, and the CSRF
    obligation it must describe.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the definition drifts from the runtime contract.
    """
    scheme = CookieJWTAuthenticationScheme(CookieJWTAuthentication)  # type: ignore[no-untyped-call]
    definition = scheme.get_security_definition(object())

    assert definition["type"] == "apiKey"
    assert definition["in"] == "cookie"
    assert definition["name"] == settings.BROWSER_ACCESS_COOKIE_NAME
    assert "X-CSRFToken" in definition["description"]
    assert CookieJWTAuthenticationScheme.name == "browserCookieAuth"
