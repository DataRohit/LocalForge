"""Unit tests for browser credential cookies and request forgery defence.

Exercises cookie naming, the fixed attribute set, ambiguous-credential rejection, and the CSRF
check against synthetic requests, without reading account or token state.
"""

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request

from accounts.browser_cookies import (
    REFRESH_PATH,
    access_cookie_name,
    carries_access_cookie,
    clear_credential_cookies,
    csrf_cookie_name,
    enforce_csrf,
    refresh_cookie_name,
    reject_ambiguous_credentials,
    set_credential_cookies,
)
from config.api_errors import AmbiguousCredentials

SECRET = "a" * 32
TRUSTED_ORIGIN = "http://testserver"


def _request(method: str = "post", **extra: str) -> Request:
    """Build a REST request carrying the supplied WSGI entries.

    Wraps a synthetic Django request so each test controls only the headers it cares about.
    Uses the request factory's lowercase method helpers.

    Arguments:
        method: Lowercase HTTP method.
        **extra: WSGI environment entries.

    Returns:
        A REST request wrapping a synthetic Django request.
    """
    return Request(getattr(RequestFactory(), method)("/api/v1/users/me/", **extra))


@pytest.mark.unit
def test_cookie_names_come_from_the_validated_settings() -> None:
    """Read the three names from configuration rather than literals.

    Compares each helper with the setting it must expose, so no caller can hardcode a name.
    Covers all three cookies.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a helper returns anything but the configured value.
    """
    assert access_cookie_name() == settings.BROWSER_ACCESS_COOKIE_NAME
    assert refresh_cookie_name() == settings.BROWSER_REFRESH_COOKIE_NAME
    assert csrf_cookie_name() == settings.BROWSER_CSRF_COOKIE_NAME


@pytest.mark.unit
def test_credential_cookies_carry_the_fixed_attribute_set() -> None:
    """Write both credential cookies with every protective attribute.

    Checks the secure, script-hidden, strict, host-bound flags together with the path and lifetime
    each cookie must carry.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any flag, path, or lifetime deviates from the contract.
    """
    response = HttpResponse()

    set_credential_cookies(response, access="a.b.c", refresh="d.e.f")

    access = response.cookies[settings.BROWSER_ACCESS_COOKIE_NAME]
    refresh = response.cookies[settings.BROWSER_REFRESH_COOKIE_NAME]
    for cookie in (access, refresh):
        assert cookie["httponly"] is True
        assert cookie["secure"] is True
        assert cookie["samesite"] == "Strict"
        assert cookie["domain"] == ""
    assert (access["path"], refresh["path"]) == ("/", REFRESH_PATH)
    assert access["max-age"] == settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS
    assert refresh["max-age"] == settings.JWT_REFRESH_TOKEN_LIFETIME_SECONDS
    assert (access.value, refresh.value) == ("a.b.c", "d.e.f")


@pytest.mark.unit
def test_clearing_expires_all_three_cookies_on_their_original_paths() -> None:
    """Expire every cookie where it was set.

    A browser ignores a deletion whose path differs from the original, so each expiry must repeat
    the path used when the cookie was written.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a deletion targets the wrong path or omits a cookie.
    """
    response = HttpResponse()

    clear_credential_cookies(response)

    assert {name: response.cookies[name]["path"] for name in response.cookies} == {
        settings.BROWSER_ACCESS_COOKIE_NAME: "/",
        settings.BROWSER_REFRESH_COOKIE_NAME: REFRESH_PATH,
        settings.BROWSER_CSRF_COOKIE_NAME: "/",
    }
    assert all(response.cookies[name]["max-age"] == 0 for name in response.cookies)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("authorization", "cookie", "ambiguous"),
    [
        ("Bearer token", "value", True),
        ("Bearer token", "", False),
        ("", "value", False),
        ("", "", False),
    ],
)
def test_ambiguity_requires_both_credential_sources(
    authorization: str,
    cookie: str,
    ambiguous: bool,  # noqa: FBT001
) -> None:
    """Reject only a request that presents both sources.

    Varies the presence of the authorization header and the access cookie independently.
    Only the combination of both may be refused.

    Arguments:
        authorization: Authorization header value.
        cookie: Access cookie value.
        ambiguous: Whether rejection is expected.

    Returns:
        None.

    Raises:
        AssertionError: If rejection does not match the presence of both sources.
    """
    extra = {"HTTP_AUTHORIZATION": authorization} if authorization else {}
    request = _request("get", **extra)
    request.COOKIES[settings.BROWSER_ACCESS_COOKIE_NAME] = cookie

    assert carries_access_cookie(request) is bool(cookie)
    if ambiguous:
        with pytest.raises(AmbiguousCredentials):
            reject_ambiguous_credentials(request)
    else:
        reject_ambiguous_credentials(request)


@pytest.mark.unit
def test_csrf_is_not_enforced_on_safe_methods_or_when_disabled_by_the_harness() -> None:
    """Skip the check where it has nothing to protect.

    A safe method changes nothing, and the Django test client marks its requests exempt, so both
    must pass without proof.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a safe or harness-exempt request is rejected.
    """
    enforce_csrf(_request("get"))

    django_request = RequestFactory().post("/api/v1/users/me/")
    vars(django_request)["_dont_enforce_csrf_checks"] = True
    enforce_csrf(Request(django_request))


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"HTTP_ORIGIN": TRUSTED_ORIGIN},
        {"HTTP_X_CSRFTOKEN": SECRET},
        {"HTTP_ORIGIN": "https://attacker.invalid", "HTTP_X_CSRFTOKEN": SECRET},
        {"HTTP_ORIGIN": TRUSTED_ORIGIN, "HTTP_X_CSRFTOKEN": "b" * 32},
    ],
    ids=["nothing", "no-token", "no-origin", "foreign-origin", "mismatched-token"],
)
def test_an_unsafe_request_without_full_proof_is_denied(extra: dict[str, str]) -> None:
    """Deny an unsafe request missing or mismatching either half of the proof.

    Supplies a cookie secret and varies the header token and origin around it.
    Every incomplete combination must be refused.

    Arguments:
        extra: WSGI entries supplied with the request.

    Returns:
        None.

    Raises:
        AssertionError: If the request is accepted.
    """
    request = _request("post", **extra)
    request.COOKIES[settings.BROWSER_CSRF_COOKIE_NAME] = SECRET

    with pytest.raises(PermissionDenied):
        enforce_csrf(request)


@pytest.mark.unit
def test_an_unsafe_request_with_matching_token_and_trusted_origin_is_accepted() -> None:
    """Accept a request proving both the token and the origin.

    Supplies a header token equal to the cookie secret and an origin equal to the request host.
    Nothing may be raised.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a genuine request is rejected.
    """
    request = _request("post", HTTP_ORIGIN=TRUSTED_ORIGIN, HTTP_X_CSRFTOKEN=SECRET)
    request.COOKIES[settings.BROWSER_CSRF_COOKIE_NAME] = SECRET

    enforce_csrf(request)
