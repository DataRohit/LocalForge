"""Integration tests for JSON web token authentication.

Exercises the versioned create, refresh, and verify HTTP boundaries against PostgreSQL, proving
issued credentials, account-state decisions, rotation, and response contracts where clients use
them.
"""

# mypy: disable-error-code=misc

import json
import logging
import secrets
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
from importlib import import_module
from threading import Barrier, Event
from typing import TYPE_CHECKING, Any, cast

import pytest
from django.conf import settings
from django.contrib.auth.hashers import (
    PBKDF2PasswordHasher,
    get_hashers,
    identify_hasher,
    make_password,
)
from django.core.management import call_command
from django.db import DatabaseError, connections
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import QuerySet
from django.test import Client as DjangoClient
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.authtoken.models import Token as DRFToken
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

import accounts.jwt_authentication as jwt_authentication_module
import accounts.token_authentication as token_authentication_module
from accounts.authentication import JWTAuthentication
from accounts.login_throttle import PostgresLoginThrottleStore
from accounts.models import User
from config.api_errors import ErrorCode, ServiceUnavailable
from config.logs import REQUEST_ID_HEADER, StructuredFormatter

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Protocol

    from django.test import Client

    class ClientResponse(Protocol):
        """Describe public response values used by JWT tests.

        Inherits from ``Protocol`` and exposes only status, headers, content, and JSON decoding,
        avoiding dependence on Django's dynamic test-response subtype.

        Attributes:
            status_code: Observed HTTP status.
            headers: Public response headers.
            content: Rendered response body.

        Members:
            json: Decode the response body as JSON.
        """

        status_code: int
        headers: Mapping[str, str]
        content: bytes

        def json(self) -> object:
            """Decode the rendered response body.

            Uses Django's public response helper contract without naming its dynamic concrete type.
            Keeps each assertion on the same client-facing JSON seam.

            Arguments:
                None.

            Returns:
                Parsed JSON-compatible value.

            Raises:
                ValueError: If the body is not JSON.
            """
            ...


@dataclass(frozen=True, slots=True)
class TokenOperationCase:
    """Describe one body-carried JWT endpoint contract.

    Groups the route, field, and implementation seam that vary together so parametrized tests keep
    one domain argument instead of a positional data clump.

    Attributes:
        route: Versioned endpoint path.
        field: Request field carrying the credential.
        helper_name: View helper replaced to induce failures.

    Members:
        None.
    """

    route: str
    field: str
    helper_name: str


@dataclass(frozen=True, slots=True)
class MalformedTemporalClaimCase:
    """Describe one signed non-scalar temporal-claim rejection.

    Groups the public boundary, temporal claim, JSON shape, and response leak markers so each
    regression case remains one domain argument.

    Attributes:
        boundary: Public token operation receiving the credential.
        claim: Temporal claim replaced with a non-scalar value.
        value: JSON-compatible non-scalar claim value under test.
        leak_markers: Encoded claim fragments forbidden from the response.

    Members:
        None.
    """

    boundary: str
    claim: str
    value: object
    leak_markers: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class InvalidNumericDateCase:
    """Describe one signed NumericDate conversion rejection.

    Groups the public boundary, temporal claim, numeric shape, and response leak marker so
    platform-specific timestamp conversion failures remain observable only as authentication.

    Attributes:
        boundary: Public token operation receiving the credential.
        claim: Temporal claim replaced with an invalid NumericDate value.
        value: Finite out-of-range or non-finite numeric value under test.
        leak_marker: Encoded claim value forbidden from the response.

    Members:
        None.
    """

    boundary: str
    claim: str
    value: int | float
    leak_marker: bytes


PASSWORD = secrets.token_urlsafe(24)
TIMING_WARMUP_REQUESTS = 5
TIMING_MEASURED_REQUESTS = 30
TIMING_RELATIVE_LIMIT = 0.20
TIMING_ABSOLUTE_LIMIT_SECONDS = 0.010
CONCURRENT_TIMING_BATCH_SIZE = 4
CONCURRENT_TIMING_WARMUP_BATCHES = 1
CONCURRENT_TIMING_MEASURED_BATCHES = 7
TOKEN_LIFETIME_CLOCK_TOLERANCE_SECONDS = 1
SUPPORTED_HASHER_ALGORITHMS = tuple(hasher.algorithm for hasher in get_hashers())
timing_logger = logging.getLogger("localforge.tests.jwt_timing")
LOW_ADDRESS_RATE = "1/minute"
HIGH_LOGIN_RATE = "1000/minute"
RACE_WAIT_SECONDS = 45
pytestmark = [
    pytest.mark.api_runtime,
    pytest.mark.xdist_group(name="jwt-authentication"),
]


class RejectUnpinnedJWTReadRouter:
    """Reject any JWT state read that does not select a database.

    Inherits nothing and raises when Django asks where account or revocation state belongs, allowing
    tests to prove project adapters bypass routing for authoritative reads.

    Attributes:
        None.

    Members:
        db_for_read: Reject unpinned account and revocation reads.
    """

    def db_for_read(self, model: type, **hints: object) -> str | None:
        """Reject an unpinned JWT state read.

        Leaves every other model undecided and fails immediately if authentication or rotation
        consults the router instead of selecting the primary alias.

        Arguments:
            model: Model Django is routing.
            **hints: Routing metadata supplied by Django.

        Returns:
            No routing opinion for models outside JWT state.

        Raises:
            AssertionError: If an account or revocation read reaches the router.
        """
        del hints

        if model in {User, OutstandingToken, BlacklistedToken}:
            message = "JWT state lookup did not select the primary"
            raise AssertionError(message)

        return None


class JWTProtectedProbeView(APIView):
    """Expose a test-only route using central authentication policy.

    Inherits from DRF's ``APIView`` without local authentication declarations so the request
    proves the globally primary JWT scheme admits an active account.

    Attributes:
        None beyond those inherited from ``APIView``.

    Members:
        get: Return success after authentication resolves the caller.
    """

    def get(self, _request: object) -> Response:
        """Return success after central authentication admits the caller.

        Provides no body because only the authentication result and database route are under test.
        Makes a failed authentication observable before the method can return.

        Arguments:
            _request: Authenticated REST request.

        Returns:
            Empty successful response.
        """
        return Response(status=HTTPStatus.NO_CONTENT)


urlpatterns = [
    path("api/v1/", include("accounts.urls")),
    path("api/v1/jwt-protected-probe/", JWTProtectedProbeView.as_view()),
]


def _post_credentials(
    client: Client,
    username: str,
    password: str,
    *,
    remote_address: str,
) -> ClientResponse:
    """Post one credential exchange through the public JWT create endpoint.

    Keeps equivalence and timing requests on one wire representation while assigning a client
    address that cannot inherit admission state from another integration test.

    Arguments:
        client: Django test client supplied by the framework.
        username: Submitted account username.
        password: Submitted raw password.
        remote_address: Client address exposed to login admission.

    Returns:
        Django test response from the JWT create route.
    """
    return cast(
        "ClientResponse",
        client.post(
            "/api/v1/jwt/create/",
            {"username": username, "password": password},
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        ),
    )


def _unlimited_login_settings() -> dict[str, str]:
    """Build login rates above every request count in this module.

    Keeps security tests on credential verification rather than admission rejection while retaining
    the real shared throttle implementation.

    Arguments:
        None.

    Returns:
        High address and account rates.
    """
    return {
        "TOKEN_LOGIN_ADDRESS_THROTTLE_RATE": HIGH_LOGIN_RATE,
        "TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE": HIGH_LOGIN_RATE,
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_active_account_can_create_access_and_refresh_tokens(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Issue both JWT credentials to an active account.

    Posts valid credentials through the public versioned route and verifies the response exposes
    only the access and refresh values required by the protocol.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If issuance fails or returns another response shape.
    """
    username = f"jwt-create-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    response = client.post(
        "/api/v1/jwt/create/",
        {"username": username, "password": PASSWORD},
        content_type="application/json",
    )
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.OK
    assert set(payload) == {"access", "refresh"}
    assert all(isinstance(payload[name], str) for name in payload)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_jwt_create_locks_revalidated_account_before_outstanding_token(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Lock the authenticated account only inside JWT issuance.

    Captures a successful public exchange and proves the lock-free account read precedes the
    issuance transaction, whose account lock precedes the outstanding-token insert.

    Arguments:
        client: Django test client issuing JWT create.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If verification locks the account or issuance touches token state first.
    """
    username = f"jwt-create-lock-order-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    with (
        override_settings(**_unlimited_login_settings()),
        CaptureQueriesContext(connections["default"]) as captured,
    ):
        response = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    statements = [query["sql"] for query in captured.captured_queries]
    account_reads = [
        index
        for index, statement in enumerate(statements)
        if '"accounts_user"' in statement and "SELECT" in statement
    ]
    account_lock = next(index for index in account_reads if "FOR UPDATE" in statements[index])
    outstanding_insert = next(
        index
        for index, statement in enumerate(statements)
        if '"token_blacklist_outstandingtoken"' in statement and "INSERT" in statement
    )

    assert response.status_code == HTTPStatus.OK
    assert any("FOR UPDATE" not in statements[index] for index in account_reads[:account_lock])
    assert account_lock < outstanding_insert


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_refresh_rotates_blacklists_and_verify_discloses_no_claims(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Rotate a refresh token and reject replay without exposing claims.

    Creates a pair, inspects only the protocol claim names and configured lifetimes, exchanges the
    refresh token, and proves both replay and verification of the blacklisted value fail.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If claims drift, rotation fails, replay succeeds, or verify exposes data.
    """
    username = f"jwt-rotate-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    created = client.post(
        "/api/v1/jwt/create/",
        {"username": username, "password": PASSWORD},
        content_type="application/json",
    )
    pair = cast("dict[str, str]", created.json())
    access = AccessToken(cast("Any", pair["access"]))
    refresh = RefreshToken(cast("Any", pair["refresh"]))

    rotated = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": pair["refresh"]},
        content_type="application/json",
    )
    rotated_pair = cast("dict[str, str]", rotated.json())
    verified = client.post(
        "/api/v1/jwt/verify/",
        {"token": rotated_pair["access"]},
        content_type="application/json",
    )
    replayed = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": pair["refresh"]},
        content_type="application/json",
    )
    blacklisted = client.post(
        "/api/v1/jwt/verify/",
        {"token": pair["refresh"]},
        content_type="application/json",
    )

    assert set(access.payload) == {
        "token_type",
        "exp",
        "iat",
        "jti",
        "user_id",
        "hash_password",
    }
    assert set(refresh.payload) == {
        "token_type",
        "exp",
        "iat",
        "jti",
        "user_id",
        "hash_password",
    }
    assert access["user_id"] == refresh["user_id"] == str(account.pk)
    assert (
        abs(access["exp"] - access["iat"] - settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS)
        <= TOKEN_LIFETIME_CLOCK_TOLERANCE_SECONDS
    )
    assert (
        abs(refresh["exp"] - refresh["iat"] - settings.JWT_REFRESH_TOKEN_LIFETIME_SECONDS)
        <= TOKEN_LIFETIME_CLOCK_TOLERANCE_SECONDS
    )
    assert rotated.status_code == HTTPStatus.OK
    assert set(rotated_pair) == {"access", "refresh"}
    assert rotated_pair["refresh"] != pair["refresh"]
    assert verified.status_code == HTTPStatus.OK
    assert verified.json() == {}
    assert replayed.status_code == blacklisted.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", replayed.json())["code"] == ErrorCode.AUTHENTICATION_FAILED
    assert cast("dict[str, Any]", blacklisted.json())["code"] == ErrorCode.AUTHENTICATION_FAILED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    ROOT_URLCONF=__name__,
    DATABASE_ROUTERS=[RejectUnpinnedJWTReadRouter()],
)
def test_jwt_authentication_reads_immediate_account_state_from_primary(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Authenticate access tokens only while their primary account remains active.

    Uses a router that rejects unpinned reads, then deactivates and deletes the account between
    requests, proving every authorization decision observes current primary state.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If authentication uses the replica or accepts stale account state.
    """
    username = f"jwt-authenticate-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    refresh = RefreshToken.for_user(account)
    authorization = {"authorization": f"Bearer {refresh.access_token}"}

    authenticated = JWTAuthentication().authenticate(
        Request(
            RequestFactory().get(
                "/api/v1/jwt-protected-probe/",
                HTTP_AUTHORIZATION=authorization["authorization"],
            )
        )
    )

    active = client.get("/api/v1/jwt-protected-probe/", headers=authorization)

    account.is_active = False
    account.save(using="default", update_fields=["is_active"])
    inactive = client.get("/api/v1/jwt-protected-probe/", headers=authorization)
    account.delete(using="default")
    deleted = client.get("/api/v1/jwt-protected-probe/", headers=authorization)

    assert authenticated is not None
    assert active.status_code == HTTPStatus.NO_CONTENT
    assert inactive.status_code == deleted.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", inactive.json())["code"] == ErrorCode.AUTHENTICATION_FAILED
    assert cast("dict[str, Any]", deleted.json())["code"] == ErrorCode.AUTHENTICATION_FAILED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(ROOT_URLCONF=__name__)
def test_jwt_authentication_maps_primary_account_outage_to_service_unavailable(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the correlated service envelope when bearer lookup loses the primary.

    Presents a valid access token through a protected HTTP route, then fails only the
    authenticator's mutable-account read so dependency loss remains distinct from invalid
    credentials.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing primary account lookup failure.

    Returns:
        None.

    Raises:
        AssertionError: If database loss becomes unauthorized or an internal error.
    """
    username = f"jwt-auth-outage-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    access = str(RefreshToken.for_user(account).access_token)

    def fail_account_lookup(
        _authentication: JWTAuthentication,
        _token: object,
    ) -> None:
        """Raise the primary database failure under test.

        Replaces only mutable-account resolution after token validation has succeeded. Leaves
        bearer parsing and signature verification on their production path.

        Arguments:
            _authentication: Project authenticator resolving the request.
            _token: Validated access token carrying the account identity.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary database loss.
        """
        raise DatabaseError

    monkeypatch.setattr(JWTAuthentication, "get_user", fail_account_lookup)
    client.raise_request_exception = False
    response = client.get(
        "/api/v1/jwt-protected-probe/",
        headers={"authorization": f"Bearer {access}"},
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json()) == {
        "code": ErrorCode.SERVICE_UNAVAILABLE,
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(DATABASE_ROUTERS=[RejectUnpinnedJWTReadRouter()])
def test_refresh_and_verify_bypass_replica_routing(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Read account and revocation state only from the primary.

    Installs a router that rejects every unpinned JWT-state read, then creates, verifies, and
    rotates credentials through HTTP, proving replica lag cannot admit stale state.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If any JWT state read reaches ordinary routing.
    """
    username = f"jwt-primary-state-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    created = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    pair = cast("dict[str, str]", created.json())
    verified = client.post(
        "/api/v1/jwt/verify/",
        {"token": pair["access"]},
        content_type="application/json",
    )
    rotated = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": pair["refresh"]},
        content_type="application/json",
    )

    assert created.status_code == verified.status_code == rotated.status_code == HTTPStatus.OK


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_concurrent_refresh_replay_has_exactly_one_winner(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Serialize concurrent use of one refresh credential.

    Releases two independent HTTP clients together and proves atomic outstanding-token locking
    permits one rotation while the other observes the committed blacklist entry.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If replay produces zero or multiple successful rotations.
    """
    username = f"jwt-concurrent-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    created = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    refresh = cast("dict[str, str]", created.json())["refresh"]
    barrier = Barrier(2)

    def rotate() -> int:
        """Rotate the shared credential from one independent client.

        Waits until both callers are ready, then returns only the public status needed to grade
        atomic replay prevention.

        Arguments:
            None.

        Returns:
            HTTP status from the refresh endpoint.
        """
        request_client = DjangoClient()
        barrier.wait()
        response = request_client.post(
            "/api/v1/jwt/refresh/",
            {"refresh": refresh},
            content_type="application/json",
        )

        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _index: rotate(), range(2)))

    assert statuses == [HTTPStatus.OK, HTTPStatus.UNAUTHORIZED]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_refresh_locks_account_before_outstanding_token(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Acquire the account row before the refresh credential row.

    Captures one successful rotation and verifies its two row locks follow the same account-first
    order as password replacement, preventing opposite-order deadlocks.

    Arguments:
        client: Django test client issuing JWT requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If rotation locks outstanding state before its account.
    """
    username = f"jwt-lock-order-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    refresh = jwt_authentication_module.PrimaryRefreshToken.for_user(account)

    with CaptureQueriesContext(connections["default"]) as captured:
        response = client.post(
            "/api/v1/jwt/refresh/",
            {"refresh": str(refresh)},
            content_type="application/json",
        )

    lock_statements = [
        query["sql"] for query in captured.captured_queries if "FOR UPDATE" in query["sql"]
    ]

    assert response.status_code == HTTPStatus.OK
    assert '"accounts_user"' in lock_statements[0]
    assert '"token_blacklist_outstandingtoken"' in lock_statements[1]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(90)
def test_concurrent_refresh_and_password_change_leave_no_surviving_old_identity(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotate and replace a password without deadlock or surviving credentials.

    Pauses refresh after its account-then-outstanding lock sequence, starts password replacement on
    another connection, then resumes both operations and proves account-first locking lets rotation
    finish before replacement revokes both the original and replacement credentials.

    Arguments:
        django_user_model: Configured custom account model.
        monkeypatch: Fixture pausing the outstanding-token lock boundary.

    Returns:
        None.

    Raises:
        AssertionError: If opposite lock order deadlocks or leaves a credential usable.
    """
    current_password = "Concurrent-Refresh-Password-33"  # noqa: S105
    new_password = "Concurrent-Refresh-Password-34"  # noqa: S105
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"jwt-password-race-{suffix}",
        f"jwt-password-race-{suffix}@localforge.invalid",
        current_password,
        is_active=True,
    )
    authorization = DRFToken.objects.using("default").create(user=account)
    original_refresh = jwt_authentication_module.PrimaryRefreshToken.for_user(account)
    outstanding_locked = Event()
    release_refresh = Event()
    original_get = QuerySet.get

    def pause_outstanding_lock(queryset: QuerySet[Any], *args: object, **kwargs: object) -> object:
        """Pause after refresh has locked the account and refresh credential rows.

        Preserves every other queryset lookup and holds the selected rows while the competing
        password transaction waits for the account lock before credential revocation.

        Arguments:
            queryset: Model queryset performing the lookup.
            *args: Positional lookup arguments.
            **kwargs: Keyword lookup arguments.

        Returns:
            Real selected model instance.

        Raises:
            AssertionError: If the test does not release rotation.
        """
        result = original_get(queryset, *args, **kwargs)
        if queryset.model is OutstandingToken and queryset.query.select_for_update:
            outstanding_locked.set()
            assert release_refresh.wait(timeout=RACE_WAIT_SECONDS)
        return result

    monkeypatch.setattr(QuerySet, "get", pause_outstanding_lock)

    def refresh() -> tuple[int, dict[str, str]]:
        """Rotate the original refresh credential on an independent connection.

        Returns the public status and replacement pair after the lock pause resumes.
        Keeps thread-local database state isolated from the competing password request.

        Arguments:
            None.

        Returns:
            Rotation status and decoded body.
        """
        connections.close_all()
        try:
            response = DjangoClient().post(
                "/api/v1/jwt/refresh/",
                {"refresh": str(original_refresh)},
                content_type="application/json",
            )
            return response.status_code, cast("dict[str, str]", response.json())
        finally:
            connections.close_all()

    def change_password() -> int:
        """Replace the password on an independent connection.

        Uses the public authenticated route so account locking and complete credential revocation
        match production behavior.

        Arguments:
            None.

        Returns:
            Password-change response status.
        """
        connections.close_all()
        try:
            response = DjangoClient().post(
                "/api/v1/users/set_password/",
                {
                    "current_password": current_password,
                    "new_password": new_password,
                    "new_password_confirm": new_password,
                },
                content_type="application/json",
                headers={"authorization": f"Token {authorization.key}"},
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        refresh_future = executor.submit(refresh)
        assert outstanding_locked.wait(timeout=RACE_WAIT_SECONDS)
        password_future = executor.submit(change_password)
        time.sleep(0.5)
        release_refresh.set()
        refresh_status, pair = refresh_future.result(timeout=RACE_WAIT_SECONDS)
        password_status = password_future.result(timeout=RACE_WAIT_SECONDS)

    replacement_probe = DjangoClient().post(
        "/api/v1/jwt/refresh/",
        {"refresh": pair.get("refresh", "")},
        content_type="application/json",
    )
    access_probe = DjangoClient().post(
        "/api/v1/jwt/verify/",
        {"token": pair.get("access", "")},
        content_type="application/json",
    )

    assert refresh_status == HTTPStatus.OK
    assert password_status == HTTPStatus.NO_CONTENT
    assert replacement_probe.status_code == HTTPStatus.UNAUTHORIZED
    assert access_probe.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_jwt_create_rejected_account_states_are_wire_equivalent(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return one credential failure for wrong, unknown, and inactive accounts.

    Fixes correlation randomness and compares raw response bytes and status, proving the JWT create
    boundary does not disclose whether the submitted account exists or is inactive.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture controlling request identifiers.

    Returns:
        None.

    Raises:
        AssertionError: If a rejected account state changes the public response.
    """
    suffix = uuid.uuid4().hex
    active_username = f"jwt-wrong-{suffix}"
    inactive_username = f"jwt-inactive-{suffix}"
    django_user_model.objects.create_user(
        active_username,
        f"{active_username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    django_user_model.objects.create_user(
        inactive_username,
        f"{inactive_username}@localforge.invalid",
        PASSWORD,
    )
    request_identifier = uuid.uuid4()
    monkeypatch.setattr("config.logs.uuid.uuid4", lambda: request_identifier)

    with override_settings(**_unlimited_login_settings()):
        wrong = _post_credentials(
            client,
            active_username,
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        unknown = _post_credentials(
            client,
            f"jwt-unknown-{suffix}",
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        inactive = _post_credentials(
            client,
            inactive_username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    assert (
        wrong.status_code == unknown.status_code == inactive.status_code == HTTPStatus.UNAUTHORIZED
    )
    assert wrong.content == unknown.content == inactive.content
    assert wrong.headers[REQUEST_ID_HEADER] == str(request_identifier)
    assert (
        wrong.content
        == json.dumps(
            {
                "code": ErrorCode.AUTHENTICATION_FAILED,
                "message": "Authentication failed.",
                "details": {},
                "request_id": str(request_identifier),
            },
            separators=(",", ":"),
        ).encode()
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("hasher_algorithm", SUPPORTED_HASHER_ALGORITHMS)
def test_jwt_create_wrong_and_unknown_credentials_perform_equivalent_hash_work(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    hasher_algorithm: str,
) -> None:
    """Perform the same complete password schedule for JWT credential failures.

    Records the public create boundary's encoded verification inputs and proves a wrong password
    and unknown username execute every configured hasher with equivalent work metadata.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture observing password verification.
        hasher_algorithm: Algorithm used by the known account.

    Returns:
        None.

    Raises:
        AssertionError: If either path changes the verification schedule.
    """
    username = f"jwt-hash-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    account.password = make_password(PASSWORD, hasher=hasher_algorithm)
    account.save(using="default", update_fields=["password"])
    encoded_hashes: list[str] = []
    original_verify = token_authentication_module.verify_encoded_password

    def observe_password(
        raw_password: str,
        encoded: str,
        *,
        preferred: str = "default",
    ) -> bool:
        """Record and execute one password verification.

        Preserves the real hasher work while exposing only encoded metadata needed to compare the
        known and unknown schedules.

        Arguments:
            raw_password: Submitted password.
            encoded: Stored or dummy encoded password.
            preferred: Hasher defining runtime-hardening parameters.

        Returns:
            Whether the submitted password matches.
        """
        encoded_hashes.append(encoded)

        return original_verify(raw_password, encoded, preferred=preferred)

    monkeypatch.setattr(token_authentication_module, "verify_encoded_password", observe_password)

    with override_settings(**_unlimited_login_settings()):
        wrong = _post_credentials(
            client,
            username,
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        unknown = _post_credentials(
            client,
            f"jwt-unknown-hash-{uuid.uuid4().hex}",
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    schedule_length = len(SUPPORTED_HASHER_ALGORITHMS)
    wrong_schedule = encoded_hashes[:schedule_length]
    unknown_schedule = encoded_hashes[schedule_length:]

    assert wrong.status_code == unknown.status_code == HTTPStatus.UNAUTHORIZED
    assert len(wrong_schedule) == len(unknown_schedule) == schedule_length
    assert [identify_hasher(value).algorithm for value in wrong_schedule] == list(
        SUPPORTED_HASHER_ALGORITHMS
    )
    assert [
        {
            name: value
            for name, value in identify_hasher(encoded).safe_summary(encoded).items()
            if name not in {"salt", "hash"}
        }
        for encoded in wrong_schedule
    ] == [
        {
            name: value
            for name, value in identify_hasher(encoded).safe_summary(encoded).items()
            if name not in {"salt", "hash"}
        }
        for encoded in unknown_schedule
    ]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_jwt_create_requires_reset_for_nonaccepted_password_profiles(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject a stronger unapproved password profile without issuing credentials.

    Stores a positive PBKDF2 profile above the configured work factor and submits its correct
    password, proving JWT create follows the bounded reset-required policy.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the nonaccepted profile authenticates or is mutated.
    """
    username = f"jwt-reset-required-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    hasher = cast(
        "PBKDF2PasswordHasher",
        next(value for value in get_hashers() if value.algorithm == "pbkdf2_sha256"),
    )
    original_hash = hasher.encode(
        PASSWORD,
        hasher.salt(),
        iterations=hasher.iterations + 1,
    )
    account.password = original_hash
    account.save(using="default", update_fields=["password"])

    with override_settings(**_unlimited_login_settings()):
        response = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert account.password == original_hash
    assert not OutstandingToken.objects.using("default").filter(user=account).exists()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_expired_and_malformed_tokens_return_correlated_unauthorized_envelopes(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject expired and malformed credentials uniformly.

    Freezes issuance time, advances beyond each configured lifetime, and compares refresh and
    verify failures with malformed input through the public HTTP boundaries.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If an invalid credential escapes the unauthorized envelope.
    """
    username = f"jwt-expiry-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    issued_at = timezone.now()
    with freeze_time(issued_at):
        refresh = RefreshToken.for_user(account)
        access_value = str(refresh.access_token)
        refresh_value = str(refresh)

    with freeze_time(issued_at + timedelta(seconds=settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS + 1)):
        expired_access = client.post(
            "/api/v1/jwt/verify/",
            {"token": access_value},
            content_type="application/json",
        )

    with freeze_time(
        issued_at + timedelta(seconds=settings.JWT_REFRESH_TOKEN_LIFETIME_SECONDS + 1)
    ):
        expired_refresh = client.post(
            "/api/v1/jwt/refresh/",
            {"refresh": refresh_value},
            content_type="application/json",
        )

    malformed_verify = client.post(
        "/api/v1/jwt/verify/",
        {"token": "not-a-json-web-token"},
        content_type="application/json",
    )
    malformed_refresh = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": "not-a-json-web-token"},
        content_type="application/json",
    )

    for response in (
        expired_access,
        expired_refresh,
        malformed_verify,
        malformed_refresh,
    ):
        payload = cast("dict[str, Any]", response.json())

        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert payload == {
            "code": ErrorCode.AUTHENTICATION_FAILED,
            "message": "Authentication failed.",
            "details": {},
            "request_id": response.headers[REQUEST_ID_HEADER],
        }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(ROOT_URLCONF=__name__)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            MalformedTemporalClaimCase("refresh", "exp", None, (b"null",)),
            id="refresh-null-exp",
        ),
        pytest.param(
            MalformedTemporalClaimCase(
                "verify",
                "iat",
                ["signed-iat-list-value"],
                (b"signed-iat-list-value",),
            ),
            id="verify-list-iat",
        ),
        pytest.param(
            MalformedTemporalClaimCase(
                "authenticate",
                "exp",
                {"signed-exp-mapping-key": "signed-exp-mapping-value"},
                (b"signed-exp-mapping-key", b"signed-exp-mapping-value"),
            ),
            id="authenticate-mapping-exp",
        ),
    ],
)
def test_signed_tokens_with_non_scalar_temporal_claims_return_unauthorized(
    client: Client,
    django_user_model: type[User],
    case: MalformedTemporalClaimCase,
) -> None:
    """Reject non-scalar temporal claims at every public token boundary.

    Signs representative null, list, and mapping values across expiration and issued-at claims,
    then presents them to refresh, verify, and protected bearer authentication through HTTP.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        case: Public boundary, malformed claim, and forbidden response fragments.

    Returns:
        None.

    Raises:
        AssertionError: If decoding raises internally or exposes the rejected claim value.
    """
    username = f"jwt-temporal-claim-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    token: AccessToken | jwt_authentication_module.PrimaryRefreshToken
    if case.boundary == "refresh":
        token = jwt_authentication_module.PrimaryRefreshToken.for_user(account)
    else:
        token = AccessToken()
        token["user_id"] = str(account.pk)
    token[case.claim] = case.value
    encoded = str(token)
    client.raise_request_exception = False

    if case.boundary == "refresh":
        response = client.post(
            "/api/v1/jwt/refresh/",
            {"refresh": encoded},
            content_type="application/json",
        )
    elif case.boundary == "verify":
        response = client.post(
            "/api/v1/jwt/verify/",
            {"token": encoded},
            content_type="application/json",
        )
    else:
        response = client.get(
            "/api/v1/jwt-protected-probe/",
            headers={"authorization": f"Bearer {encoded}"},
        )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", response.json()) == {
        "code": ErrorCode.AUTHENTICATION_FAILED,
        "message": "Authentication failed.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }
    assert all(marker not in response.content for marker in case.leak_markers)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(ROOT_URLCONF=__name__)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            InvalidNumericDateCase("refresh", "exp", 10**100, str(10**100).encode()),
            id="refresh-finite-exp-overflow",
        ),
        pytest.param(
            InvalidNumericDateCase("verify", "exp", 10**100, str(10**100).encode()),
            id="verify-finite-exp-overflow",
        ),
        pytest.param(
            InvalidNumericDateCase("authenticate", "exp", 10**100, str(10**100).encode()),
            id="authenticate-finite-exp-overflow",
        ),
        pytest.param(
            InvalidNumericDateCase("verify", "exp", float("inf"), b"Infinity"),
            id="verify-infinite-exp",
        ),
        pytest.param(
            InvalidNumericDateCase("verify", "exp", float("-inf"), b"-Infinity"),
            id="verify-negative-infinite-exp",
        ),
        pytest.param(
            InvalidNumericDateCase("verify", "exp", float("nan"), b"NaN"),
            id="verify-not-a-number-exp",
        ),
        pytest.param(
            InvalidNumericDateCase("refresh", "iat", float("inf"), b"Infinity"),
            id="refresh-infinite-iat",
        ),
        pytest.param(
            InvalidNumericDateCase("authenticate", "nbf", float("-inf"), b"-Infinity"),
            id="authenticate-negative-infinite-nbf",
        ),
    ],
)
def test_signed_tokens_with_invalid_numeric_dates_return_unauthorized(
    client: Client,
    django_user_model: type[User],
    case: InvalidNumericDateCase,
) -> None:
    """Reject overflowing and non-finite NumericDates at public token boundaries.

    Exercises finite timestamp conversion beyond the platform range at refresh, verification, and
    bearer authentication, plus every PyJWT temporal integer conversion branch using non-finite
    expiration, issued-at, and not-before values.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        case: Public boundary, temporal claim, numeric value, and forbidden response fragment.

    Returns:
        None.

    Raises:
        AssertionError: If conversion raises internally or exposes the rejected claim value.
    """
    username = f"jwt-numeric-date-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    token: AccessToken | jwt_authentication_module.PrimaryRefreshToken
    if case.boundary == "refresh":
        token = jwt_authentication_module.PrimaryRefreshToken.for_user(account)
    else:
        token = AccessToken()
        token["user_id"] = str(account.pk)
    token[case.claim] = case.value
    encoded = str(token)
    client.raise_request_exception = False

    if case.boundary == "refresh":
        response = client.post(
            "/api/v1/jwt/refresh/",
            {"refresh": encoded},
            content_type="application/json",
        )
    elif case.boundary == "verify":
        response = client.post(
            "/api/v1/jwt/verify/",
            {"token": encoded},
            content_type="application/json",
        )
    else:
        response = client.get(
            "/api/v1/jwt-protected-probe/",
            headers={"authorization": f"Bearer {encoded}"},
        )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", response.json()) == {
        "code": ErrorCode.AUTHENTICATION_FAILED,
        "message": "Authentication failed.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }
    assert case.leak_marker not in response.content


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(ROOT_URLCONF=__name__)
def test_signed_non_uuid_user_id_claims_return_correlated_unauthorized_envelopes(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject signed non-UUID identities at every public token boundary.

    Presents a validly signed access token to verification and bearer authentication, then a
    refresh token whose existing outstanding identifier still matches, proving identity parsing
    fails uniformly before Django field coercion and never exposes the rejected claim.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If any boundary returns an internal error or exposes the invalid identity.
    """
    username = f"jwt-non-uuid-identity-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    rejected_identity = "signed-but-not-a-uuid"
    access = AccessToken()
    access["user_id"] = rejected_identity
    refresh = jwt_authentication_module.PrimaryRefreshToken.for_user(account)
    refresh["user_id"] = rejected_identity
    encoded_access = str(access)
    client.raise_request_exception = False

    verified = client.post(
        "/api/v1/jwt/verify/",
        {"token": encoded_access},
        content_type="application/json",
    )
    rotated = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": str(refresh)},
        content_type="application/json",
    )
    authenticated = client.get(
        "/api/v1/jwt-protected-probe/",
        headers={"authorization": f"Bearer {encoded_access}"},
    )

    for response in (verified, rotated, authenticated):
        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert cast("dict[str, Any]", response.json()) == {
            "code": ErrorCode.AUTHENTICATION_FAILED,
            "message": "Authentication failed.",
            "details": {},
            "request_id": response.headers[REQUEST_ID_HEADER],
        }
        assert rejected_identity.encode() not in response.content


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_refresh_and_verify_reject_immediate_account_deactivation_and_deletion(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject body-carried tokens as soon as account state changes.

    Deactivates and then deletes the token owner on the primary, proving both refresh and verify
    consult current account state instead of trusting claims or a lagging replica.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If either endpoint accepts stale account state.
    """
    username = f"jwt-account-state-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    refresh = RefreshToken.for_user(account)
    access_value = str(refresh.access_token)
    refresh_value = str(refresh)
    account.is_active = False
    account.save(using="default", update_fields=["is_active"])

    inactive_refresh = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": refresh_value},
        content_type="application/json",
    )
    inactive_verify = client.post(
        "/api/v1/jwt/verify/",
        {"token": access_value},
        content_type="application/json",
    )
    account.delete(using="default")
    deleted_refresh = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": refresh_value},
        content_type="application/json",
    )
    deleted_verify = client.post(
        "/api/v1/jwt/verify/",
        {"token": access_value},
        content_type="application/json",
    )

    assert {
        response.status_code
        for response in (
            inactive_refresh,
            inactive_verify,
            deleted_refresh,
            deleted_verify,
        )
    } == {HTTPStatus.UNAUTHORIZED}


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("claim", "claim_present", "value"),
    [
        pytest.param("token_type", False, None, id="missing-token-type"),
        pytest.param("token_type", True, None, id="null-token-type"),
        pytest.param("token_type", True, [], id="list-token-type"),
        pytest.param("token_type", True, {}, id="mapping-token-type"),
        pytest.param("token_type", True, 7, id="integer-token-type"),
        pytest.param("token_type", True, 7.5, id="number-token-type"),
        pytest.param("token_type", True, True, id="boolean-token-type"),
        pytest.param("token_type", True, "", id="empty-token-type"),
        pytest.param("token_type", True, "unsupported", id="unsupported-token-type"),
        pytest.param("jti", True, 7, id="non-string-token-id"),
        pytest.param("user_id", True, 7, id="non-string-user-id"),
    ],
)
def test_verify_rejects_signed_tokens_with_invalid_protocol_claims(
    client: Client,
    claim: str,
    *,
    claim_present: bool,
    value: object,
) -> None:
    """Reject valid signatures carrying invalid protocol metadata.

    Signs an access token with one invalid claim at a time and verifies the public endpoint returns
    the same unauthorized envelope as malformed input.

    Arguments:
        client: Django test client supplied by the framework.
        claim: Protocol claim to replace.
        claim_present: Whether the invalid claim remains in the signed payload.
        value: Invalid value written into that claim.

    Returns:
        None.

    Raises:
        AssertionError: If invalid protocol metadata is accepted or exposed.
    """
    token = AccessToken()
    if claim_present:
        token[claim] = value
    else:
        del token[claim]

    response = client.post(
        "/api/v1/jwt/verify/",
        {"token": str(token)},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.AUTHENTICATION_FAILED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("claim_present", "value"),
    [
        pytest.param(False, None, id="missing"),
        pytest.param(True, None, id="null"),
        pytest.param(True, [], id="list"),
        pytest.param(True, {}, id="mapping"),
        pytest.param(True, 7, id="integer"),
        pytest.param(True, "", id="empty-string"),
    ],
)
def test_refresh_rejects_signed_tokens_without_a_valid_string_identifier(
    client: Client,
    django_user_model: type[User],
    *,
    claim_present: bool,
    value: object,
) -> None:
    """Reject signed refresh tokens whose protocol identifier is not a usable string.

    Mutates only the signed ``jti`` claim through every JSON-reachable invalid shape and proves
    blacklist checking returns the generic unauthorized contract instead of raising internally.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        claim_present: Whether the malformed claim remains in the signed payload.
        value: Invalid JSON-compatible claim value when present.

    Returns:
        None.

    Raises:
        AssertionError: If malformed protocol metadata escapes the unauthorized envelope.
    """
    username = f"jwt-refresh-jti-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    refresh = RefreshToken.for_user(account)
    if claim_present:
        refresh["jti"] = value
    else:
        del refresh["jti"]

    response = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": str(refresh)},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", response.json()) == {
        "code": ErrorCode.AUTHENTICATION_FAILED,
        "message": "Authentication failed.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_refresh_rechecks_blacklist_after_locking_the_outstanding_token(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject replay even when the token constructor's first check is stale.

    Blacklists a refresh token, suppresses the constructor's preliminary lookup, and proves the
    locked outstanding-token check independently rejects the replay.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture suppressing only the preliminary blacklist check.

    Returns:
        None.

    Raises:
        AssertionError: If the post-lock replay guard is absent.
    """
    username = f"jwt-lock-recheck-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    created = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    refresh = cast("dict[str, str]", created.json())["refresh"]
    first = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": refresh},
        content_type="application/json",
    )

    monkeypatch.setattr(
        jwt_authentication_module.PrimaryRefreshToken,
        "check_blacklist",
        lambda _token: None,
    )
    replay = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": refresh},
        content_type="application/json",
    )

    assert first.status_code == HTTPStatus.OK
    assert replay.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("route", "field"),
    [
        pytest.param("/api/v1/jwt/refresh/", "refresh", id="refresh"),
        pytest.param("/api/v1/jwt/verify/", "token", id="verify"),
    ],
)
def test_token_operations_map_primary_account_outages_to_service_unavailable(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    field: str,
) -> None:
    """Return the correlated service envelope when account state is unavailable.

    Lets token parsing and revocation reads succeed, then fails the shared primary account lookup
    so each helper's real database-error conversion crosses the HTTP boundary.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing primary account lookup failure.
        route: Refresh or verify endpoint path.
        field: Request field carrying the relevant credential.

    Returns:
        None.

    Raises:
        AssertionError: If database loss becomes unauthorized or an internal error.
    """
    username = f"jwt-account-outage-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    created = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    pair = cast("dict[str, str]", created.json())
    credential = pair["refresh"] if field == "refresh" else pair["access"]

    def fail_account_lookup(_token: object, *, for_update: bool = False) -> None:
        """Raise the primary database failure under test.

        Preserves token parsing and blacklist reads while replacing only current account-state
        resolution.

        Arguments:
            _token: Validated token whose owner would be resolved.
            for_update: Whether the caller requested a locked account read.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary database loss.
        """
        del for_update
        raise DatabaseError

    monkeypatch.setattr(jwt_authentication_module, "active_token_user", fail_account_lookup)
    response = client.post(
        route,
        {field: credential},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json()) == {
        "code": ErrorCode.SERVICE_UNAVAILABLE,
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_jwt_refresh_maps_replacement_persistence_outage_to_service_unavailable(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the correlated service envelope when rotation cannot persist its replacement.

    Issues a real refresh token, then fails only the replacement outstanding-token write inside
    rotation so the public boundary preserves the dependency-failure contract.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing replacement-token persistence failure.

    Returns:
        None.

    Raises:
        AssertionError: If database loss becomes unauthorized or an internal error.
    """
    username = f"jwt-rotation-persistence-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    with override_settings(**_unlimited_login_settings()):
        created = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
    refresh = cast("dict[str, str]", created.json())["refresh"]

    def fail_token_persistence(
        _token_type: type[jwt_authentication_module.PrimaryRefreshToken],
        _account: User,
    ) -> None:
        """Raise the primary database failure under test.

        Replaces only replacement issuance after the original credential has been validated. Leaves
        transaction, locking, and blacklist behavior on their production path.

        Arguments:
            _token_type: Refresh token class receiving the account.
            _account: Active token owner receiving the replacement.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary database loss.
        """
        raise DatabaseError

    monkeypatch.setattr(
        jwt_authentication_module.PrimaryRefreshToken,
        "for_user",
        classmethod(fail_token_persistence),
    )
    response = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": refresh},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json()) == {
        "code": ErrorCode.SERVICE_UNAVAILABLE,
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_jwt_create_maps_primary_account_outage_to_service_unavailable(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the correlated service envelope when credential lookup loses the primary.

    Lets authoritative login admission complete, then fails credential account resolution at the
    public create boundary so dependency loss cannot be misclassified as an internal failure.

    Arguments:
        client: Django test client supplied by the framework.
        monkeypatch: Fixture inducing primary account lookup failure.

    Returns:
        None.

    Raises:
        AssertionError: If database loss becomes authentication failure or an internal error.
    """

    def fail_account_lookup(_username: str, _password: str) -> None:
        """Raise the primary database failure under test.

        Replaces only credential resolution after the real shared admission decision has completed.
        Leaves request parsing and throttling on their production path.

        Arguments:
            _username: Submitted account identifier.
            _password: Submitted credential secret.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary database loss.
        """
        raise DatabaseError

    monkeypatch.setattr(
        jwt_authentication_module,
        "verify_login_credentials",
        fail_account_lookup,
    )
    client.raise_request_exception = False
    with override_settings(**_unlimited_login_settings()):
        response = _post_credentials(
            client,
            f"jwt-account-outage-{uuid.uuid4().hex}",
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json()) == {
        "code": ErrorCode.SERVICE_UNAVAILABLE,
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_jwt_create_maps_outstanding_token_outage_to_service_unavailable(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the correlated service envelope when token persistence loses the primary.

    Authenticates a real active account, then fails only the outstanding-token write so issuance
    cannot leak a database failure through the internal-error contract.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing outstanding-token persistence failure.

    Returns:
        None.

    Raises:
        AssertionError: If database loss becomes authentication failure or an internal error.
    """
    username = f"jwt-persistence-outage-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    def fail_token_persistence(
        _token_type: type[jwt_authentication_module.PrimaryRefreshToken],
        _account: User,
    ) -> None:
        """Raise the primary database failure under test.

        Replaces only refresh issuance after credential verification has succeeded. Leaves request
        parsing, throttling, and password verification on their production path.

        Arguments:
            _token_type: Refresh token class receiving the account.
            _account: Authenticated account receiving the token.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary database loss.
        """
        raise DatabaseError

    monkeypatch.setattr(
        jwt_authentication_module.PrimaryRefreshToken,
        "for_user",
        classmethod(fail_token_persistence),
    )
    client.raise_request_exception = False
    with override_settings(**_unlimited_login_settings()):
        response = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json()) == {
        "code": ErrorCode.SERVICE_UNAVAILABLE,
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": response.headers[REQUEST_ID_HEADER],
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(360)
@pytest.mark.security_timing
@pytest.mark.parametrize("hasher_algorithm", SUPPORTED_HASHER_ALGORITHMS)
def test_jwt_create_wrong_and_unknown_credentials_meet_the_timing_criterion(
    client: Client,
    django_user_model: type[User],
    hasher_algorithm: str,
) -> None:
    """Keep JWT credential failures within the approved timing bound.

    Warms both paths, alternates thirty measured requests for each, and compares medians against the
    larger of twenty percent or ten milliseconds in the dedicated timing stage.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        hasher_algorithm: Algorithm used by the known account.

    Returns:
        None.

    Raises:
        AssertionError: If median response time discloses whether the account exists.
    """
    username = f"jwt-timing-{uuid.uuid4().hex}"
    unknown_username = f"jwt-unknown-timing-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    account.password = make_password(PASSWORD, hasher=hasher_algorithm)
    account.save(using="default", update_fields=["password"])

    def measure(candidate: str) -> float:
        """Measure one rejected JWT credential exchange.

        Times the full HTTP request and verifies it reached authentication failure rather than
        another response path.

        Arguments:
            candidate: Username to submit.

        Returns:
            Elapsed request time in seconds.

        Raises:
            AssertionError: If the request returns another status.
        """
        started = time.perf_counter()
        response = _post_credentials(
            client,
            candidate,
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        elapsed = time.perf_counter() - started

        assert response.status_code == HTTPStatus.UNAUTHORIZED

        return elapsed

    with override_settings(**_unlimited_login_settings()):
        for _warmup in range(TIMING_WARMUP_REQUESTS):
            measure(username)
            measure(unknown_username)

        wrong_samples: list[float] = []
        unknown_samples: list[float] = []
        for attempt in range(TIMING_MEASURED_REQUESTS):
            first, second = (
                (username, unknown_username) if attempt % 2 == 0 else (unknown_username, username)
            )
            first_elapsed = measure(first)
            second_elapsed = measure(second)

            if first == username:
                wrong_samples.append(first_elapsed)
                unknown_samples.append(second_elapsed)
            else:
                unknown_samples.append(first_elapsed)
                wrong_samples.append(second_elapsed)

    wrong_median = statistics.median(wrong_samples)
    unknown_median = statistics.median(unknown_samples)
    median_delta = abs(wrong_median - unknown_median)
    allowed_delta = max(
        max(wrong_median, unknown_median) * TIMING_RELATIVE_LIMIT,
        TIMING_ABSOLUTE_LIMIT_SECONDS,
    )
    timing_logger.info(
        (
            "jwt credential timing measured hasher=%s wrong=%.6fs unknown=%.6fs "
            "delta=%.6fs allowed=%.6fs"
        ),
        hasher_algorithm,
        wrong_median,
        unknown_median,
        median_delta,
        allowed_delta,
    )

    assert median_delta <= allowed_delta, (
        f"wrong={wrong_median:.6f}s unknown={unknown_median:.6f}s "
        f"delta={median_delta:.6f}s allowed={allowed_delta:.6f}s"
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(180)
@pytest.mark.security_timing
def test_concurrent_wrong_and_unknown_jwt_batches_meet_the_timing_criterion(
    django_user_model: type[User],
) -> None:
    """Keep concurrent existing and unknown JWT rejection batches within the approved bound.

    Releases four same-identity requests together, alternates seven measured batches per outcome,
    and compares median wall durations using the established twenty-percent or ten-millisecond
    criterion so account row locks cannot serialize password hash work.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If an existing-account batch reveals row-lock serialization.
    """
    username = f"jwt-concurrent-timing-{uuid.uuid4().hex}"
    unknown_username = f"jwt-concurrent-unknown-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    def measure_batch(candidate: str, batch_index: int) -> float:
        """Measure one synchronized rejected JWT credential batch.

        Releases one batch through independent HTTP clients and includes their complete request
        lifetimes in the wall duration used for the public timing comparison.

        Arguments:
            candidate: Existing or unknown username shared by the batch.
            batch_index: Stable address namespace for this measured batch.

        Returns:
            Batch wall duration in seconds.

        Raises:
            AssertionError: If any request reaches another public response.
        """
        barrier = Barrier(CONCURRENT_TIMING_BATCH_SIZE)

        def reject(request_index: int) -> int:
            """Submit one synchronized rejection on a thread-local connection.

            Opens and closes thread-local database state around one public request so connection
            sharing cannot hide or introduce credential-path serialization.

            Arguments:
                request_index: Unique client-address suffix inside the batch.

            Returns:
                Public response status.
            """
            connections.close_all()
            try:
                barrier.wait()
                response = _post_credentials(
                    DjangoClient(),
                    candidate,
                    f"{PASSWORD}-wrong",
                    remote_address=f"198.19.{batch_index}.{request_index + 1}",
                )
                return response.status_code
            finally:
                connections.close_all()

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=CONCURRENT_TIMING_BATCH_SIZE) as executor:
            statuses = list(executor.map(reject, range(CONCURRENT_TIMING_BATCH_SIZE)))
        elapsed = time.perf_counter() - started
        assert statuses == [HTTPStatus.UNAUTHORIZED] * CONCURRENT_TIMING_BATCH_SIZE
        return elapsed

    with override_settings(**_unlimited_login_settings()):
        for warmup in range(CONCURRENT_TIMING_WARMUP_BATCHES):
            measure_batch(username, warmup * 2)
            measure_batch(unknown_username, warmup * 2 + 1)

        wrong_samples: list[float] = []
        unknown_samples: list[float] = []
        for attempt in range(CONCURRENT_TIMING_MEASURED_BATCHES):
            first, second = (
                (username, unknown_username) if attempt % 2 == 0 else (unknown_username, username)
            )
            first_elapsed = measure_batch(first, 10 + attempt * 2)
            second_elapsed = measure_batch(second, 11 + attempt * 2)
            if first == username:
                wrong_samples.append(first_elapsed)
                unknown_samples.append(second_elapsed)
            else:
                unknown_samples.append(first_elapsed)
                wrong_samples.append(second_elapsed)

    wrong_median = statistics.median(wrong_samples)
    unknown_median = statistics.median(unknown_samples)
    median_delta = abs(wrong_median - unknown_median)
    allowed_delta = max(
        max(wrong_median, unknown_median) * TIMING_RELATIVE_LIMIT,
        TIMING_ABSOLUTE_LIMIT_SECONDS,
    )
    timing_logger.info(
        (
            "jwt concurrent credential timing batch=%d samples=%d wrong=%.6fs "
            "unknown=%.6fs delta=%.6fs allowed=%.6fs"
        ),
        CONCURRENT_TIMING_BATCH_SIZE,
        CONCURRENT_TIMING_MEASURED_BATCHES,
        wrong_median,
        unknown_median,
        median_delta,
        allowed_delta,
    )

    assert median_delta <= allowed_delta, (
        f"batch={CONCURRENT_TIMING_BATCH_SIZE} wrong={wrong_median:.6f}s "
        f"unknown={unknown_median:.6f}s delta={median_delta:.6f}s "
        f"allowed={allowed_delta:.6f}s"
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_json_web_tokens_never_leave_their_success_response_or_request_body(
    client: Client,
    django_user_model: type[User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep JWT values out of URLs, headers, logs, and later responses.

    Captures issuance, verification, rotation, and replay while rendering every emitted record
    through the production formatter, proving credentials appear only in successful bodies.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        caplog: Fixture collecting request sequence records.

    Returns:
        None.

    Raises:
        AssertionError: If any credential escapes its permitted body.
    """
    username = f"jwt-leak-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    with caplog.at_level(logging.INFO):
        created = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        pair = cast("dict[str, str]", created.json())
        verified = client.post(
            "/api/v1/jwt/verify/",
            {"token": pair["access"]},
            content_type="application/json",
        )
        rotated = client.post(
            "/api/v1/jwt/refresh/",
            {"refresh": pair["refresh"]},
            content_type="application/json",
        )
        replayed = client.post(
            "/api/v1/jwt/refresh/",
            {"refresh": pair["refresh"]},
            content_type="application/json",
        )

    rendered_logs = "\n".join(StructuredFormatter().format(record) for record in caplog.records)
    later_channels = b"\n".join(
        [
            verified.content,
            replayed.content,
            repr(dict(verified.headers)).encode(),
            repr(dict(rotated.headers)).encode(),
            repr(dict(replayed.headers)).encode(),
        ]
    )

    assert pair["access"].encode() in created.content
    assert pair["refresh"].encode() in created.content
    assert "Location" not in created.headers
    assert pair["access"] not in rendered_logs
    assert pair["refresh"] not in rendered_logs
    assert pair["access"].encode() not in later_channels
    assert pair["refresh"].encode() not in later_channels
    assert verified.json() == {}
    assert cast("dict[str, Any]", replayed.json())["code"] == ErrorCode.AUTHENTICATION_FAILED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_simplejwt_blacklist_migrations_and_primary_records_are_present(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Apply the blacklist schema and persist issuance and revocation on the primary.

    Reads Django's migration recorder, issues and rotates a refresh token, then verifies both
    outstanding records and the blacklist entry exist through the explicit primary alias.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If migrations or primary-backed token state are absent.
    """
    applied = MigrationRecorder.Migration.objects.using("default").filter(app="token_blacklist")
    username = f"jwt-migration-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    created = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    original = cast("dict[str, str]", created.json())["refresh"]
    original_token = RefreshToken(cast("Any", original))
    rotated = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": original},
        content_type="application/json",
    )
    replacement = cast("dict[str, str]", rotated.json())["refresh"]
    replacement_token = RefreshToken(cast("Any", replacement))

    assert applied.filter(name="0013_alter_blacklistedtoken_options_and_more").exists()
    assert (
        OutstandingToken.objects.using("default")
        .filter(
            jti=original_token["jti"],
            user=account,
        )
        .exists()
    )
    assert (
        OutstandingToken.objects.using("default")
        .filter(
            jti=replacement_token["jti"],
            user=account,
        )
        .exists()
    )
    assert (
        BlacklistedToken.objects.using("default").filter(token__jti=original_token["jti"]).exists()
    )


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
@override_settings(DATABASE_ROUTERS=["config.db_router.PrimaryReplicaRouter"])
def test_flushexpiredtokens_prunes_only_expired_primary_token_state(
    django_user_model: type[User],
) -> None:
    """Prune expired outstanding and blacklisted tokens on the authoritative primary.

    Runs SimpleJWT's upstream management command under the project router, rejects any replica
    query, and proves cascading cleanup preserves every unexpired token record.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup uses the replica, retains expired state, or removes live state.
    """
    username = f"jwt-cleanup-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    now = timezone.now()
    expired = OutstandingToken.objects.using("default").create(
        user=account,
        jti=uuid.uuid4().hex,
        token=secrets.token_urlsafe(24),
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )
    unexpired = OutstandingToken.objects.using("default").create(
        user=account,
        jti=uuid.uuid4().hex,
        token=secrets.token_urlsafe(24),
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    BlacklistedToken.objects.using("default").create(token=expired)
    unexpired_blacklist = BlacklistedToken.objects.using("default").create(token=unexpired)
    primary_statements: list[str] = []

    def capture_primary(
        execute: object,
        sql: object,
        *arguments: object,
    ) -> object:
        """Record and execute one primary cleanup statement.

        Preserves the database operation while proving the management command selected the
        authoritative connection.

        Arguments:
            execute: Django database execution callback.
            sql: SQL statement sent to the primary.
            *arguments: Remaining Django execution-wrapper arguments.

        Returns:
            Database driver's statement result.

        Raises:
            TypeError: If Django supplies a non-string SQL statement.
        """
        if not isinstance(sql, str):
            message = "database execution supplied non-string SQL"
            raise TypeError(message)

        primary_statements.append(sql)

        return cast("Callable[..., object]", execute)(sql, *arguments)

    def reject_replica(
        _execute: object,
        _sql: object,
        *_arguments: object,
    ) -> None:
        """Reject every cleanup statement sent to the read replica.

        Makes incorrect routing fail at the database boundary before mirrored test connections can
        hide it.

        Arguments:
            _execute: Django database execution callback.
            _sql: SQL statement sent to the replica.
            *_arguments: Remaining Django execution-wrapper arguments.

        Returns:
            Never returns.

        Raises:
            AssertionError: Always, because cleanup is a primary responsibility.
        """
        message = "flushexpiredtokens queried the replica"
        raise AssertionError(message)

    with (
        connections["default"].execute_wrapper(capture_primary),
        connections["replica"].execute_wrapper(reject_replica),
    ):
        call_command("flushexpiredtokens", verbosity=0)

    assert primary_statements
    assert not OutstandingToken.objects.using("default").filter(pk=expired.pk).exists()
    assert not BlacklistedToken.objects.using("default").filter(token_id=expired.pk).exists()
    assert OutstandingToken.objects.using("default").filter(pk=unexpired.pk).exists()
    assert BlacklistedToken.objects.using("default").filter(pk=unexpired_blacklist.pk).exists()


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
def test_jwt_routes_document_every_reachable_response_with_examples() -> None:
    """Expose the complete JWT create, refresh, and verify contracts in OpenAPI.

    Generates the public schema and verifies each operation lists every successful, framework, and
    dependency outcome with concrete examples while errors contain no token fields.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a route, status, example, or leak constraint is missing.
    """
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    paths = cast("dict[str, Any]", schema["paths"])
    expected = {
        "/api/v1/jwt/create/": {
            "200",
            "400",
            "401",
            "405",
            "406",
            "413",
            "415",
            "429",
            "500",
            "503",
        },
        "/api/v1/jwt/refresh/": {
            "200",
            "400",
            "401",
            "405",
            "406",
            "413",
            "415",
            "429",
            "500",
            "503",
        },
        "/api/v1/jwt/verify/": {
            "200",
            "400",
            "401",
            "405",
            "406",
            "413",
            "415",
            "429",
            "500",
            "503",
        },
    }

    for route, statuses in expected.items():
        responses = cast("dict[str, Any]", paths[route]["post"]["responses"])

        assert set(responses) == statuses
        for response in responses.values():
            content = cast("dict[str, Any]", response["content"])
            examples = cast("dict[str, Any]", content["application/json"]["examples"])

            assert examples

    create_responses = cast(
        "dict[str, Any]",
        paths["/api/v1/jwt/create/"]["post"]["responses"],
    )
    refresh_responses = cast(
        "dict[str, Any]",
        paths["/api/v1/jwt/refresh/"]["post"]["responses"],
    )
    refresh_operation = cast(
        "dict[str, Any]",
        paths["/api/v1/jwt/refresh/"]["post"],
    )
    verify_responses = cast(
        "dict[str, Any]",
        paths["/api/v1/jwt/verify/"]["post"]["responses"],
    )

    assert "token-persistence" in create_responses["503"]["description"]
    assert "account is locked before outstanding-token state" in refresh_operation["description"]
    assert "invalid protocol claims" in refresh_responses["401"]["description"]
    assert "outstanding-token" in refresh_responses["503"]["description"]
    assert "invalid protocol claims" in verify_responses["401"]["description"]
    assert "blacklist" in verify_responses["503"]["description"]

    rendered_errors = json.dumps(
        {
            route: {
                status: response
                for status, response in cast(
                    "dict[str, Any]",
                    paths[route]["post"]["responses"],
                ).items()
                if status != "200"
            }
            for route in expected
        },
        sort_keys=True,
    ).lower()

    assert "<jwt>" not in rendered_errors


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_jwt_create_observed_statuses_exactly_match_its_documented_contract(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare every reachable JWT create status with OpenAPI.

    Exercises validation, authentication, routing, negotiation, request size, representation,
    throttling, dependency loss, unexpected failure, and success through the public endpoint.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing documented failure boundaries.

    Returns:
        None.

    Raises:
        AssertionError: If observed and documented statuses differ.
    """
    username = f"jwt-create-contract-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    remote_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"
    with override_settings(**_unlimited_login_settings()):
        success = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=remote_address,
        )
        invalid = client.post(
            "/api/v1/jwt/create/",
            {},
            content_type="application/json",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        unauthorized = _post_credentials(
            client,
            username,
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        method_not_allowed = client.get("/api/v1/jwt/create/")
        not_acceptable = client.post(
            "/api/v1/jwt/create/",
            {"username": username, "password": PASSWORD},
            content_type="application/json",
            headers={"accept": "text/plain"},
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
            too_large = client.post(
                "/api/v1/jwt/create/",
                data="oversized",
                content_type="text/plain",
                REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )
        unsupported = client.post(
            "/api/v1/jwt/create/",
            data="unsupported",
            content_type="text/plain",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    throttle_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"
    with override_settings(
        TOKEN_LOGIN_ADDRESS_THROTTLE_RATE=LOW_ADDRESS_RATE,
        TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE=HIGH_LOGIN_RATE,
    ):
        _post_credentials(
            client,
            f"jwt-throttle-first-{uuid.uuid4().hex}",
            PASSWORD,
            remote_address=throttle_address,
        )
        throttled = _post_credentials(
            client,
            f"jwt-throttle-second-{uuid.uuid4().hex}",
            PASSWORD,
            remote_address=throttle_address,
        )

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(
            jwt_authentication_module,
            "verify_login_credentials",
            lambda _username, _password: (_ for _ in ()).throw(RuntimeError("induced")),
        )
        client.raise_request_exception = False
        with override_settings(**_unlimited_login_settings()):
            unexpected = _post_credentials(
                client,
                username,
                PASSWORD,
                remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )

    def fail_admission(
        _store: PostgresLoginThrottleStore,
        _rules: object,
        *,
        member: str,
    ) -> None:
        """Raise the dependency failure documented by JWT create.

        Replaces only primary admission persistence while the request still crosses the public
        middleware and view boundary.

        Arguments:
            _store: Login throttle store receiving the request.
            _rules: Rolling-window rules being admitted.
            member: Opaque request member.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary database loss.
        """
        del member
        raise DatabaseError

    with monkeypatch.context() as outage_patch:
        outage_patch.setattr(PostgresLoginThrottleStore, "admit", fail_admission)
        unavailable = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    observed = {
        response.status_code
        for response in (
            success,
            invalid,
            unauthorized,
            method_not_allowed,
            not_acceptable,
            too_large,
            unsupported,
            throttled,
            unexpected,
            unavailable,
        )
    }
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])["/api/v1/jwt/create/"]["post"]["responses"],
        )
    }

    assert observed == documented


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            TokenOperationCase(
                route="/api/v1/jwt/refresh/",
                field="refresh",
                helper_name="rotate_refresh_token",
            ),
            id="refresh",
        ),
        pytest.param(
            TokenOperationCase(
                route="/api/v1/jwt/verify/",
                field="token",
                helper_name="verify_token",
            ),
            id="verify",
        ),
    ],
)
def test_jwt_token_operation_statuses_match_their_documented_contracts(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    case: TokenOperationCase,
) -> None:
    """Compare every reachable refresh and verify status with OpenAPI.

    Exercises success and every framework, credential, dependency, and unexpected failure through
    each public token-operation route.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing documented failure boundaries.
        case: Refresh or verify endpoint contract under test.

    Returns:
        None.

    Raises:
        AssertionError: If observed and documented statuses differ.
    """
    username = f"jwt-operation-contract-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    with override_settings(**_unlimited_login_settings()):
        created = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
    pair = cast("dict[str, str]", created.json())
    credential = pair["refresh"] if case.field == "refresh" else pair["access"]
    success = client.post(case.route, {case.field: credential}, content_type="application/json")
    invalid = client.post(case.route, {}, content_type="application/json")
    unauthorized = client.post(
        case.route,
        {case.field: "not-a-json-web-token"},
        content_type="application/json",
    )
    method_not_allowed = client.get(case.route)
    not_acceptable = client.post(
        case.route,
        {case.field: credential},
        content_type="application/json",
        headers={"accept": "text/plain"},
    )
    with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
        too_large = client.post(case.route, data="oversized", content_type="text/plain")
    unsupported = client.post(case.route, data="unsupported", content_type="text/plain")
    throttle_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"
    with override_settings(
        API_AUTHENTICATION_THROTTLE_RATE="1/minute",
        API_ANONYMOUS_THROTTLE_RATE="1000/minute",
    ):
        client.post(
            case.route,
            {case.field: "not-a-json-web-token"},
            content_type="application/json",
            REMOTE_ADDR=throttle_address,
        )
        throttled = client.post(
            case.route,
            {case.field: "not-a-json-web-token"},
            content_type="application/json",
            REMOTE_ADDR=throttle_address,
        )

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(
            jwt_authentication_module,
            case.helper_name,
            lambda _encoded: (_ for _ in ()).throw(RuntimeError("induced")),
        )
        client.raise_request_exception = False
        unexpected = client.post(
            case.route,
            {case.field: credential},
            content_type="application/json",
        )

    with monkeypatch.context() as outage_patch:
        outage_patch.setattr(
            jwt_authentication_module,
            case.helper_name,
            lambda _encoded: (_ for _ in ()).throw(ServiceUnavailable),
        )
        unavailable = client.post(
            case.route,
            {case.field: credential},
            content_type="application/json",
        )

    observed = {
        response.status_code
        for response in (
            success,
            invalid,
            unauthorized,
            method_not_allowed,
            not_acceptable,
            too_large,
            unsupported,
            throttled,
            unexpected,
            unavailable,
        )
    }
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])[case.route]["post"]["responses"],
        )
    }

    assert observed == documented
