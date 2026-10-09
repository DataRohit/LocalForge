"""Integration tests for cookie-authenticated REST.

Proves every authenticated route accepts the browser access cookie, enforces CSRF only on unsafe
cookie requests, rejects ambiguous credential sources, treats account state exactly as the bearer
path does, and shares throttle identity with bearer requests.
"""

# mypy: disable-error-code=misc

import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import pytest
from django.conf import settings
from django.db import DatabaseError
from django.db.models import QuerySet
from django.test import Client, override_settings
from freezegun import freeze_time
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from config.api_errors import ErrorCode
from tests.integration.accounts.test_browser_sessions import (
    BROWSER_PREFIX,
    FUTURE_MILLISECONDS,
    PASSWORD,
    _account,
    _address,
    _browser,
    _csrf_headers,
    _origin,
    _signed_in,
)

if TYPE_CHECKING:
    from rest_framework_simplejwt.tokens import Token

pytestmark = [
    pytest.mark.api_runtime,
    pytest.mark.xdist_group(name="browser-sessions"),
]
ME = "/api/v1/users/me/"


def _bearer(account: User) -> dict[str, str]:
    """Issue a bearer header for an account through the bearer route.

    Issues an access token through the real bearer route.
    The header therefore matches what production clients send.

    Arguments:
        account: Active account.

    Returns:
        The authorization header mapping.
    """
    response = Client().post(
        "/api/v1/jwt/create/",
        {"username": account.get_username(), "password": PASSWORD},
        content_type="application/json",
        REMOTE_ADDR=_address(),
    )

    return {"authorization": f"Bearer {cast('dict[str, str]', response.json())['access']}"}


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_a_safe_cookie_request_needs_no_csrf_proof(django_user_model: type[User]) -> None:
    """Read the profile with only the access cookie.

    Signs a fresh account in and reads the profile with the cookie alone.
    Safe methods change nothing, so no proof is demanded.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If a safe cookie request is refused or returns the wrong account.
    """
    client, _headers, account = _signed_in(django_user_model)

    response = client.get(ME, REMOTE_ADDR=_address())

    assert response.status_code == HTTPStatus.OK
    assert cast("dict[str, Any]", response.json())["username"] == account.get_username()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "case", ["no-token", "wrong-token", "foreign-token", "no-origin", "bad-origin"]
)
def test_an_unsafe_cookie_request_needs_a_valid_token_and_origin(
    django_user_model: type[User],
    case: str,
) -> None:
    """Reject forged unsafe requests and accept a proven one.

    Covers missing, mismatched, replayed-from-another-browser, absent-origin, and cross-origin
    requests, then confirms the same call succeeds with correct proof and changes the profile.

    Arguments:
        django_user_model: Configured custom user model class.
        case: Which part of the proof is broken.

    Returns:
        None.

    Raises:
        AssertionError: If a forged request is accepted or a genuine one refused.
    """
    client, headers, account = _signed_in(django_user_model)
    _other, other_headers = _browser()
    forged = {
        "no-token": {"Origin": _origin()},
        "wrong-token": {**headers, "X-CSRFToken": "x" * 64},
        "foreign-token": {**headers, "X-CSRFToken": other_headers["X-CSRFToken"]},
        "no-origin": {"X-CSRFToken": headers["X-CSRFToken"]},
        "bad-origin": {**headers, "Origin": "https://attacker.invalid"},
    }[case]
    email = f"changed-{uuid.uuid4().hex[:8]}@localforge.invalid"

    rejected = client.patch(
        ME,
        {"email": email},
        content_type="application/json",
        REMOTE_ADDR=_address(),
        headers=forged,
    )
    accepted = client.patch(
        ME,
        {"email": email},
        content_type="application/json",
        REMOTE_ADDR=_address(),
        headers=headers,
    )

    assert rejected.status_code == HTTPStatus.FORBIDDEN
    assert cast("dict[str, Any]", rejected.json())["code"] == ErrorCode.PERMISSION_DENIED
    assert accepted.status_code == HTTPStatus.OK
    account.refresh_from_db()
    assert account.email == email


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_bearer_requests_are_exempt_from_csrf(django_user_model: type[User]) -> None:
    """Leave the bearer contract untouched.

    Sends an unsafe bearer request through a CSRF-enforcing client with neither token nor origin
    and asserts it succeeds and sets no cookie.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If bearer requests are subjected to CSRF or receive cookies.
    """
    account = _account(django_user_model)
    email = f"bearer-{uuid.uuid4().hex[:8]}@localforge.invalid"

    response = Client(enforce_csrf_checks=True).patch(
        ME,
        {"email": email},
        content_type="application/json",
        headers=_bearer(account),
        REMOTE_ADDR=_address(),
    )

    assert response.status_code == HTTPStatus.OK
    assert not response.cookies


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("scheme", ["jwt", "token"])
def test_two_credential_sources_are_rejected_never_merged(
    django_user_model: type[User],
    scheme: str,
) -> None:
    """Refuse a request carrying an authorization header and the access cookie.

    Pairs the access cookie with each bearer scheme on safe and unsafe methods.
    Neither credential may be silently preferred.

    Arguments:
        django_user_model: Configured custom user model class.
        scheme: Which authorization scheme accompanies the cookie.

    Returns:
        None.

    Raises:
        AssertionError: If either credential is silently preferred.
    """
    client, headers, account = _signed_in(django_user_model)
    authorization = _bearer(account) if scheme == "jwt" else {"authorization": "Token " + "0" * 40}

    response = client.get(ME, headers=authorization, REMOTE_ADDR=_address())
    unsafe = client.patch(
        ME,
        {"email": "ignored@localforge.invalid"},
        content_type="application/json",
        headers={**authorization, **headers},
        REMOTE_ADDR=_address(),
    )

    assert response.status_code == unsafe.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.BAD_REQUEST


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("case", ["malformed", "refresh-in-access", "expired", "revoked"])
def test_an_unusable_access_cookie_is_unauthorized(
    django_user_model: type[User],
    case: str,
) -> None:
    """Reject malformed, wrong-type, expired, and password-revoked cookies.

    Corrupts, retypes, expires, or revokes the cookie before a profile read.
    Each defect must yield the same enveloped authentication failure.

    Arguments:
        django_user_model: Configured custom user model class.
        case: Which defect the cookie carries.

    Returns:
        None.

    Raises:
        AssertionError: If an unusable cookie authenticates.
    """
    client, _headers, account = _signed_in(django_user_model)
    name = settings.BROWSER_ACCESS_COOKIE_NAME
    if case == "malformed":
        client.cookies[name] = "not-a-token"
    elif case == "refresh-in-access":
        client.cookies[name] = client.cookies[settings.BROWSER_REFRESH_COOKIE_NAME].value
    elif case == "revoked":
        account.set_password("replaced-" + PASSWORD)
        account.save()

    moment = (
        freeze_time(
            datetime.now(UTC) + timedelta(seconds=settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS + 5)
        )
        if case == "expired"
        else freeze_time()
    )
    with moment:
        response = client.get(ME, REMOTE_ADDR=_address())

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.AUTHENTICATION_FAILED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("state", ["inactive", "deleted"])
def test_inactive_and_deleted_accounts_are_rejected_on_the_cookie_path(
    django_user_model: type[User],
    state: str,
) -> None:
    """Treat account state on the cookie path exactly as on the bearer path.

    Disables or removes the account after sign-in and reads the profile.
    The cookie response must equal the bearer response for the same account.

    Arguments:
        django_user_model: Configured custom user model class.
        state: Account state applied after sign-in.

    Returns:
        None.

    Raises:
        AssertionError: If a disabled or removed account keeps access.
    """
    client, _headers, account = _signed_in(django_user_model)
    bearer = _bearer(account)
    if state == "inactive":
        django_user_model.objects.filter(pk=account.pk).update(is_active=False)
    else:
        django_user_model.objects.filter(pk=account.pk).delete()

    by_cookie = client.get(ME, REMOTE_ADDR=_address())
    by_bearer = Client().get(ME, headers=bearer, REMOTE_ADDR=_address())

    assert by_cookie.status_code == by_bearer.status_code == HTTPStatus.UNAUTHORIZED
    assert by_cookie.json() | {"request_id": ""} == by_bearer.json() | {"request_id": ""}


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_a_primary_outage_on_the_cookie_path_is_service_unavailable(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report authoritative-store loss as 503 rather than rejecting the credential.

    Fails only the primary account lookup used by authentication.
    The rest of the request stays production-shaped.

    Arguments:
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing the outage.

    Returns:
        None.

    Raises:
        AssertionError: If the outage is reported as an authentication failure.
    """
    client, _headers, _account_row = _signed_in(django_user_model)
    original = QuerySet.get

    def fail_account_lookup(queryset: QuerySet[Any], *args: object, **kwargs: object) -> object:
        """Fail the primary account lookup used by authentication.

        Raises a database error for the account model only.
        Every other model is left untouched so authentication reaches the lookup.

        Arguments:
            queryset: Queryset performing the lookup.
            *args: Positional lookup arguments.
            **kwargs: Keyword lookup arguments.

        Returns:
            The object for unrelated models.

        Raises:
            DatabaseError: For the account model.
        """
        if queryset.model is User:
            raise DatabaseError
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "get", fail_account_lookup)
    client.raise_request_exception = False
    response = client.get(ME, REMOTE_ADDR=_address())

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_cookie_and_bearer_requests_share_one_throttle_identity(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Charge one account budget whichever credential carries the request.

    Spends the authenticated-read budget with a bearer request and asserts the cookie request of
    the same account from the same address is throttled.

    Arguments:
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture freezing the throttle clock.

    Returns:
        None.

    Raises:
        AssertionError: If the two credential sources keep separate budgets.
    """
    client, _headers, account = _signed_in(django_user_model)
    bearer = _bearer(account)
    address = _address()
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS", FUTURE_MILLISECONDS
    )

    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE="1000/minute",
        API_AUTHENTICATED_READ_THROTTLE_RATE="1/minute",
    ):
        first = Client().get(ME, headers=bearer, REMOTE_ADDR=address)
        second = client.get(ME, REMOTE_ADDR=address)

    assert first.status_code == HTTPStatus.OK
    assert second.status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_api_responses_forbid_framing(django_user_model: type[User]) -> None:
    """Carry both anti-clickjacking controls on API responses.

    Reads one authenticated API response.
    Checks the frame-denial header and the content security policy directive.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If either control is absent.
    """
    client, _headers, _account_row = _signed_in(django_user_model)

    response = client.get(ME, REMOTE_ADDR=_address())

    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_the_refresh_token_never_travels_outside_the_browser_route_group(
    django_user_model: type[User],
) -> None:
    """Scope the refresh cookie to the browser route group.

    Confirms the stored refresh cookie is restricted to the browser path, so the browser withholds
    it from every other request.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the refresh cookie is scoped wider than the browser routes.
    """
    client, _headers, _account_row = _signed_in(django_user_model)

    refresh = client.cookies[settings.BROWSER_REFRESH_COOKIE_NAME]

    assert refresh["path"] == BROWSER_PREFIX
    assert RefreshToken(cast("Token", refresh.value))["token_type"] == "refresh"  # noqa: S105
    assert client.cookies[settings.BROWSER_CSRF_COOKIE_NAME]["path"] == "/"
    assert _csrf_headers(client)["X-CSRFToken"]
