"""Integration tests for the browser session endpoints.

Exercises the CSRF, login, refresh, and logout routes against PostgreSQL and Valkey through the
real HTTP stack, proving cookie attributes, CSRF enforcement, rotation, revocation, credential
secrecy, and that every documented status is observed.
"""

# mypy: disable-error-code=misc

import logging
import secrets
import uuid
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import Client, override_settings
from prometheus_client import generate_latest
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

import accounts.browser_sessions as browser_sessions_module
from config.api_errors import ErrorCode, ServiceUnavailable
from config.logs import StructuredFormatter

if TYPE_CHECKING:
    from collections.abc import Mapping
    from http.cookies import Morsel

    from rest_framework_simplejwt.tokens import Token

    from accounts.models import User

PASSWORD = secrets.token_urlsafe(24)
BROWSER_PREFIX = "/api/v1/browser/"
FUTURE_MILLISECONDS = 1_800_000_050_000
pytestmark = [
    pytest.mark.api_runtime,
    pytest.mark.xdist_group(name="browser-sessions"),
]


def _address() -> str:
    """Return a unique client address.

    Gives each request its own throttle dimension so parallel tests cannot spend one another's
    budgets.

    Arguments:
        None.

    Returns:
        A documentation-range IPv6 address.
    """
    return f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"


def _origin() -> str:
    """Return a trusted browser origin.

    Selects the first configured CSRF-trusted origin.
    A request carrying it passes the trusted-origin comparison.

    Arguments:
        None.

    Returns:
        The first configured CSRF-trusted origin.
    """
    return cast("list[str]", settings.CSRF_TRUSTED_ORIGINS)[0]


def _browser() -> tuple[Client, dict[str, str]]:
    """Create a CSRF-enforcing browser with a seeded CSRF cookie.

    Fetches the CSRF route like a single-page application would, then returns the client together
    with the headers a same-origin unsafe request must carry.

    Arguments:
        None.

    Returns:
        The client and its CSRF and Origin headers.
    """
    client = Client(enforce_csrf_checks=True)
    response = client.get(f"{BROWSER_PREFIX}csrf/", REMOTE_ADDR=_address())
    assert response.status_code == HTTPStatus.NO_CONTENT

    return client, _csrf_headers(client)


def _csrf_headers(client: Client) -> dict[str, str]:
    """Build the headers an unsafe browser request carries.

    Reads the CSRF cookie the client currently holds.
    Reading it each time means a rotated token is never stale.

    Arguments:
        client: Client holding the CSRF cookie.

    Returns:
        The token and Origin headers in WSGI form.
    """
    token = client.cookies[settings.BROWSER_CSRF_COOKIE_NAME].value

    return {"X-CSRFToken": token, "Origin": _origin()}


def _account(django_user_model: type[User], *, active: bool = True) -> User:
    """Create an account with a unique username.

    Creates a user with a unique name and the shared test password.
    Unique names keep parallel tests from colliding.

    Arguments:
        django_user_model: Configured custom user model class.
        active: Whether the account may sign in.

    Returns:
        The persisted account.
    """
    username = f"browser-{uuid.uuid4().hex[:16]}"

    return django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=active,
    )


def _login(
    client: Client,
    headers: Mapping[str, str],
    username: str,
    password: str = PASSWORD,
    address: str | None = None,
) -> Any:  # noqa: ANN401
    """Post a browser login.

    Posts JSON credentials to the real browser login route.
    Uses a unique client address unless the caller supplies one.

    Arguments:
        client: Browser client.
        headers: CSRF and Origin headers.
        username: Submitted username.
        password: Submitted password.
        address: Client address, or None for a unique one.

    Returns:
        The test response.
    """
    return client.post(
        f"{BROWSER_PREFIX}login/",
        {"username": username, "password": password},
        content_type="application/json",
        REMOTE_ADDR=address or _address(),
        headers=headers,
    )


def _signed_in(django_user_model: type[User]) -> tuple[Client, dict[str, str], User]:
    """Sign a fresh account in through the browser route.

    Seeds the CSRF cookie and then signs a fresh account in.
    The sequence is the one the single-page application follows.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        The browser client, refreshed CSRF headers, and the account.
    """
    account = _account(django_user_model)
    client, headers = _browser()
    response = _login(client, headers, account.get_username())
    assert response.status_code == HTTPStatus.OK

    return client, _csrf_headers(client), account


def _morsel(client: Client, name: str) -> Morsel[str]:
    """Return one cookie morsel held by a client.

    Looks one cookie up in the client jar.
    Exposes attributes such as path and lifetime for assertion.

    Arguments:
        client: Browser client.
        name: Cookie name.

    Returns:
        The stored morsel.
    """
    return client.cookies[name]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_csrf_route_sets_only_the_readable_cookie_and_forbids_caching() -> None:
    """Seed the CSRF cookie with its fixed attributes.

    Confirms the route returns no body, forbids caching, and sets exactly one cookie that script
    can read, is secure, strict, root-scoped, and lives as long as the refresh credential.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the response or cookie deviates from the contract.
    """
    client = Client()
    response = client.get(f"{BROWSER_PREFIX}csrf/", REMOTE_ADDR=_address())

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert response.content == b""
    assert "no-store" in response.headers["Cache-Control"]
    assert set(response.cookies) == {settings.BROWSER_CSRF_COOKIE_NAME}
    csrf = response.cookies[settings.BROWSER_CSRF_COOKIE_NAME]
    assert csrf["httponly"] == ""
    assert csrf["secure"] is True
    assert csrf["samesite"] == "Strict"
    assert csrf["path"] == "/"
    assert csrf["domain"] == ""
    assert csrf["max-age"] == settings.JWT_REFRESH_TOKEN_LIFETIME_SECONDS


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_login_sets_httponly_cookies_and_returns_only_the_profile(
    django_user_model: type[User],
) -> None:
    """Sign in without any token reaching the body.

    Confirms the access and refresh cookies carry the documented attributes and lifetimes, the CSRF
    cookie is rotated, and the body is the profile with neither token nor password in it.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If an attribute, lifetime, or body field deviates from the contract.
    """
    account = _account(django_user_model)
    client, headers = _browser()
    before = client.cookies[settings.BROWSER_CSRF_COOKIE_NAME].value

    response = _login(client, headers, account.get_username())

    assert response.status_code == HTTPStatus.OK
    body = cast("dict[str, Any]", response.json())
    assert body["username"] == account.get_username()
    assert not {"access", "refresh", "token", "password"} & set(body)
    access = response.cookies[settings.BROWSER_ACCESS_COOKIE_NAME]
    refresh = response.cookies[settings.BROWSER_REFRESH_COOKIE_NAME]
    assert (access["httponly"], access["secure"], access["samesite"]) == (True, True, "Strict")
    assert (refresh["httponly"], refresh["secure"], refresh["samesite"]) == (True, True, "Strict")
    assert access["path"] == "/"
    assert refresh["path"] == BROWSER_PREFIX
    assert access["max-age"] == settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS
    assert refresh["max-age"] == settings.JWT_REFRESH_TOKEN_LIFETIME_SECONDS
    assert access["domain"] == refresh["domain"] == ""
    assert access.value not in response.content.decode()
    assert refresh.value not in response.content.decode()
    assert response.cookies[settings.BROWSER_CSRF_COOKIE_NAME].value != before


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "case", ["no-token", "wrong-token", "foreign-token", "no-origin", "bad-origin"]
)
def test_login_requires_a_matching_token_and_a_trusted_origin(
    django_user_model: type[User],
    case: str,
) -> None:
    """Reject login without a valid CSRF token and trusted origin.

    Covers a missing token, a mismatched token, a token minted for another browser, a missing
    Origin, and an untrusted Origin, asserting each yields the enveloped 403 and no cookies.

    Arguments:
        django_user_model: Configured custom user model class.
        case: Which part of the check is broken.

    Returns:
        None.

    Raises:
        AssertionError: If a forged login is accepted or leaks a cookie.
    """
    account = _account(django_user_model)
    client, headers = _browser()
    other, other_headers = _browser()
    forged = {
        "no-token": {"Origin": _origin()},
        "wrong-token": {**headers, "X-CSRFToken": "x" * 64},
        "foreign-token": {**headers, "X-CSRFToken": other_headers["X-CSRFToken"]},
        "no-origin": {"X-CSRFToken": headers["X-CSRFToken"]},
        "bad-origin": {**headers, "Origin": "https://attacker.invalid"},
    }[case]

    response = _login(client, forged, account.get_username())

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.PERMISSION_DENIED
    assert settings.BROWSER_ACCESS_COOKIE_NAME not in response.cookies
    assert other.cookies


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_a_forged_form_post_is_forbidden_not_unsupported() -> None:
    """Answer a cross-site form post with the CSRF rejection.

    Submits a multipart form carrying a trusted origin but no token header, the shape of a forged
    browser form, and asserts the CSRF check rejects it before any parser can answer 415.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the response is anything but the enveloped 403.
    """
    client = Client(enforce_csrf_checks=True)

    response = client.post(
        f"{BROWSER_PREFIX}login/",
        {"username": "river", "password": PASSWORD},
        headers={"Origin": _origin()},
        REMOTE_ADDR=_address(),
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.PERMISSION_DENIED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_login_rejections_are_indistinguishable_and_set_no_cookie(
    django_user_model: type[User],
) -> None:
    """Resist account enumeration on the browser route.

    Compares an unknown username, a wrong password, and an inactive account, asserting identical
    status and body shape and that no credential cookie is ever issued.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the rejections differ or issue a cookie.
    """
    active = _account(django_user_model)
    inactive = _account(django_user_model, active=False)
    client, headers = _browser()

    responses = [
        _login(client, headers, f"missing-{uuid.uuid4().hex[:12]}"),
        _login(client, headers, active.get_username(), PASSWORD + "-wrong"),
        _login(client, headers, inactive.get_username()),
    ]

    assert {response.status_code for response in responses} == {HTTPStatus.UNAUTHORIZED}
    bodies = {
        (body["code"], body["message"])
        for body in (cast("dict[str, Any]", response.json()) for response in responses)
    }
    assert bodies == {(ErrorCode.AUTHENTICATION_FAILED, "Authentication failed.")}
    assert all(
        settings.BROWSER_ACCESS_COOKIE_NAME not in response.cookies for response in responses
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_refresh_rotates_the_pair_and_a_replayed_refresh_clears_every_cookie(
    django_user_model: type[User],
) -> None:
    """Rotate on refresh and end the session on replay.

    Confirms refresh returns 204 with a new pair, that presenting the rotated-out refresh token
    again fails with all three cookies expired, and that the successor remains usable until then.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If rotation or replay detection deviates from the contract.
    """
    client, headers, _account_row = _signed_in(django_user_model)
    original = _morsel(client, settings.BROWSER_REFRESH_COOKIE_NAME).value

    rotated = client.post(f"{BROWSER_PREFIX}refresh/", REMOTE_ADDR=_address(), headers=headers)

    assert rotated.status_code == HTTPStatus.NO_CONTENT
    assert rotated.content == b""
    assert {settings.BROWSER_ACCESS_COOKIE_NAME, settings.BROWSER_REFRESH_COOKIE_NAME} <= set(
        rotated.cookies
    )
    assert _morsel(client, settings.BROWSER_REFRESH_COOKIE_NAME).value != original

    replay = Client(enforce_csrf_checks=True)
    replay.cookies[settings.BROWSER_CSRF_COOKIE_NAME] = client.cookies[
        settings.BROWSER_CSRF_COOKIE_NAME
    ].value
    replay.cookies[settings.BROWSER_REFRESH_COOKIE_NAME] = original
    failed = replay.post(f"{BROWSER_PREFIX}refresh/", REMOTE_ADDR=_address(), headers=headers)

    assert failed.status_code == HTTPStatus.UNAUTHORIZED
    for name in (
        settings.BROWSER_ACCESS_COOKIE_NAME,
        settings.BROWSER_REFRESH_COOKIE_NAME,
        settings.BROWSER_CSRF_COOKIE_NAME,
    ):
        assert failed.cookies[name]["max-age"] == 0
        assert failed.cookies[name].value == ""


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_refresh_without_a_cookie_is_unauthorized_and_clears_stale_cookies() -> None:
    """Treat a missing refresh cookie as a dead session.

    Posts to the refresh route with no credential cookie.
    No cookie means nothing to forge, so the CSRF check is skipped.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the response is not the enveloped 401 with expiring cookies.
    """
    client = Client(enforce_csrf_checks=True)

    response = client.post(f"{BROWSER_PREFIX}refresh/", REMOTE_ADDR=_address())

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.AUTHENTICATION_FAILED
    assert response.cookies[settings.BROWSER_REFRESH_COOKIE_NAME]["max-age"] == 0


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_refresh_csrf_failure_leaves_the_cookies_in_place(
    django_user_model: type[User],
) -> None:
    """Keep the session when only the CSRF proof is wrong.

    Posts a refresh with an origin but no token.
    A forged or stale request must not sign the user out.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If a CSRF failure clears or rotates credentials.
    """
    client, _headers, _account_row = _signed_in(django_user_model)
    before = _morsel(client, settings.BROWSER_REFRESH_COOKIE_NAME).value

    response = client.post(
        f"{BROWSER_PREFIX}refresh/",
        REMOTE_ADDR=_address(),
        headers={"Origin": _origin()},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert not response.cookies
    assert _morsel(client, settings.BROWSER_REFRESH_COOKIE_NAME).value == before


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_logout_blacklists_the_refresh_token_and_is_safe_to_repeat(
    django_user_model: type[User],
) -> None:
    """End the session and tolerate a second logout.

    Confirms the refresh token lands in the blacklist, all cookies expire, a later refresh with
    the old token fails, and logging out again with no cookies still succeeds without a CSRF token.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If revocation or idempotence deviates from the contract.
    """
    client, headers, _account_row = _signed_in(django_user_model)
    refresh = _morsel(client, settings.BROWSER_REFRESH_COOKIE_NAME).value
    jti = RefreshToken(cast("Token", refresh))["jti"]

    response = client.post(f"{BROWSER_PREFIX}logout/", REMOTE_ADDR=_address(), headers=headers)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert BlacklistedToken.objects.using("default").filter(token__jti=jti).exists()
    for name in (
        settings.BROWSER_ACCESS_COOKIE_NAME,
        settings.BROWSER_REFRESH_COOKIE_NAME,
        settings.BROWSER_CSRF_COOKIE_NAME,
    ):
        assert response.cookies[name]["max-age"] == 0

    repeat = Client(enforce_csrf_checks=True).post(
        f"{BROWSER_PREFIX}logout/",
        REMOTE_ADDR=_address(),
    )
    assert repeat.status_code == HTTPStatus.NO_CONTENT

    stale = Client(enforce_csrf_checks=True)
    stale.cookies[settings.BROWSER_CSRF_COOKIE_NAME] = headers["X-CSRFToken"]
    stale.cookies[settings.BROWSER_REFRESH_COOKIE_NAME] = refresh
    assert (
        stale.post(f"{BROWSER_PREFIX}logout/", REMOTE_ADDR=_address(), headers=headers).status_code
        == HTTPStatus.NO_CONTENT
    )
    assert (
        stale.post(f"{BROWSER_PREFIX}refresh/", REMOTE_ADDR=_address(), headers=headers).status_code
        == HTTPStatus.UNAUTHORIZED
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_logout_with_a_credential_cookie_requires_csrf(django_user_model: type[User]) -> None:
    """Refuse a forged logout that would sign a victim out.

    Posts a logout carrying credential cookies but no CSRF proof.
    A third-party page must not be able to end a victim session.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If a cookie-carrying logout skips the CSRF check.
    """
    client, _headers, _account_row = _signed_in(django_user_model)

    response = client.post(f"{BROWSER_PREFIX}logout/", REMOTE_ADDR=_address())

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert not BlacklistedToken.objects.using("default").exists()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_logout_survives_a_failed_revocation_broadcast(
    django_user_model: type[User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Complete logout when the channel layer is unreachable.

    Fails the channel publication during logout.
    The broadcast is best effort, so only one warning is expected.

    Arguments:
        django_user_model: Configured custom user model class.
        caplog: Log capture fixture.

    Returns:
        None.

    Raises:
        AssertionError: If the outage fails the logout or is not recorded once.
    """
    client, headers, _account_row = _signed_in(django_user_model)
    caplog.set_level(logging.WARNING)

    with patch.object(
        browser_sessions_module,
        "publish_session_revoked",
        side_effect=OSError("channel layer down"),
    ):
        response = client.post(f"{BROWSER_PREFIX}logout/", REMOTE_ADDR=_address(), headers=headers)

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert [record.getMessage() for record in caplog.records].count(
        browser_sessions_module.REVOCATION_BROADCAST_FAILED
    ) == 1


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_no_credential_reaches_a_url_or_a_log_record(
    django_user_model: type[User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep every issued credential out of URLs and logs.

    Runs the full session lifecycle at debug verbosity and asserts neither token nor the password
    appears in any rendered log record, response header other than the cookie, or redirect.

    Arguments:
        django_user_model: Configured custom user model class.
        caplog: Log capture fixture.

    Returns:
        None.

    Raises:
        AssertionError: If a secret appears where it must not.
    """
    caplog.set_level(logging.DEBUG)
    account = _account(django_user_model)
    client, headers = _browser()
    login = _login(client, headers, account.get_username())
    secrets_seen = [
        login.cookies[settings.BROWSER_ACCESS_COOKIE_NAME].value,
        login.cookies[settings.BROWSER_REFRESH_COOKIE_NAME].value,
        PASSWORD,
    ]
    headers = _csrf_headers(client)
    responses = [
        login,
        client.post(f"{BROWSER_PREFIX}refresh/", REMOTE_ADDR=_address(), headers=headers),
        client.post(f"{BROWSER_PREFIX}logout/", REMOTE_ADDR=_address(), headers=headers),
    ]
    secrets_seen += [
        cookie.value
        for response in responses
        for cookie in response.cookies.values()
        if cookie.value and cookie.key != settings.BROWSER_CSRF_COOKIE_NAME
    ]

    rendered = "\n".join(StructuredFormatter().format(record) for record in caplog.records)
    exposition = generate_latest().decode()
    failure = client.post(f"{BROWSER_PREFIX}refresh/", REMOTE_ADDR=_address(), headers=headers)
    assert failure.status_code == HTTPStatus.UNAUTHORIZED
    bodies = [response.content.decode() for response in (*responses, failure)]
    for secret in secrets_seen:
        assert secret not in rendered
        assert secret not in exposition
        assert not any(secret in body for body in bodies)
        for response in responses:
            assert "Location" not in response.headers
            assert not any(
                secret in value
                for name, value in response.headers.items()
                if name.lower() != "set-cookie"
            )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("operation", ["csrf", "csrf-head", "login", "refresh", "logout"])
def test_browser_operation_statuses_match_their_documented_contracts(  # noqa: C901, PLR0915
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,  # noqa: ANN401
    operation: str,
) -> None:
    """Compare every empirically reachable status with OpenAPI.

    Drives each browser route through success, validation, authentication, CSRF, method,
    negotiation, body-size, media, throttle, dependency, and unexpected-failure paths, then asserts
    the observed set equals the documented set.

    Arguments:
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing contained operation failures.
        settings: Pytest-Django settings fixture.
        operation: Browser route under test.

    Returns:
        None.

    Raises:
        AssertionError: If observed and documented statuses differ.
    """
    for rate_name in ("TOKEN_LOGIN_ADDRESS_THROTTLE_RATE", "TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE"):
        setattr(settings, rate_name, "1000/minute")
    route = f"{BROWSER_PREFIX}{operation.removesuffix('-head')}/"
    view = {
        "csrf-head": browser_sessions_module.BrowserCsrfView,
        "csrf": browser_sessions_module.BrowserCsrfView,
        "login": browser_sessions_module.BrowserLoginView,
        "refresh": browser_sessions_module.BrowserRefreshView,
        "logout": browser_sessions_module.BrowserLogoutView,
    }[operation]
    method = {"csrf": "get", "csrf-head": "head"}.get(operation, "post")
    patched_method = "get" if method == "head" else method
    is_csrf = operation.startswith("csrf")
    observed: set[int] = set()

    def send(client: Client, **extra: Any) -> int:  # noqa: ANN401
        """Send one request on the route and record its status.

        Adds a unique client address unless one is given.
        The status is recorded for the final comparison with the schema.

        Arguments:
            client: Client sending the request.
            **extra: Request keyword arguments; ``csrf`` adds the client's current proof and
                ``verb`` overrides the HTTP method.

        Returns:
            The status code.
        """
        extra.setdefault("REMOTE_ADDR", _address())
        if extra.pop("csrf", False):
            extra["headers"] = {**extra.get("headers", {}), **_csrf_headers(client)}
        status = getattr(client, extra.pop("verb", method))(route, **extra).status_code
        observed.add(status)

        return cast("int", status)

    account = _account(django_user_model)
    client, _headers, _row = _signed_in(django_user_model)
    fresh, _fresh_headers = _browser()
    body = {"username": account.get_username(), "password": PASSWORD}
    login_body = {"data": body, "content_type": "application/json"} if operation == "login" else {}

    if is_csrf:
        send(Client())
    elif operation == "login":
        send(fresh, csrf=True, **login_body)
        send(fresh, csrf=True, data={}, content_type="application/json")
        send(
            fresh,
            csrf=True,
            data={**body, "password": PASSWORD + "-wrong"},
            content_type="application/json",
        )
        send(fresh, headers={"Origin": _origin()}, **login_body)
        send(fresh, csrf=True, data="unsupported", content_type="text/plain")
    elif operation == "refresh":
        send(client, headers={"Origin": _origin()})
        send(client, csrf=True)
        send(Client(enforce_csrf_checks=True))
    else:
        send(client, headers={"Origin": _origin()})
        send(client, csrf=True)

    probe, _probe_headers = _browser()
    if operation != "csrf-head":
        send(probe, verb="put" if is_csrf else "get", csrf=True)
    send(probe, headers={"Accept": "text/plain"}, csrf=True, **login_body)
    with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
        if is_csrf:
            send(probe, CONTENT_LENGTH="100", csrf=True)
        else:
            send(probe, csrf=True, data="oversized", content_type="text/plain")
    with override_settings(ALLOWED_HOSTS=["allowed.test"]):
        send(probe, HTTP_HOST="invalid host", csrf=True)

    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS", FUTURE_MILLISECONDS
    )
    throttle_address = _address()
    limited = (
        {"API_ANONYMOUS_THROTTLE_RATE": "1/minute"}
        if is_csrf
        else {"API_AUTHENTICATION_THROTTLE_RATE": "1/minute"}
    )
    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE="1000/minute",
        **limited,
    ):
        for _attempt in range(2):
            send(probe, REMOTE_ADDR=throttle_address, csrf=True, **login_body)

    def fail_unexpected(_view: object, _request: object) -> None:
        """Raise one contained server failure.

        Replaces only the operation so routing and error containment stay real.
        The envelope middleware must convert it.

        Arguments:
            _view: View instance.
            _request: Admitted request.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        message = "induced browser-session failure"
        raise RuntimeError(message)

    def fail_unavailable(_view: object, _request: object) -> None:
        """Raise one contained dependency failure.

        Replaces only the operation so the service-unavailable conversion stays real.
        The envelope middleware must convert it.

        Arguments:
            _view: View instance.
            _request: Admitted request.

        Returns:
            Never returns.

        Raises:
            ServiceUnavailable: Always.
        """
        raise ServiceUnavailable

    probe.raise_request_exception = False
    with monkeypatch.context() as failure:
        failure.setattr(view, patched_method, fail_unexpected)
        send(probe, csrf=True, **login_body)
    if not is_csrf:
        with monkeypatch.context() as outage:
            outage.setattr(view, patched_method, fail_unavailable)
            send(probe, csrf=True, **login_body)

    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])[route][method]["responses"],
        )
    }

    assert observed == documented
