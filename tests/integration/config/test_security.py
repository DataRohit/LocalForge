"""Integration coverage for cross-cutting API security.

Exercises the fixed public routes through Django and DRF so cache-backed scope limits, browser
headers, CORS, proxy address trust, and correlated failures are verified at their public seams.
"""

import os
import secrets
import uuid
from concurrent.futures import ProcessPoolExecutor
from http import HTTPStatus
from importlib import import_module
from ipaddress import ip_network
from multiprocessing import get_context
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from django.db import connections
from django.test import Client, override_settings
from rest_framework.authtoken.models import Token

from accounts.jwt_authentication import PrimaryRefreshToken
from config.api import ErrorCode
from tests.integration.config.throttle_worker import (
    AuthenticatedAdmissionCase,
    count_anonymous_api_admissions,
    count_authenticated_api_admissions,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from accounts.models import User

ACCOUNT_SECRET = secrets.token_urlsafe(24)
HIGH_RATE = "1000/minute"
LOW_RATE = "2/second"
FIXED_SERVER_TIME_MILLISECONDS = 1_800_000_000_250
pytestmark = pytest.mark.api_runtime


class ResponseProtocol(Protocol):
    """Describe the Django test response surface used by assertions.

    Inherits from ``Protocol`` and exposes status, headers, and JSON decoding without coupling test
    typing to the framework's dynamically augmented response implementation.

    Attributes:
        status_code: HTTP response status.
        headers: Case-insensitive response header mapping.

    Members:
        json: Decode the JSON response body.
    """

    status_code: int

    @property
    def headers(self) -> Mapping[str, str]:
        """Return response headers.

        Exposes only read access to the case-insensitive mapping so concrete framework response
        types satisfy the protocol without widening their mutable header implementation.

        Arguments:
            None.

        Returns:
            Case-insensitive header mapping.
        """
        ...

    def json(self) -> object:
        """Decode the response body.

        Represents the dynamically attached Django test-client helper without constraining the
        JSON-compatible value it returns.

        Arguments:
            None.

        Returns:
            Parsed JSON-compatible value.
        """
        ...


def _assert_correlated_throttle(response: ResponseProtocol) -> int:
    """Assert one standard throttled response and return its retry delay.

    Checks the public status, stable envelope, response correlation, and positive integer
    ``Retry-After`` contract without reaching into throttle storage.

    Arguments:
        response: Django test response returned by an existing API route.

    Returns:
        Retry delay in whole seconds.

    Raises:
        AssertionError: If the response violates the throttling contract.
    """
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert payload == {
        "code": ErrorCode.THROTTLED,
        "message": "Too many requests.",
        "details": {},
        "request_id": response.headers["X-Request-ID"],
    }
    retry_after = int(response.headers["Retry-After"])
    assert retry_after > 0

    return retry_after


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(15)
def test_authentication_recovery_scope_trips_exactly_and_recovers(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Share one exact authentication scope across credential and recovery routes.

    Admits two rejected credentials on existing public routes, rejects a recovery request with a
    correlated delay, then advances the fixed clock and observes normal recovery processing.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture controlling the fixed-window clock.

    Returns:
        None.

    Raises:
        AssertionError: If the shared threshold, response, or recovery timing changes.
    """
    address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    current_time = [FIXED_SERVER_TIME_MILLISECONDS]
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        current_time[0],
    )

    with override_settings(
        API_AUTHENTICATION_THROTTLE_RATE=LOW_RATE,
        API_ANONYMOUS_THROTTLE_RATE=HIGH_RATE,
        PASSWORD_RESET_ADDRESS_THROTTLE_RATE=HIGH_RATE,
        PASSWORD_RESET_ACCOUNT_THROTTLE_RATE=HIGH_RATE,
        USERNAME_RESET_ADDRESS_THROTTLE_RATE=HIGH_RATE,
        USERNAME_RESET_ACCOUNT_THROTTLE_RATE=HIGH_RATE,
    ):
        first = client.post(
            "/api/v1/jwt/verify/",
            data={"token": "invalid"},
            content_type="application/json",
            REMOTE_ADDR=address,
        )
        second = client.post(
            "/api/v1/jwt/refresh/",
            data={"refresh": "invalid"},
            content_type="application/json",
            REMOTE_ADDR=address,
        )
        throttled = client.post(
            "/api/v1/users/reset_username/",
            data={"email": "unknown-ticket35@example.invalid"},
            content_type="application/json",
            REMOTE_ADDR=address,
            HTTP_ORIGIN="http://localhost:8080",
        )
        retry_after = _assert_correlated_throttle(throttled)
        current_time[0] += retry_after * 1000
        monkeypatch.setattr(
            "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
            current_time[0],
        )
        recovered = client.post(
            "/api/v1/users/reset_username/",
            data={"email": "unknown-ticket35@example.invalid"},
            content_type="application/json",
            REMOTE_ADDR=address,
        )

    assert first.status_code == HTTPStatus.UNAUTHORIZED
    assert second.status_code == HTTPStatus.UNAUTHORIZED
    assert throttled.headers["Access-Control-Expose-Headers"] == "Retry-After, X-Request-ID"
    assert recovered.status_code == HTTPStatus.ACCEPTED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(15)
def test_anonymous_scope_trips_exactly_and_recovers(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Limit anonymous use independently of the tighter authentication scope.

    Raises the route-specific scope, admits exactly two anonymous requests, rejects the third, and
    proves the advertised fixed window restores access without clearing shared cache state.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture controlling the fixed-window clock.

    Returns:
        None.

    Raises:
        AssertionError: If anonymous scope threshold or recovery changes.
    """
    address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    current_time = [FIXED_SERVER_TIME_MILLISECONDS + 10_000]
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        current_time[0],
    )

    with override_settings(
        API_AUTHENTICATION_THROTTLE_RATE=HIGH_RATE,
        API_ANONYMOUS_THROTTLE_RATE=LOW_RATE,
    ):
        responses = [
            client.post(
                "/api/v1/jwt/verify/",
                data={},
                content_type="application/json",
                REMOTE_ADDR=address,
            )
            for _attempt in range(3)
        ]
        retry_after = _assert_correlated_throttle(responses[-1])
        current_time[0] += retry_after * 1000
        monkeypatch.setattr(
            "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
            current_time[0],
        )
        recovered = client.post(
            "/api/v1/jwt/verify/",
            data={},
            content_type="application/json",
            REMOTE_ADDR=address,
        )

    assert [response.status_code for response in responses[:2]] == [
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.BAD_REQUEST,
    ]
    assert recovered.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(15)
def test_authenticated_read_scope_limits_account_across_addresses(
    django_user_model: type[User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent one authenticated account from evading limits by changing address.

    Authenticates one existing account, varies the apparent direct address for every profile read,
    and observes that the immutable account dimension rejects the third request.

    Arguments:
        django_user_model: Configured custom account model.
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If account-scoped authenticated reads can evade the threshold.
    """
    account = django_user_model.objects.create_user(
        username="read-account-limit",
        email="read-account-limit@example.invalid",
        password=ACCOUNT_SECRET,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 30_000,
    )

    with override_settings(API_AUTHENTICATED_READ_THROTTLE_RATE=LOW_RATE):
        responses = [
            client.get(
                "/api/v1/users/me/",
                HTTP_AUTHORIZATION=f"Token {token.key}",
                REMOTE_ADDR=f"198.51.100.{40 + attempt}",
            )
            for attempt in range(3)
        ]

    assert [response.status_code for response in responses[:2]] == [
        HTTPStatus.OK,
        HTTPStatus.OK,
    ]
    _assert_correlated_throttle(responses[-1])


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(15)
def test_authenticated_read_scope_limits_address_across_accounts(
    django_user_model: type[User],
    client: Client,
) -> None:
    """Keep unrelated authenticated accounts independent on one shared address.

    Reads two profiles twice from one direct address and proves each account receives its own
    account-plus-address composite budget instead of sharing a tight global address counter.

    Arguments:
        django_user_model: Configured custom account model.
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If one account can consume another account's shared-address budget.
    """
    headers = []
    for attempt in range(3):
        account = django_user_model.objects.create_user(
            username=f"read-address-{attempt}",
            email=f"read-address-{attempt}@example.invalid",
            password=ACCOUNT_SECRET,
            is_active=True,
        )
        token = Token.objects.using("default").create(user=account)
        headers.append(f"Token {token.key}")

    with override_settings(API_AUTHENTICATED_READ_THROTTLE_RATE=LOW_RATE):
        responses = [
            client.get(
                "/api/v1/users/me/",
                HTTP_AUTHORIZATION=authorization,
                REMOTE_ADDR="198.51.100.50",
            )
            for authorization in headers
        ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.OK,
        HTTPStatus.OK,
        HTTPStatus.OK,
    ]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(15)
def test_identified_throttle_dimensions_follow_account_and_composite_sequence(
    django_user_model: type[User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforce exact A/B and X/Y account-plus-composite admissions atomically.

    Exhausts A at X, verifies the denied request does not poison B at X, then proves changing to Y
    cannot evade either account limit while an unrelated B receives its complete independent limit.

    Arguments:
        django_user_model: Configured custom account model.
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If dimensions are global-address, non-atomic, or independently incremented.
    """
    accounts = [
        django_user_model.objects.create_user(
            username=f"dimension-{label}",
            email=f"dimension-{label}@example.invalid",
            password=ACCOUNT_SECRET,
            is_active=True,
        )
        for label in ("a", "b")
    ]
    authorizations = [
        f"Token {Token.objects.using('default').create(user=account).key}" for account in accounts
    ]
    x_address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    y_address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 40_000,
    )

    def read(account_index: int, address: str) -> ResponseProtocol:
        """Read one account profile through its public authenticated seam.

        Dispatches the selected account credential and address through the real profile route so
        assertions observe only public admission behavior.

        Arguments:
            account_index: Position of the account credential to use.
            address: Direct client address supplying the composite dimension.

        Returns:
            Public Django response.
        """
        return cast(
            "ResponseProtocol",
            client.get(
                "/api/v1/users/me/",
                HTTP_AUTHORIZATION=authorizations[account_index],
                REMOTE_ADDR=address,
            ),
        )

    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE=HIGH_RATE,
        API_AUTHENTICATED_READ_THROTTLE_RATE=LOW_RATE,
    ):
        a_x = [read(0, x_address) for _attempt in range(3)]
        b_x = [read(1, x_address) for _attempt in range(2)]
        a_y = read(0, y_address)
        b_y = read(1, y_address)

    assert [response.status_code for response in a_x[:2]] == [HTTPStatus.OK, HTTPStatus.OK]
    _assert_correlated_throttle(a_x[-1])
    assert [response.status_code for response in b_x] == [HTTPStatus.OK, HTTPStatus.OK]
    _assert_correlated_throttle(a_y)
    _assert_correlated_throttle(b_y)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_authentication_scope_limits_signed_token_account_across_addresses(
    django_user_model: type[User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent one token account from evading authentication limits by changing address.

    Verifies a valid signed token supplies its immutable account dimension before the public verify
    view runs, so a second address cannot obtain a separate authentication budget.

    Arguments:
        django_user_model: Configured custom account model.
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If a signed token can rotate addresses around the account threshold.
    """
    account = django_user_model.objects.create_user(
        username="jwt-account-limit",
        email="jwt-account-limit@example.invalid",
        password=ACCOUNT_SECRET,
        is_active=True,
    )
    access = str(PrimaryRefreshToken.for_user(account).access_token)
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 50_000,
    )

    with override_settings(
        API_AUTHENTICATION_THROTTLE_RATE="1/minute",
        API_ANONYMOUS_THROTTLE_RATE=HIGH_RATE,
    ):
        admitted = client.post(
            "/api/v1/jwt/verify/",
            data={"token": access},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.53",
        )
        throttled = client.post(
            "/api/v1/jwt/verify/",
            data={"token": access},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.54",
        )

    assert admitted.status_code == HTTPStatus.OK
    _assert_correlated_throttle(throttled)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(15)
def test_signed_subject_precedes_undeclared_body_identity(
    django_user_model: type[User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject spoof fields without letting them rotate a signed subject's account bucket.

    Sends a valid declared token beside an undeclared account selector, then retries from another
    address without the selector and proves both requests share the cryptographically signed owner.

    Arguments:
        django_user_model: Configured custom account model.
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If a raw undeclared identity outranks the signed token subject.
    """
    account = django_user_model.objects.create_user(
        username="signed-subject-priority",
        email="signed-subject-priority@example.invalid",
        password=ACCOUNT_SECRET,
        is_active=True,
    )
    access = str(PrimaryRefreshToken.for_user(account).access_token)
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 50_000,
    )

    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE=HIGH_RATE,
        API_ANONYMOUS_THROTTLE_RATE=HIGH_RATE,
        API_AUTHENTICATION_THROTTLE_RATE="1/minute",
    ):
        spoofed = client.post(
            "/api/v1/jwt/verify/",
            data={"token": access, "account": str(uuid.uuid4())},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.55",
        )
        repeated = client.post(
            "/api/v1/jwt/verify/",
            data={"token": access},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.56",
        )

    assert spoofed.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", spoofed.json())["details"] == {
        "account": ["This field is not allowed."]
    }
    _assert_correlated_throttle(repeated)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_undeclared_raw_identity_cannot_burn_an_account_bucket(client: Client) -> None:
    """Ignore undeclared raw account selectors while rejecting their request shape.

    Repeats one spoofed account value from two source addresses and proves neither request can make
    the other consume an account-scoped budget because no authenticated or signed subject exists.

    Arguments:
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If undeclared raw fields create or rotate account dimensions.
    """
    body = {"token": "invalid", "email": "victim@example.invalid"}
    addresses = (
        f"2001:db8::{uuid.uuid4().hex[:4]}",
        f"2001:db8::{uuid.uuid4().hex[:4]}",
    )
    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE=HIGH_RATE,
        API_ANONYMOUS_THROTTLE_RATE=HIGH_RATE,
        API_AUTHENTICATION_THROTTLE_RATE="1/minute",
    ):
        responses = [
            client.post(
                "/api/v1/jwt/verify/",
                data=body,
                content_type="application/json",
                REMOTE_ADDR=address,
            )
            for address in addresses
        ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.BAD_REQUEST,
    ]
    assert all(
        cast("dict[str, Any]", response.json())["details"]
        == {"email": ["This field is not allowed."]}
        for response in responses
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_signed_subject_field_requires_an_object_body(client: Client) -> None:
    """Treat a non-object body as unidentified without bypassing validation.

    Sends an array to the signed-token operation and verifies the throttle identity resolver leaves
    it to the serializer's ordinary object-shape validation rather than inventing a subject.

    Arguments:
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If non-object data creates identity or bypasses validation.
    """
    response = client.post(
        "/api/v1/jwt/verify/",
        data=[],
        content_type="application/json",
        REMOTE_ADDR="198.51.100.60",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.VALIDATION_ERROR


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_token_login_rejects_undeclared_identity_fields(client: Client) -> None:
    """Reject token-login keys outside the declared credential contract.

    Exercises the public serializer seam with a spoofable email field so it cannot silently pass
    validation or become general-throttle identity material.

    Arguments:
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If token login silently ignores undeclared fields.
    """
    response = client.post(
        "/api/v1/token/login/",
        data={
            "username": "unknown",
            "password": ACCOUNT_SECRET,
            "email": "victim@example.invalid",
        },
        content_type="application/json",
        REMOTE_ADDR="198.51.100.59",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", response.json())["details"] == {
        "email": ["This field is not allowed."]
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(15)
def legacy_api_boundary_charges_pre_framework_failures(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Charge malformed, negotiated, and unauthenticated failures before framework processing.

    Sends four failures that terminate at distinct pre-view stages and proves the fifth request from
    the same source receives the correlated boundary throttle response.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If any early failure bypasses the source aggregate.
    """
    address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 50_000,
    )
    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE="4/minute",
        API_ANONYMOUS_THROTTLE_RATE=HIGH_RATE,
        API_AUTHENTICATION_THROTTLE_RATE=HIGH_RATE,
    ):
        malformed = client.generic(
            "POST",
            "/api/v1/jwt/verify/",
            data=b"{",
            content_type="application/json",
            REMOTE_ADDR=address,
        )
        unsupported = client.post(
            "/api/v1/jwt/verify/",
            data="token=invalid",
            content_type="text/plain",
            REMOTE_ADDR=address,
        )
        unacceptable = client.get(
            "/api/v1/users/me/",
            HTTP_ACCEPT="text/plain",
            REMOTE_ADDR=address,
        )
        invalid_auth = client.get(
            "/api/v1/users/me/",
            HTTP_AUTHORIZATION="Bearer invalid",
            REMOTE_ADDR=address,
        )
        throttled = client.get(
            "/api/v1/users/me/",
            REMOTE_ADDR=address,
            HTTP_ORIGIN="http://localhost:8080",
        )

    assert malformed.status_code == HTTPStatus.BAD_REQUEST
    assert unsupported.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert unacceptable.status_code == HTTPStatus.NOT_ACCEPTABLE
    assert invalid_auth.status_code == HTTPStatus.UNAUTHORIZED
    _assert_correlated_throttle(cast("ResponseProtocol", throttled))
    assert throttled.headers["X-Content-Type-Options"] == "nosniff"
    assert throttled.headers["Access-Control-Allow-Origin"] == "http://localhost:8080"
    assert throttled.headers["Access-Control-Allow-Credentials"] == "true"
    assert "Origin" in throttled.headers["Vary"]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def legacy_boundary_rejection_precedes_body_parser(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an exhausted source before malformed JSON reaches a body parser.

    Uses a negotiation failure to consume the one-request boundary budget, replaces the JSON parser
    with a sentinel failure, and proves the next write returns 429 without invoking that parser.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture replacing the parser seam after the first admission.

    Returns:
        None.

    Raises:
        AssertionError: If the body parser runs before source admission.
    """

    def fail_parse(*_args: object, **_kwargs: object) -> None:
        """Fail if exhausted source admission reaches JSON parsing.

        Supplies a sentinel parser implementation after source capacity is exhausted, making any
        incorrect framework entry observable as an immediate test failure.

        Arguments:
            *_args: Positional parser arguments.
            **_kwargs: Keyword parser arguments.

        Returns:
            None.

        Raises:
            AssertionError: Always, because admission should reject first.
        """
        message = "body parser ran before boundary admission"
        raise AssertionError(message)

    address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 50_000,
    )
    with override_settings(API_BOUNDARY_ADDRESS_THROTTLE_RATE="1/minute"):
        consumed = client.get(
            "/api/v1/users/me/",
            HTTP_ACCEPT="text/plain",
            REMOTE_ADDR=address,
        )
        monkeypatch.setattr("rest_framework.parsers.JSONParser.parse", fail_parse)
        throttled = client.generic(
            "POST",
            "/api/v1/jwt/verify/",
            data=b"{",
            content_type="application/json",
            REMOTE_ADDR=address,
        )

    assert consumed.status_code == HTTPStatus.NOT_ACCEPTABLE
    _assert_correlated_throttle(cast("ResponseProtocol", throttled))


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_anonymous_scope_is_shared_across_spawned_application_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforce one cache-backed threshold across isolated Django processes.

    Spawns two complete runtimes that call the same fixed public route and verifies their combined
    admissions equal one shared limit rather than one limit per process.

    Arguments:
        monkeypatch: Fixture removing inherited coverage process configuration.

    Returns:
        None.

    Raises:
        AssertionError: If separate application processes hold separate throttle state.
    """
    workers = 2
    attempts_per_worker = 4
    limit = 3
    address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    for name in tuple(os.environ):
        if name.startswith("COV_CORE_") or name == "COVERAGE_PROCESS_START":
            monkeypatch.delenv(name)

    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=get_context("spawn"),
    ) as executor:
        futures = [
            executor.submit(
                count_anonymous_api_admissions,
                address,
                attempts_per_worker,
                limit,
                FIXED_SERVER_TIME_MILLISECONDS,
            )
            for _worker in range(workers)
        ]
        admitted = sum(future.result(timeout=20) for future in futures)

    assert admitted == limit


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_identified_scope_is_exact_across_spawned_application_processes(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforce one exact atomic account-plus-composite limit across processes.

    Coordinates both spawned runtimes in one injected server-time window and verifies their
    combined authenticated admissions equal the configured limit without a wall-boundary race.

    Arguments:
        django_user_model: Configured custom account model.
        monkeypatch: Fixture removing inherited coverage process configuration.

    Returns:
        None.

    Raises:
        AssertionError: If workers over-admit, under-admit, or use separate windows.
    """
    account = django_user_model.objects.create_user(
        username="multiprocess-identified",
        email="multiprocess-identified@example.invalid",
        password=ACCOUNT_SECRET,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    workers = 2
    attempts_per_worker = 4
    limit = 3
    address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    for name in tuple(os.environ):
        if name.startswith("COV_CORE_") or name == "COVERAGE_PROCESS_START":
            monkeypatch.delenv(name)

    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=get_context("spawn"),
    ) as executor:
        futures = [
            executor.submit(
                count_authenticated_api_admissions,
                AuthenticatedAdmissionCase(
                    address=address,
                    authorization=f"Token {token.key}",
                    attempts=attempts_per_worker,
                    limit=limit,
                    database_name=str(connections["default"].settings_dict["NAME"]),
                    fixed_time_milliseconds=FIXED_SERVER_TIME_MILLISECONDS,
                ),
            )
            for _worker in range(workers)
        ]
        admitted = sum(future.result(timeout=20) for future in futures)

    assert admitted == limit


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_direct_spoofed_forwarded_address_does_not_evade_anonymous_scope(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore forwarded addresses supplied by an untrusted direct peer.

    Sends each request with a different spoofed forwarded value while the direct peer remains
    constant and proves the third attempt is still rejected from the direct-address bucket.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If untrusted forwarded metadata changes the throttle identity.
    """
    direct_address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 50_000,
    )
    with override_settings(
        API_AUTHENTICATION_THROTTLE_RATE=HIGH_RATE,
        API_ANONYMOUS_THROTTLE_RATE=LOW_RATE,
        TRUSTED_PROXY_NETWORKS=(),
    ):
        responses = [
            client.post(
                "/api/v1/jwt/verify/",
                data={},
                content_type="application/json",
                REMOTE_ADDR=direct_address,
                HTTP_X_FORWARDED_FOR=f"203.0.113.{attempt}",
            )
            for attempt in range(3)
        ]

    _assert_correlated_throttle(responses[-1])


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_trusted_proxy_forwarded_clients_receive_distinct_address_buckets(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use forwarded clients only when the immediate peer is the configured proxy.

    Sends two clients through one trusted edge peer, admits each distinct address once, and rejects
    only a repeated client so proxy sharing does not collapse all callers into one bucket.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If trusted forwarded clients are ignored or merged.
    """
    first_address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    second_address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 50_000,
    )
    with override_settings(
        API_AUTHENTICATION_THROTTLE_RATE=HIGH_RATE,
        API_ANONYMOUS_THROTTLE_RATE="1/minute",
        TRUSTED_PROXY_NETWORKS=(ip_network("10.89.2.0/24"),),
    ):
        first = client.post(
            "/api/v1/jwt/verify/",
            data={},
            content_type="application/json",
            REMOTE_ADDR="10.89.2.12",
            HTTP_X_FORWARDED_FOR=first_address,
        )
        second = client.post(
            "/api/v1/jwt/verify/",
            data={},
            content_type="application/json",
            REMOTE_ADDR="10.89.2.12",
            HTTP_X_FORWARDED_FOR=second_address,
        )
        repeated = client.post(
            "/api/v1/jwt/verify/",
            data={},
            content_type="application/json",
            REMOTE_ADDR="10.89.2.12",
            HTTP_X_FORWARDED_FOR=first_address,
        )

    assert first.status_code == HTTPStatus.BAD_REQUEST
    assert second.status_code == HTTPStatus.BAD_REQUEST
    _assert_correlated_throttle(repeated)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_security_headers_and_exact_origin_cors_cover_api_responses(client: Client) -> None:
    """Attach every browser defense and reflect only one configured origin.

    Compares allowed and disallowed origins on the same fixed API route while also asserting
    content type, nosniff, frame, referrer, and content-security-policy headers.

    Arguments:
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If a hardening header is absent or CORS reflects an unlisted origin.
    """
    allowed = client.post(
        "/api/v1/jwt/verify/",
        data={},
        content_type="application/json",
        REMOTE_ADDR="198.51.100.80",
        HTTP_ORIGIN="http://localhost:8080",
    )
    denied = client.post(
        "/api/v1/jwt/verify/",
        data={},
        content_type="application/json",
        REMOTE_ADDR="198.51.100.81",
        HTTP_ORIGIN="http://attacker.invalid",
    )
    no_origin = client.post(
        "/api/v1/jwt/verify/",
        data={},
        content_type="application/json",
        REMOTE_ADDR="198.51.100.84",
    )

    assert allowed.headers["Content-Type"].startswith("application/json")
    assert allowed.headers["X-Content-Type-Options"] == "nosniff"
    assert allowed.headers["X-Frame-Options"] == "DENY"
    assert allowed.headers["Referrer-Policy"] == "same-origin"
    assert allowed.headers["Content-Security-Policy"] == (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    )
    assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:8080"
    assert allowed.headers["Access-Control-Allow-Credentials"] == "true"
    assert allowed.headers["Access-Control-Expose-Headers"] == "Retry-After, X-Request-ID"
    assert "Origin" in allowed.headers["Vary"]
    assert "Access-Control-Allow-Origin" not in denied.headers
    assert "Access-Control-Allow-Credentials" not in denied.headers
    assert "Access-Control-Expose-Headers" not in denied.headers
    assert "Access-Control-Expose-Headers" not in no_origin.headers


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_allowed_origin_can_omit_cross_origin_credentials(client: Client) -> None:
    """Reflect an exact allowed origin without credentials when configured.

    Disables the credential flag for one request and verifies the origin remains exact while the
    credential permission header is absent rather than emitted with a false-like value.

    Arguments:
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If credential-disabled CORS still permits browser credentials.
    """
    with override_settings(CORS_ALLOW_CREDENTIALS=False):
        response = client.post(
            "/api/v1/jwt/verify/",
            data={},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.83",
            HTTP_ORIGIN="http://localhost:8080",
        )

    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:8080"
    assert "Access-Control-Allow-Credentials" not in response.headers


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_allowed_origin_preflight_bypasses_credentials_without_wildcards(client: Client) -> None:
    """Answer an exact-origin browser preflight before endpoint authentication.

    Exercises the middleware-owned public preflight seam for an authenticated fixed route and
    verifies only bounded methods and headers are advertised with credentials.

    Arguments:
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If preflight is denied, widened, or reflected as a wildcard.
    """
    response = client.options(
        "/api/v1/users/me/",
        REMOTE_ADDR="198.51.100.82",
        HTTP_ORIGIN="http://localhost:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,x-request-id",
    )

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:8080"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert response.headers["Access-Control-Allow-Methods"] == "DELETE, GET, HEAD, OPTIONS, PATCH"
    assert response.headers["Access-Control-Allow-Headers"] == (
        "Accept, Authorization, Content-Type, X-CSRFToken, X-Request-ID"
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/api/v1/unknown/", HTTPStatus.NOT_FOUND),
        ("/admin/", HTTPStatus.FOUND),
        ("/health/", HTTPStatus.OK),
    ],
)
def test_preflight_never_short_circuits_non_api_or_unresolved_routes(
    client: Client,
    path: str,
    expected_status: HTTPStatus,
) -> None:
    """Leave unknown, administration, and health OPTIONS requests to normal routing.

    Submits a syntactically valid allowed-origin preflight outside a resolvable versioned operation
    and verifies CORS middleware does not replace the route's ordinary result with 204.

    Arguments:
        client: Django client supplied by the integration harness.
        path: Unknown or non-versioned route under test.
        expected_status: Normal routing status expected from that route.

    Returns:
        None.

    Raises:
        AssertionError: If CORS middleware treats the route as an API operation.
    """
    response = client.options(
        path,
        HTTP_ORIGIN="http://localhost:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
    )

    assert response.status_code == expected_status


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_preflight_delegates_a_versioned_non_class_resolver(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decline preflight short-circuiting when resolution has no API view class.

    Replaces only Django's resolver result at the browser boundary and verifies a function-shaped
    versioned match delegates to the real route instead of advertising invented methods.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture replacing the resolver result.

    Returns:
        None.

    Raises:
        AssertionError: If method discovery assumes every match is a class-based API view.
    """
    monkeypatch.setattr(
        "config.security.resolve",
        lambda _path: SimpleNamespace(namespaces=["api-v1"], func=object()),
    )

    response = client.options(
        "/health/",
        HTTP_ORIGIN="http://localhost:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
    )

    assert response.status_code == HTTPStatus.OK
    assert "Access-Control-Allow-Methods" not in response.headers


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_preflight_advertises_only_the_resolved_operation_methods(client: Client) -> None:
    """Describe the concrete resolved route instead of one global method list.

    Compares a POST-only token operation with the mixed self-profile operation so each preflight
    receives only methods Django can dispatch for that resolved view.

    Arguments:
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If unrelated methods are advertised.
    """
    token = client.options(
        "/api/v1/jwt/verify/",
        HTTP_ORIGIN="http://localhost:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    profile = client.options(
        "/api/v1/users/me/",
        HTTP_ORIGIN="http://localhost:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="PATCH",
    )

    assert token.status_code == HTTPStatus.NO_CONTENT
    assert token.headers["Access-Control-Allow-Methods"] == "OPTIONS, POST"
    assert profile.headers["Access-Control-Allow-Methods"] == "DELETE, GET, HEAD, OPTIONS, PATCH"


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_every_origin_varies_ordinary_responses(client: Client) -> None:
    """Vary ordinary responses on Origin whether the value is allowed or denied.

    Exercises the same resolved route with an unlisted origin and proves caches cannot reuse that
    denial for a later allowed-origin request.

    Arguments:
        client: Django client supplied by the integration harness.

    Returns:
        None.

    Raises:
        AssertionError: If a denied origin omits the cache variance contract.
    """
    response = client.post(
        "/api/v1/jwt/verify/",
        data={},
        content_type="application/json",
        REMOTE_ADDR="198.51.100.85",
        HTTP_ORIGIN="http://attacker.invalid",
    )

    assert "Origin" in response.headers["Vary"]
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_head_and_options_do_not_consume_tight_authentication_scope(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exclude safe and metadata methods from unsupported write-operation account budgets.

    Sends HEAD and non-preflight OPTIONS to a POST-only token route, then proves the first actual
    write still receives the complete one-request authentication budget.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If unsupported safe or metadata methods charge the tight write scope.
    """
    address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 50_000,
    )
    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE=HIGH_RATE,
        API_ANONYMOUS_THROTTLE_RATE=HIGH_RATE,
        API_AUTHENTICATION_THROTTLE_RATE="1/minute",
    ):
        head = client.head("/api/v1/jwt/verify/", REMOTE_ADDR=address)
        options = client.options("/api/v1/jwt/verify/", REMOTE_ADDR=address)
        write = client.post(
            "/api/v1/jwt/verify/",
            data={"token": "invalid"},
            content_type="application/json",
            REMOTE_ADDR=address,
        )

    assert head.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert options.status_code == HTTPStatus.OK
    assert write.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_head_consumes_authenticated_read_scope(
    django_user_model: type[User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classify HEAD with GET in the authenticated-read budget.

    Performs one automatic HEAD against the self-profile operation and proves a following GET for
    the same account and address is throttled at the one-request read limit.

    Arguments:
        django_user_model: Configured custom account model.
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture fixing every request in one Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If HEAD bypasses the authenticated-read scope.
    """
    account = django_user_model.objects.create_user(
        username="head-read-scope",
        email="head-read-scope@example.invalid",
        password=ACCOUNT_SECRET,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        FIXED_SERVER_TIME_MILLISECONDS + 50_000,
    )
    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE=HIGH_RATE,
        API_AUTHENTICATED_READ_THROTTLE_RATE="1/minute",
    ):
        head = client.head(
            "/api/v1/users/me/",
            HTTP_AUTHORIZATION=f"Token {token.key}",
            REMOTE_ADDR="198.51.100.87",
        )
        throttled = client.get(
            "/api/v1/users/me/",
            HTTP_AUTHORIZATION=f"Token {token.key}",
            REMOTE_ADDR="198.51.100.87",
        )

    assert head.status_code == HTTPStatus.OK
    _assert_correlated_throttle(cast("ResponseProtocol", throttled))


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(15)
def test_authenticated_read_account_and_composite_dimensions_recover(
    django_user_model: type[User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recover both authenticated account and composite dimensions after Retry-After.

    Exhausts one account and composite at one address, advances the coordinated server-time seam
    by the advertised delay, then succeeds through both renewed dimensions without clearing.

    Arguments:
        django_user_model: Configured custom account model.
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture advancing the coordinated Valkey window.

    Returns:
        None.

    Raises:
        AssertionError: If either dimension outlives its advertised fixed window.
    """
    account = django_user_model.objects.create_user(
        username="read-recovery",
        email="read-recovery@example.invalid",
        password=ACCOUNT_SECRET,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    authorization = f"Token {token.key}"
    current_time = FIXED_SERVER_TIME_MILLISECONDS + 20_000
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        current_time,
    )

    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE=HIGH_RATE,
        API_AUTHENTICATED_READ_THROTTLE_RATE="1/second",
    ):
        admitted = client.get(
            "/api/v1/users/me/",
            HTTP_AUTHORIZATION=authorization,
            REMOTE_ADDR="198.51.100.88",
        )
        denied = client.get(
            "/api/v1/users/me/",
            HTTP_AUTHORIZATION=authorization,
            REMOTE_ADDR="198.51.100.88",
        )
        retry_after = _assert_correlated_throttle(cast("ResponseProtocol", denied))
        monkeypatch.setattr(
            "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
            current_time + retry_after * 1000,
        )
        recovered = client.get(
            "/api/v1/users/me/",
            HTTP_AUTHORIZATION=authorization,
            REMOTE_ADDR="198.51.100.88",
        )

    assert admitted.status_code == HTTPStatus.OK
    assert recovered.status_code == HTTPStatus.OK


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_every_documented_throttle_response_declares_exact_headers() -> None:
    """Document Retry-After and correlation headers on every current 429 response.

    Generates the complete fixed API schema and inspects each operation carrying throttling so no
    route documents the body while omitting protocol headers clients must consume.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a current 429 response omits or widens either header schema.
    """
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    paths = cast("dict[str, Any]", schema["paths"])
    throttled_operations = [
        operation
        for path_item in paths.values()
        for operation in cast("dict[str, Any]", path_item).values()
        if isinstance(operation, dict) and "429" in operation.get("responses", {})
    ]

    assert throttled_operations
    for operation in throttled_operations:
        response = cast("dict[str, Any]", operation["responses"]["429"])
        assert response["headers"] == {
            "Retry-After": {
                "description": (
                    "Whole seconds until the fixed or rolling admission window recovers."
                ),
                "schema": {"type": "integer", "minimum": 1},
            },
            "X-Request-ID": {
                "description": "Request correlation identifier echoed by the error envelope.",
                "schema": {"type": "string", "format": "uuid"},
            },
        }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_general_cache_scope_fails_open_without_weakening_security_admission(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep evictable general throttling fail-open at a cache boundary.

    Replaces only the general cache counter with an unavailable adapter and verifies a public token
    validation request reaches its normal response instead of becoming an authoritative denial.

    Arguments:
        client: Django client supplied by the integration harness.
        monkeypatch: Fixture replacing the external cache boundary.

    Returns:
        None.

    Raises:
        AssertionError: If general cache loss blocks the request.
    """

    class UnavailableCounterCache:
        """Represent an unavailable general cache.

        Inherits nothing and mirrors the two cache operations reusable throttles need, reporting an
        absent counter while leaving PostgreSQL security admission untouched.

        Attributes:
            None.

        Members:
            atomic_fixed_window_admit: Report unavailable atomic admission state.
        """

        def atomic_fixed_window_admit(
            self,
            keys: tuple[str, ...],
            *,
            limit: int,
            window_seconds: int,
            now_milliseconds: int | None = None,
        ) -> None:
            """Report that no atomic admission decision is available.

            Accepts the cache adapter contract but deliberately stores nothing, matching the
            resilient cache behavior during a connection failure.

            Arguments:
                keys: Opaque dimension keys.
                limit: Configured admission limit.
                window_seconds: Fixed-window duration.
                now_milliseconds: Optional coordinated test time.

            Returns:
                None because the cache is unavailable.
            """
            del keys, limit, window_seconds, now_milliseconds

    monkeypatch.setattr(
        "accounts.api_throttling.caches",
        {"default": UnavailableCounterCache()},
    )

    response = client.post(
        "/api/v1/jwt/verify/",
        data={},
        content_type="application/json",
        REMOTE_ADDR="198.51.100.84",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
