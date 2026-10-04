"""Integration tests for token authentication endpoints.

Exercises the versioned login and logout HTTP boundaries against PostgreSQL and required services,
so credential decisions, token persistence, throttling, and response contracts are observed where
clients use them.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import statistics
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import timedelta
from http import HTTPStatus
from importlib import import_module
from ipaddress import ip_network
from multiprocessing import get_context
from threading import Barrier
from typing import TYPE_CHECKING, Any, cast

import pytest
from django.conf import settings
from django.contrib.auth.hashers import (
    Argon2PasswordHasher,
    BasePasswordHasher,
    PBKDF2PasswordHasher,
    PBKDF2SHA1PasswordHasher,
    ScryptPasswordHasher,
    get_hashers,
    identify_hasher,
    make_password,
)
from django.core.cache import caches
from django.db import (
    DatabaseError,
    OperationalError,
    close_old_connections,
    connection,
    connections,
)
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import QuerySet
from django.test import Client as DjangoClient
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.authtoken.models import Token

import accounts.credentials as credentials_module
import accounts.token_authentication as token_authentication_module
from accounts.credentials import PasswordHashDisposition, classify_password_hash
from accounts.login_throttle import PostgresLoginThrottleStore, RollingWindowRule
from accounts.models import LOGIN_THROTTLE_TABLE, LoginThrottleEvent
from config.api_errors import ErrorCode, ServiceUnavailable
from config.logs import REQUEST_ID_HEADER, StructuredFormatter
from tests.integration.config.throttle_worker import count_postgres_throttle_admissions

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Protocol

    from django.test import Client

    from accounts.models import User

    class ClientResponse(Protocol):
        """Describe the Django client response values these tests observe.

        Inherits from ``Protocol`` and exposes only public response state, keeping helper typing
        independent of Django's dynamic test response subtype.

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

            Parses the client response using Django's public JSON helper, matching how each test
            observes the API body without exposing its dynamic concrete response type.

            Arguments:
                None.

            Returns:
                Parsed JSON-compatible value.

            Raises:
                ValueError: If the body is not JSON.
            """
            ...


class ReplicaLagTokenRouter:
    """Model a router that would expose stale token state through the replica.

    Inherits nothing and deliberately sends every unpinned token operation to the replica, allowing
    an HTTP regression to prove issuance bypasses routing and remains on the authoritative primary.

    Attributes:
        None.

    Members:
        db_for_read: Route unpinned token reads to the replica.
        db_for_write: Route unpinned token writes to the replica.
        allow_relation: Permit account and token instances to relate across aliases.
    """

    def db_for_read(self, model: type, **hints: object) -> str | None:
        """Route an unpinned token read to stale replica state.

        Returns no opinion for other models so explicit primary account and throttle operations
        retain their production behavior.

        Arguments:
            model: Model Django is routing.
            **hints: Routing metadata supplied by Django.

        Returns:
            Replica for token reads, otherwise no routing opinion.
        """
        del hints

        return "replica" if model is Token else None

    def db_for_write(self, model: type, **hints: object) -> str | None:
        """Route an unpinned token write through the replica decision path.

        Models a hostile lag-sensitive routing decision so explicit primary binding is observable
        for both the creation path and every internal ``get_or_create`` retry.

        Arguments:
            model: Model Django is routing.
            **hints: Routing metadata supplied by Django.

        Returns:
            Replica for token writes, otherwise no routing opinion.
        """
        del hints

        return "replica" if model is Token else None

    def allow_relation(self, first: object, second: object, **hints: object) -> bool:
        """Permit relations while the test varies only query routing.

        Keeps account-token assignment reachable so captured statements, rather than Django's
        cross-alias relation safeguard, decide whether issuance is correctly pinned.

        Arguments:
            first: One related model instance.
            second: The other related model instance.
            **hints: Routing metadata supplied by Django.

        Returns:
            True for every relation.
        """
        del first, second, hints

        return True


PASSWORD = secrets.token_urlsafe(24)
TIMING_WARMUP_REQUESTS = 5
TIMING_MEASURED_REQUESTS = 30
TIMING_RELATIVE_LIMIT = 0.20
TIMING_ABSOLUTE_LIMIT_SECONDS = 0.010
CONCURRENT_TIMING_BATCH_SIZE = 4
CONCURRENT_TIMING_WARMUP_BATCHES = 1
CONCURRENT_TIMING_MEASURED_BATCHES = 7
EXPECTED_HASH_VERIFICATIONS = 2
timing_logger = logging.getLogger("localforge.tests.token_timing")
SUPPORTED_HASHER_ALGORITHMS = tuple(hasher.algorithm for hasher in get_hashers())
RECOGNIZED_LOWER_HASHERS = ("pbkdf2_sha256", "pbkdf2_sha1")
SHORT_SALT_HASHERS = RECOGNIZED_LOWER_HASHERS
RESET_REQUIRED_PROFILE_NAMES = (
    "stronger-pbkdf2",
    "mixed-argon2",
    "mixed-scrypt",
    "malformed-argon2",
    "malformed-pbkdf2",
    "malformed-scrypt",
    "unrecognized",
    "overlong",
)
STORABLE_RESET_REQUIRED_PROFILE_NAMES = RESET_REQUIRED_PROFILE_NAMES[:-1]
pytestmark = [
    pytest.mark.api_runtime,
    pytest.mark.xdist_group(name="token-authentication"),
]


def _post_credentials(
    client: Client,
    username: str,
    password: str,
    *,
    remote_address: str,
) -> ClientResponse:
    """Post one credential exchange through the public login endpoint.

    Keeps timing and equivalence tests on the same wire representation while assigning a unique
    client dimension that cannot inherit throttle state from another integration test.

    Arguments:
        client: Django test client supplied by the framework.
        username: Submitted username.
        password: Submitted raw password.
        remote_address: Client address exposed to the throttle.

    Returns:
        Django test response from the login route.

    Raises:
        None.
    """
    return cast(
        "ClientResponse",
        client.post(
            "/api/v1/token/login/",
            {"username": username, "password": password},
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        ),
    )


def _unlimited_login_settings() -> dict[str, str]:
    """Build settings that keep measurement requests below both limits.

    Raises both strict dimensions while leaving every other framework option unchanged, allowing
    timing and equivalence tests to exercise authentication rather than admission rejection.

    Arguments:
        None.

    Returns:
        Settings values with high token-login rates.
    """
    return _strict_throttle_settings(
        address_rate="1000/minute",
        account_rate="1000/minute",
    )


def _strict_throttle_settings(
    *,
    address_rate: str,
    account_rate: str,
) -> dict[str, str]:
    """Build the two strict login throttle settings.

    Names both dimensions directly so tests exercise the project throttle contract rather than
    DRF's default throttle registry, which is not authoritative for token login.

    Arguments:
        address_rate: Configured request rate for one client address.
        account_rate: Configured request rate for one account identity.

    Returns:
        Settings values for both strict login dimensions.
    """
    return {
        "TOKEN_LOGIN_ADDRESS_THROTTLE_RATE": address_rate,
        "TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE": account_rate,
    }


def _recognized_lower_password_hash(algorithm: str) -> str:
    """Build one accepted PBKDF2 hash with deliberately reduced iterations.

    Uses each PBKDF2 hasher's public encoder with a lower positive iteration count, producing a
    recognized credential Django can harden to the current configured equivalent cost.

    Arguments:
        algorithm: PBKDF2 algorithm name.

    Returns:
        Encoded password using an accepted lower-iteration profile.

    Raises:
        ValueError: If the requested algorithm is not a recognized lower profile.
    """
    if algorithm == "pbkdf2_sha256":
        pbkdf2_hasher = PBKDF2PasswordHasher()
        pbkdf2_hasher.iterations = 1000

        return pbkdf2_hasher.encode(PASSWORD, pbkdf2_hasher.salt())
    if algorithm == "pbkdf2_sha1":
        pbkdf2_sha1_hasher = PBKDF2SHA1PasswordHasher()
        pbkdf2_sha1_hasher.iterations = 1000

        return pbkdf2_sha1_hasher.encode(PASSWORD, pbkdf2_sha1_hasher.salt())
    message = f"unsupported recognized lower hasher: {algorithm}"
    raise ValueError(message)


def _reset_required_password_hash(profile_name: str) -> str:
    """Build one valid but non-accepted or malformed stored encoding.

    Produces stronger PBKDF2 and representative mixed Argon2 and Scrypt profiles without running
    their attacker-controlled verification parameters, plus malformed and unrecognized values.

    Arguments:
        profile_name: Reset-required fixture name.

    Returns:
        Stored encoding the login policy must refuse without verification.

    Raises:
        ValueError: If the requested reset-required fixture is unknown.
    """
    if profile_name == "stronger-pbkdf2":
        pbkdf2_hasher = PBKDF2PasswordHasher()

        return pbkdf2_hasher.encode(
            PASSWORD,
            pbkdf2_hasher.salt(),
            iterations=pbkdf2_hasher.iterations + 1,
        )
    if profile_name == "mixed-argon2":
        argon2_hasher = Argon2PasswordHasher()
        argon2_hasher.time_cost += 1
        argon2_hasher.memory_cost //= 2

        return argon2_hasher.encode(PASSWORD, argon2_hasher.salt())
    if profile_name == "mixed-scrypt":
        scrypt_hasher = ScryptPasswordHasher()
        current = scrypt_hasher.encode(
            PASSWORD,
            scrypt_hasher.salt(),
        )
        algorithm, _work_factor, salt, block_size, _parallelism, digest = current.split("$", 5)

        return "$".join(
            (
                algorithm,
                str(scrypt_hasher.work_factor * 2),
                salt,
                block_size,
                str(scrypt_hasher.parallelism - 1),
                digest,
            )
        )
    if profile_name.startswith("malformed-"):
        algorithm = profile_name.removeprefix("malformed-")
        hasher_types = {
            "argon2": Argon2PasswordHasher,
            "pbkdf2": PBKDF2PasswordHasher,
            "scrypt": ScryptPasswordHasher,
        }
        try:
            hasher = hasher_types[algorithm]()
        except KeyError as error:
            message = f"unsupported malformed profile: {profile_name}"
            raise ValueError(message) from error
        current = hasher.encode(PASSWORD, hasher.salt())

        return f"{current[:-1]}!"
    if profile_name == "unrecognized":
        return "retired_hasher$1$salt$digest"
    if profile_name == "overlong":
        return f"retired_hasher${'x' * 128}"

    message = f"unsupported reset-required profile: {profile_name}"
    raise ValueError(message)


def _current_cost_short_salt_password_hash(algorithm: str) -> str:
    """Build one current-cost PBKDF2 hash with obsolete salt metadata.

    Uses the configured iteration count with a deliberately short salt, isolating upgrade
    staleness from any password-verification work deficit.

    Arguments:
        algorithm: PBKDF2 algorithm name whose encoder should be used.

    Returns:
        Encoded password with current iterations and a short salt.

    Raises:
        ValueError: If the requested algorithm is not a supported PBKDF2 variant.
    """
    hasher_types = {
        "pbkdf2_sha256": PBKDF2PasswordHasher,
        "pbkdf2_sha1": PBKDF2SHA1PasswordHasher,
    }
    try:
        hasher = hasher_types[algorithm]()
    except KeyError as error:
        message = f"unsupported short-salt hasher: {algorithm}"
        raise ValueError(message) from error

    return hasher.encode(PASSWORD, "short")


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("hasher_algorithm", SUPPORTED_HASHER_ALGORITHMS)
def test_current_password_profiles_are_accepted(hasher_algorithm: str) -> None:
    """Accept each configured hasher's complete current profile.

    Classifies a freshly generated encoding for every configured algorithm, proving the bounded
    policy does not reset credentials created by the current application.

    Arguments:
        hasher_algorithm: Configured algorithm used to create the encoding.

    Returns:
        None.

    Raises:
        AssertionError: If any current configured profile is rejected.
    """
    encoded = make_password(PASSWORD, hasher=hasher_algorithm)

    assert classify_password_hash(encoded) is PasswordHashDisposition.CURRENT


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("hasher_algorithm", RECOGNIZED_LOWER_HASHERS)
def test_lower_pbkdf2_profiles_are_recognized(hasher_algorithm: str) -> None:
    """Recognize only the lower-iteration profiles Django can harden exactly.

    Classifies a positive lower iteration count for each configured PBKDF2 variant independently
    of authentication, preserving the policy boundary as an explicit regression.

    Arguments:
        hasher_algorithm: PBKDF2 algorithm used to create the encoding.

    Returns:
        None.

    Raises:
        AssertionError: If a lower PBKDF2 profile is not recognized.
    """
    encoded = _recognized_lower_password_hash(hasher_algorithm)

    assert classify_password_hash(encoded) is PasswordHashDisposition.RECOGNIZED_LOWER


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("profile_name", RESET_REQUIRED_PROFILE_NAMES)
def test_nonaccepted_password_profiles_require_reset(profile_name: str) -> None:
    """Classify stronger, mixed, malformed, and unknown encodings as reset-required.

    Exercises representative attacker-controlled metadata before any HTTP request, proving no
    component-wise comparison can admit a stronger or mixed multi-parameter profile.

    Arguments:
        profile_name: Reset-required fixture name.

    Returns:
        None.

    Raises:
        AssertionError: If any non-accepted profile reaches password verification.
    """
    encoded = _reset_required_password_hash(profile_name)

    assert classify_password_hash(encoded) is PasswordHashDisposition.RESET_REQUIRED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_active_account_can_exchange_credentials_for_a_usable_token(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Issue a DRF token to an active account.

    Posts valid credentials through the public versioned route and then presents the returned token
    to logout, proving the body value is an authentication credential rather than an opaque success
    marker.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If login fails, omits its token, or returns an unusable credential.
    """
    suffix = uuid.uuid4().hex
    username = f"token-success-{suffix}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    login = client.post(
        "/api/v1/token/login/",
        {"username": username, "password": PASSWORD},
        content_type="application/json",
        headers={"authorization": "Bearer expired-credential"},
    )
    payload = cast("dict[str, Any]", login.json())

    assert login.status_code == HTTPStatus.OK
    assert set(payload) == {"token"}
    assert isinstance(payload["token"], str)

    logout = client.post(
        "/api/v1/token/logout/",
        headers={"authorization": f"Token {payload['token']}"},
    )

    assert logout.status_code == HTTPStatus.NO_CONTENT
    assert logout.content == b""


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_login_throttles_multiple_usernames_from_a_direct_client_address(
    client: Client,
) -> None:
    """Limit multiple usernames submitted from one direct client address.

    Sends three distinct account identifiers without forwarded metadata and verifies the third
    reaches the same two-attempt bucket derived from ``REMOTE_ADDR``.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If the address can exceed its configured rate.
    """
    remote_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"

    with override_settings(
        **_strict_throttle_settings(
            address_rate="2/minute",
            account_rate="100/minute",
        )
    ):
        responses = [
            client.post(
                "/api/v1/token/login/",
                {"username": uuid.uuid4().hex, "password": PASSWORD},
                content_type="application/json",
                REMOTE_ADDR=remote_address,
            )
            for _attempt in range(3)
        ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]
    throttled = cast("dict[str, Any]", responses[-1].json())
    assert throttled == {
        "code": ErrorCode.THROTTLED,
        "message": "Too many requests.",
        "details": {},
        "request_id": responses[-1].headers[REQUEST_ID_HEADER],
    }
    assert int(responses[-1].headers["Retry-After"]) > 0


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_untrusted_forwarded_addresses_cannot_change_login_buckets(client: Client) -> None:
    """Ignore forwarded addresses supplied by a direct untrusted client.

    Varies the client-controlled header while keeping the immediate peer fixed and proves the third
    request reaches the same two-attempt address bucket.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If an untrusted forwarded header changes admission identity.
    """
    remote_address = f"198.51.100.{secrets.randbelow(200) + 1}"

    with (
        override_settings(
            TRUSTED_PROXY_NETWORKS=(),
            **_strict_throttle_settings(
                address_rate="2/minute",
                account_rate="100/minute",
            ),
        ),
    ):
        responses = [
            client.post(
                "/api/v1/token/login/",
                {"username": uuid.uuid4().hex, "password": PASSWORD},
                content_type="application/json",
                REMOTE_ADDR=remote_address,
                HTTP_X_FORWARDED_FOR=f"203.0.113.{attempt + 1}",
            )
            for attempt in range(3)
        ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_repeated_correlation_identifiers_still_count_each_login_attempt(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count every request even when correlation metadata repeats.

    Fixes the request identifier across three attempts and proves unique admission members prevent
    a repeated or client-influenced correlation value from overwriting prior rolling-window state.

    Arguments:
        client: Django test client supplied by the framework.
        monkeypatch: Fixture fixing generated request identifiers.

    Returns:
        None.

    Raises:
        AssertionError: If repeated correlation metadata bypasses the address limit.
    """
    request_identifier = uuid.uuid4()
    remote_address = "198.51.100.201"
    monkeypatch.setattr("config.logs.uuid.uuid4", lambda: request_identifier)

    with override_settings(
        TRUSTED_PROXY_NETWORKS=(),
        **_strict_throttle_settings(
            address_rate="2/minute",
            account_rate="100/minute",
        ),
    ):
        responses = [
            client.post(
                "/api/v1/token/login/",
                {"username": f"repeat-correlation-{attempt}", "password": PASSWORD},
                content_type="application/json",
                REMOTE_ADDR=remote_address,
            )
            for attempt in range(3)
        ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_trusted_proxy_uses_the_correct_forwarded_client_hop(client: Client) -> None:
    """Use the first untrusted hop behind an explicitly trusted proxy chain.

    Changes a spoofable leftmost value while retaining the proxy-appended client hop, proving three
    requests share one bucket, then changes that correct hop and proves a different client remains
    independently admissible.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If trusted forwarding chooses a client-controlled or proxy address.
    """
    trusted_peer = "10.89.2.17"
    actual_client = "198.51.100.44"

    with override_settings(
        TRUSTED_PROXY_NETWORKS=(ip_network("10.89.2.0/24"),),
        **_strict_throttle_settings(
            address_rate="2/minute",
            account_rate="100/minute",
        ),
    ):
        responses = [
            client.post(
                "/api/v1/token/login/",
                {"username": uuid.uuid4().hex, "password": PASSWORD},
                content_type="application/json",
                REMOTE_ADDR=trusted_peer,
                HTTP_X_FORWARDED_FOR=f"203.0.113.{attempt + 1}, {actual_client}",
            )
            for attempt in range(3)
        ]
        independent_client = client.post(
            "/api/v1/token/login/",
            {"username": uuid.uuid4().hex, "password": PASSWORD},
            content_type="application/json",
            REMOTE_ADDR=trusted_peer,
            HTTP_X_FORWARDED_FOR="203.0.113.99, 198.51.100.45",
        )

    assert [response.status_code for response in responses] == [
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]
    assert independent_client.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_login_throttles_one_account_across_client_addresses(client: Client) -> None:
    """Limit repeated login attempts against one account.

    Varies the client address while keeping the case-insensitive account identifier fixed and
    verifies the third attempt is rejected at a two-request rate, proving changing address cannot
    evade the account dimension.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If one account can exceed its configured rate across addresses.
    """
    username = f"token-account-limit-{uuid.uuid4().hex}"
    with override_settings(
        **_strict_throttle_settings(
            address_rate="100/minute",
            account_rate="2/minute",
        )
    ):
        responses = [
            _post_credentials(
                client,
                username.upper() if attempt % 2 else username,
                PASSWORD,
                remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )
            for attempt in range(3)
        ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_login_throttle_normalizes_username_whitespace_before_account_identity(
    client: Client,
) -> None:
    """Prevent serializer-equivalent whitespace variants from escaping one account limit.

    Submits the same unknown username with different surrounding whitespace and client addresses,
    proving default ``CharField`` trimming happens before the authoritative account bucket is
    selected.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If whitespace variants consume independent account throttle buckets.
    """
    username = f"token-whitespace-{uuid.uuid4().hex}"
    with override_settings(
        **_strict_throttle_settings(
            address_rate="100/minute",
            account_rate="2/minute",
        )
    ):
        responses = [
            _post_credentials(
                client,
                submitted,
                PASSWORD,
                remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )
            for submitted in (username, f" {username}", f"{username} ")
        ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.TOO_MANY_REQUESTS,
    ]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_login_throttles_concurrent_requests_at_the_exact_shared_limit() -> None:
    """Enforce one exact admission count under concurrent HTTP requests.

    Releases independent clients together against PostgreSQL and verifies atomic shared state admits
    exactly the configured number, exercising the race that process-local histories miss.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If concurrency admits too many or rejects too early.
    """
    request_count = 8
    admitted_count = 3
    barrier = Barrier(request_count)
    username = f"token-concurrent-{uuid.uuid4().hex}"
    remote_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"

    def attempt() -> int:
        """Send one login attempt when every concurrent caller is ready.

        Constructs a separate public client, matching independent worker request state while all
        callers share only authoritative PostgreSQL admission state.

        Arguments:
            None.

        Returns:
            Observed HTTP status.
        """
        close_old_connections()
        try:
            barrier.wait()
            response = _post_credentials(
                DjangoClient(),
                username,
                PASSWORD,
                remote_address=remote_address,
            )

            return response.status_code
        finally:
            close_old_connections()

    with (
        override_settings(
            **_strict_throttle_settings(
                address_rate=f"{admitted_count}/minute",
                account_rate="100/minute",
            )
        ),
        ThreadPoolExecutor(max_workers=request_count) as executor,
    ):
        statuses = list(executor.map(lambda _attempt: attempt(), range(request_count)))

    assert statuses.count(HTTPStatus.UNAUTHORIZED) == admitted_count
    assert statuses.count(HTTPStatus.TOO_MANY_REQUESTS) == request_count - admitted_count


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_login_throttle_shares_exact_admission_across_spawned_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforce one exact database limit across isolated application processes.

    Spawns four fresh Django runtimes and totals their public adapter decisions, proving worker
    memory and connection pools cannot create independent login histories.

    Arguments:
        monkeypatch: Fixture preventing spawned helpers from replacing pytest-cov worker data.

    Returns:
        None.

    Raises:
        AssertionError: If isolated workers admit more or fewer than the shared limit.
    """
    workers = 4
    attempts_per_worker = 6
    limit = 7
    key = f"multi-worker:{uuid.uuid4().hex}"
    database_name = str(settings.DATABASES["default"]["NAME"])
    for name in tuple(os.environ):
        if name.startswith("COV_CORE_") or name == "COVERAGE_PROCESS_START":
            monkeypatch.delenv(name)

    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=get_context("spawn"),
    ) as executor:
        futures = [
            executor.submit(
                count_postgres_throttle_admissions,
                key,
                attempts_per_worker,
                limit,
                60,
                database_name,
            )
            for _worker in range(workers)
        ]
        admitted = sum(future.result(timeout=20) for future in futures)

    assert admitted == limit


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_login_throttle_recovers_after_the_rolling_window() -> None:
    """Admit requests after the authoritative rolling window expires.

    Drives the public PostgreSQL store, proving rejection carries a database-derived retry delay
    and that expiration admits a later request without clearing state.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a throttled identity remains blocked after its window.
    """
    store = PostgresLoginThrottleStore("default")
    rule = RollingWindowRule(
        key=f"recovery:{uuid.uuid4().hex}",
        limit=2,
        window_seconds=1,
    )
    first = store.admit((rule,), member=uuid.uuid4().hex)
    second = store.admit((rule,), member=uuid.uuid4().hex)
    blocked = store.admit((rule,), member=uuid.uuid4().hex)
    time.sleep(1.05)
    recovered = store.admit((rule,), member=uuid.uuid4().hex)

    assert first.admitted is True
    assert second.admitted is True
    assert blocked.admitted is False
    assert blocked.retry_after_seconds == 1
    assert recovered.admitted is True


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_later_admission_prunes_a_bounded_oldest_batch_across_untouched_buckets() -> None:
    """Prune globally expired throttle rows without scanning one active identity.

    Seeds more expired identities than one cleanup may remove, admits an unrelated bucket, and
    verifies the deterministic oldest batch disappears while newer expired rows await later work.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup is identity-local, unbounded, or not oldest-first.
    """
    cleanup_batch_size = 64
    expired_count = cleanup_batch_size + 7
    started = timezone.now() - timedelta(days=2)
    LoginThrottleEvent.objects.using("default").bulk_create(
        [
            LoginThrottleEvent(
                bucket=f"untouched:{index}",
                request_id=f"expired:{index}",
                occurred_at=started + timedelta(microseconds=index),
            )
            for index in range(expired_count)
        ]
    )
    store = PostgresLoginThrottleStore("default")
    decision = store.admit(
        (
            RollingWindowRule(
                key=f"later:{uuid.uuid4().hex}",
                limit=1,
                window_seconds=60,
            ),
        ),
        member=uuid.uuid4().hex,
    )
    remaining = list(
        LoginThrottleEvent.objects.using("default")
        .filter(request_id__startswith="expired:")
        .order_by("occurred_at")
        .values_list("request_id", flat=True)
    )

    assert decision.admitted is True
    assert remaining == [f"expired:{index}" for index in range(cleanup_batch_size, expired_count)]


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_bucket_admission_ignores_recently_expired_rows_without_unbounded_deletion() -> None:
    """Preserve bounded request work when one revisited bucket has many expired rows.

    Seeds rows outside a minute window but inside global retention, admits the same identity, and
    verifies rolling correctness does not require deleting the entire bucket during that request.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If admission counts expired rows or deletes an unbounded bucket history.
    """
    bucket = f"recently-expired:{uuid.uuid4().hex}"
    expired_count = 200
    occurred_at = timezone.now() - timedelta(minutes=2)
    LoginThrottleEvent.objects.using("default").bulk_create(
        [
            LoginThrottleEvent(
                bucket=bucket,
                request_id=f"recently-expired:{index}",
                occurred_at=occurred_at,
            )
            for index in range(expired_count)
        ]
    )
    decision = PostgresLoginThrottleStore("default").admit(
        (
            RollingWindowRule(
                key=bucket,
                limit=1,
                window_seconds=60,
            ),
        ),
        member="current",
    )

    assert decision.admitted is True
    assert (
        LoginThrottleEvent.objects.using("default")
        .filter(bucket=bucket, request_id__startswith="recently-expired:")
        .count()
        == expired_count
    )
    assert (
        LoginThrottleEvent.objects.using("default")
        .filter(
            bucket=bucket,
            request_id="current",
        )
        .exists()
    )


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_global_cleanup_query_uses_the_occurred_at_leading_index() -> None:
    """Use the retention index for the deterministic global cleanup selection.

    Inspects the applied PostgreSQL schema and disables sequential scans while explaining the same
    ordered bounded query, proving the index begins with time and supplies its required ordering.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the migration or query loses the global retention access path.
    """
    index_name = "accounts_login_occurred_id"
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor,
            LOGIN_THROTTLE_TABLE,
        )
        cursor.execute("SET enable_seqscan = off")
    try:
        explanation = (
            LoginThrottleEvent.objects.using("default")
            .filter(occurred_at__lte=timezone.now() - timedelta(days=1))
            .order_by("occurred_at", "id")
            .values_list("id", flat=True)[:64]
            .explain()
        )
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET enable_seqscan")

    assert constraints[index_name]["columns"] == ["occurred_at", "id"]
    assert index_name in explanation


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_concurrent_admissions_serialize_bounded_global_cleanup() -> None:
    """Prune expired global state safely while unrelated buckets admit concurrently.

    Releases four stores together against four cleanup batches and verifies every caller admits,
    all expired identities disappear, and each new rolling window retains exactly one event.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup races, deadlocks, loses admissions, or exceeds its batches.
    """
    request_count = 4
    cleanup_batch_size = 64
    started = timezone.now() - timedelta(days=2)
    LoginThrottleEvent.objects.using("default").bulk_create(
        [
            LoginThrottleEvent(
                bucket=f"concurrent-expired:{index}",
                request_id=f"concurrent-expired:{index}",
                occurred_at=started + timedelta(microseconds=index),
            )
            for index in range(request_count * cleanup_batch_size)
        ]
    )
    barrier = Barrier(request_count)

    def admit(index: int) -> bool:
        """Admit one independent bucket after every cleanup caller is ready.

        Opens and closes thread-local database state around the public store operation so the test
        matches concurrent application workers without sharing connection objects.

        Arguments:
            index: Unique admission identity.

        Returns:
            Whether the request was admitted.
        """
        close_old_connections()
        try:
            barrier.wait()

            return (
                PostgresLoginThrottleStore("default")
                .admit(
                    (
                        RollingWindowRule(
                            key=f"concurrent-current:{index}",
                            limit=1,
                            window_seconds=60,
                        ),
                    ),
                    member=f"concurrent-current:{index}",
                )
                .admitted
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=request_count) as executor:
        admitted = list(executor.map(admit, range(request_count)))

    assert admitted == [True] * request_count
    assert (
        LoginThrottleEvent.objects.using("default")
        .filter(request_id__startswith="concurrent-expired:")
        .count()
        == 0
    )
    assert (
        LoginThrottleEvent.objects.using("default")
        .filter(request_id__startswith="concurrent-current:")
        .count()
        == request_count
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_default_cache_clear_cannot_reset_login_admission() -> None:
    """Keep authoritative login state outside a default-cache database flush.

    Fills one PostgreSQL rolling window, clears a scratch Valkey database through Django's default
    cache API, and proves the next admission remains blocked at the exact persisted limit.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a default-cache clear resets login admission.
    """
    configured_caches = cast("dict[str, dict[str, object]]", settings.CACHES)
    location = str(configured_caches["default"]["LOCATION"])
    scratch_caches = {
        **configured_caches,
        "default": {
            **configured_caches["default"],
            "LOCATION": f"{location.rsplit('/', maxsplit=1)[0]}/14",
            "KEY_PREFIX": f"login-clear-{uuid.uuid4().hex}",
        },
    }
    store = PostgresLoginThrottleStore("default")
    rule = RollingWindowRule(
        key=f"cache-clear:{uuid.uuid4().hex}",
        limit=2,
        window_seconds=60,
    )
    assert store.admit((rule,), member=uuid.uuid4().hex).admitted is True
    assert store.admit((rule,), member=uuid.uuid4().hex).admitted is True

    with override_settings(CACHES=scratch_caches):
        default_cache = caches["default"]
        default_cache.set("probe", "present")
        default_cache.clear()
        assert default_cache.get("probe") is None

    blocked = store.admit((rule,), member=uuid.uuid4().hex)

    assert blocked.admitted is False


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_general_cache_memory_eviction_cannot_reset_login_admission() -> None:
    """Keep authoritative login state outside general-cache memory eviction.

    Forces Django's least-recently-used local cache to evict an entry at a one-entry ceiling and
    proves the independently persisted PostgreSQL rolling window remains full.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If general-cache memory pressure resets login admission.
    """
    store = PostgresLoginThrottleStore("default")
    rule = RollingWindowRule(
        key=f"cache-eviction:{uuid.uuid4().hex}",
        limit=1,
        window_seconds=60,
    )
    assert store.admit((rule,), member=uuid.uuid4().hex).admitted is True

    with override_settings(
        CACHES={
            **settings.CACHES,
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": f"login-eviction-{uuid.uuid4().hex}",
                "OPTIONS": {"CULL_FREQUENCY": 0, "MAX_ENTRIES": 1},
            },
        }
    ):
        default_cache = caches["default"]
        default_cache.set("first", "value")
        default_cache.set("second", "value")
        assert default_cache.get("first") is None

    blocked = store.admit((rule,), member=uuid.uuid4().hex)

    assert blocked.admitted is False


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_account_throttle_matches_database_identity_without_casefold_collisions(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Separate Unicode identities that PostgreSQL treats as distinct.

    Creates two existing accounts whose names collide under Python case folding, then proves each
    receives its own bucket while a database-case-insensitive variant shares the first account ID.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If distinct database identities share state or variants evade a bucket.
    """
    suffix = uuid.uuid4().hex
    sharp_username = f"straße-{suffix}"
    ascii_username = f"strasse-{suffix}"
    django_user_model.objects.create_user(
        sharp_username,
        f"sharp-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    django_user_model.objects.create_user(
        ascii_username,
        f"ascii-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    with override_settings(
        **_strict_throttle_settings(
            address_rate="100/minute",
            account_rate="1/minute",
        )
    ):
        sharp = _post_credentials(
            client,
            sharp_username,
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        ascii_response = _post_credentials(
            client,
            ascii_username,
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        sharp_variant = _post_credentials(
            client,
            f"Straße-{suffix}",
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    assert sharp.status_code == HTTPStatus.UNAUTHORIZED
    assert ascii_response.status_code == HTTPStatus.UNAUTHORIZED
    assert sharp_variant.status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_postgresql_distinct_unicode_usernames_authenticate_only_their_accounts(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Authenticate usernames using the model constraint's PostgreSQL lowercase semantics.

    Creates uppercase Latin I and lowercase dotless I accounts that PostgreSQL ``LOWER`` keeps
    distinct, then exchanges each account's unique password and verifies each token belongs only to
    the submitted account.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If lookup conflates the usernames or assigns either account's token wrongly.
    """
    suffix = uuid.uuid4().hex
    latin_password = f"{PASSWORD}-latin"
    dotless_password = f"{PASSWORD}-dotless"
    latin_account = django_user_model.objects.create_user(
        f"I-{suffix}",
        f"latin-i-{suffix}@localforge.invalid",
        latin_password,
        is_active=True,
    )
    dotless_account = django_user_model.objects.create_user(
        f"\u0131-{suffix}",
        f"dotless-i-{suffix}@localforge.invalid",
        dotless_password,
        is_active=True,
    )

    with override_settings(**_unlimited_login_settings()):
        latin_response = _post_credentials(
            client,
            latin_account.username,
            latin_password,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        dotless_response = _post_credentials(
            client,
            dotless_account.username,
            dotless_password,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    with override_settings(
        **_strict_throttle_settings(
            address_rate="100/minute",
            account_rate="2/minute",
        )
    ):
        latin_second = _post_credentials(
            client,
            latin_account.username,
            latin_password,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        dotless_second = _post_credentials(
            client,
            dotless_account.username,
            dotless_password,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        latin_blocked = _post_credentials(
            client,
            latin_account.username,
            latin_password,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        dotless_blocked = _post_credentials(
            client,
            dotless_account.username,
            dotless_password,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    assert latin_response.status_code == HTTPStatus.OK
    assert dotless_response.status_code == HTTPStatus.OK
    assert latin_second.status_code == HTTPStatus.OK
    assert dotless_second.status_code == HTTPStatus.OK
    assert latin_blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert dotless_blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS

    latin_key = cast("str", cast("dict[str, Any]", latin_response.json())["token"])
    dotless_key = cast("str", cast("dict[str, Any]", dotless_response.json())["token"])
    latin_token_owner = (
        Token.objects.using("default").values_list("user_id", flat=True).get(key=latin_key)
    )
    dotless_token_owner = (
        Token.objects.using("default").values_list("user_id", flat=True).get(key=dotless_key)
    )

    assert latin_token_owner == latin_account.pk
    assert dotless_token_owner == dotless_account.pk
    assert latin_key != dotless_key


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(10)
def test_login_throttle_database_outage_fails_closed_and_recovers(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject login during primary-database outage and recover afterward.

    Points only the admission store at a refused local port and verifies no credential guess is
    admitted with a correlated service failure, then restores the primary alias and proves a retry
    reaches credential verification without clearing any shared state.

    Arguments:
        client: Django test client supplied by the framework.
        monkeypatch: Fixture inducing and restoring the database outage.

    Returns:
        None.

    Raises:
        AssertionError: If an outage admits the request, leaks another contract, or cannot recover.
    """
    original_admit = PostgresLoginThrottleStore.admit

    def fail_admission(
        _store: PostgresLoginThrottleStore,
        _rules: tuple[RollingWindowRule, ...],
        *,
        member: str,
    ) -> object:
        """Raise the database exception produced by an unavailable primary.

        Replaces only the authoritative store call so the request still crosses address and account
        resolution, DRF throttling, exception translation, and response correlation.

        Arguments:
            _store: Store whose call is being induced to fail.
            _rules: Admission dimensions the request resolved.
            member: Correlated request identifier that would have been persisted.

        Returns:
            Never returns.

        Raises:
            OperationalError: Always.
        """
        del member
        message = "controlled primary outage"
        raise OperationalError(message)

    monkeypatch.setattr(PostgresLoginThrottleStore, "admit", fail_admission)
    response = _post_credentials(
        client,
        f"token-outage-{uuid.uuid4().hex}",
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    monkeypatch.setattr(PostgresLoginThrottleStore, "admit", original_admit)
    recovered = _post_credentials(
        client,
        f"token-recovered-{uuid.uuid4().hex}",
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
    assert recovered.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.serial
@pytest.mark.timeout(30)
def test_login_throttle_state_loss_fails_closed_and_recovers(client: Client) -> None:
    """Reject login while the authoritative admission table is unavailable.

    Renames the PostgreSQL table transactionally visible to every worker, verifies the HTTP
    boundary returns a correlated service failure, restores the table, and proves admission
    recovers without manufacturing replacement state.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If missing security state admits a request or prevents recovery.
    """
    table_name = LOGIN_THROTTLE_TABLE
    missing_table_name = f"{table_name}_missing"

    with connection.schema_editor() as editor:
        editor.alter_db_table(LoginThrottleEvent, table_name, missing_table_name)

    try:
        unavailable = _post_credentials(
            client,
            f"token-state-loss-{uuid.uuid4().hex}",
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
    finally:
        with connection.schema_editor() as editor:
            editor.alter_db_table(LoginThrottleEvent, missing_table_name, table_name)

    recovered = _post_credentials(
        client,
        f"token-state-recovered-{uuid.uuid4().hex}",
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )

    assert unavailable.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", unavailable.json()) == {
        "code": ErrorCode.SERVICE_UNAVAILABLE,
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": unavailable.headers[REQUEST_ID_HEADER],
    }
    assert recovered.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_rejected_account_states_are_wire_equivalent(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return one indistinguishable credential failure for every account state.

    Fixes correlation randomness while comparing raw response bytes and status, proving wrong
    Wrong passwords, unknown usernames, inactive accounts, and unusable passwords disclose no state
    through the public representation while still carrying the request identifier envelope.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture controlling generated request identifiers.

    Returns:
        None.

    Raises:
        AssertionError: If any account state changes the failure response.
    """
    suffix = uuid.uuid4().hex
    active_username = f"token-wrong-{suffix}"
    inactive_username = f"token-inactive-{suffix}"
    unusable_username = f"token-unusable-{suffix}"
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
    unusable = django_user_model.objects.create_user(
        unusable_username,
        f"{unusable_username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    unusable.set_unusable_password()
    unusable.save(using="default", update_fields=["password"])
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
            f"token-unknown-{suffix}",
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        inactive = _post_credentials(
            client,
            inactive_username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        unusable_response = _post_credentials(
            client,
            unusable_username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    assert {
        wrong.status_code,
        unknown.status_code,
        inactive.status_code,
        unusable_response.status_code,
    } == {HTTPStatus.UNAUTHORIZED}
    assert wrong.content == unknown.content == inactive.content == unusable_response.content
    assert wrong.headers[REQUEST_ID_HEADER] == str(request_identifier)
    assert cast("dict[str, Any]", wrong.json()) == {
        "code": ErrorCode.AUTHENTICATION_FAILED,
        "message": "Authentication failed.",
        "details": {},
        "request_id": str(request_identifier),
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("profile_name", STORABLE_RESET_REQUIRED_PROFILE_NAMES)
def test_reset_required_profiles_use_the_unknown_account_schedule_without_mutation(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    profile_name: str,
) -> None:
    """Reject reset-required credentials through the fixed unknown-account schedule.

    Submits the correct password for an active account carrying each non-accepted encoding, proves
    the encoding is never verified, and compares the response and hash work with an unknown user.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture observing bounded password verification.
        profile_name: Reset-required fixture name.

    Returns:
        None.

    Raises:
        AssertionError: If login verifies, mutates, or reveals a reset-required encoding.
    """
    username = f"token-reset-required-{profile_name}-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    original_hash = _reset_required_password_hash(profile_name)
    account.password = original_hash
    account.save(using="default", update_fields=["password"])
    checked_hashes: list[str] = []
    original_verify_password = credentials_module.verify_encoded_password

    def observe_password(
        raw_password: str,
        encoded: str,
        *,
        preferred: str = "default",
    ) -> bool:
        """Record and execute one bounded schedule verification.

        Preserves the real dummy verification while exposing the encoded inputs needed to prove the
        stored reset-required value never reaches a password hasher.

        Arguments:
            raw_password: Submitted password.
            encoded: Schedule-only encoded password.
            preferred: Hasher defining the schedule work profile.

        Returns:
            Whether the password matches the schedule-only encoding.
        """
        checked_hashes.append(encoded)

        return original_verify_password(raw_password, encoded, preferred=preferred)

    monkeypatch.setattr(credentials_module, "verify_encoded_password", observe_password)
    request_identifier = uuid.uuid4()
    monkeypatch.setattr("config.logs.uuid.uuid4", lambda: request_identifier)

    with override_settings(**_unlimited_login_settings()):
        reset_required = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        unknown = _post_credentials(
            client,
            f"token-reset-unknown-{uuid.uuid4().hex}",
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    account.refresh_from_db(using="default")
    schedule_length = len(SUPPORTED_HASHER_ALGORITHMS)
    reset_schedule = checked_hashes[:schedule_length]
    unknown_schedule = checked_hashes[schedule_length:]

    assert reset_required.status_code == unknown.status_code == HTTPStatus.UNAUTHORIZED
    assert reset_required.content == unknown.content
    assert (
        reset_required.content
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
    assert account.password == original_hash
    assert not Token.objects.using("default").filter(user=account).exists()
    assert original_hash not in checked_hashes
    assert len(reset_schedule) == len(unknown_schedule) == schedule_length
    assert [identify_hasher(encoded).algorithm for encoded in reset_schedule] == list(
        SUPPORTED_HASHER_ALGORITHMS
    )
    assert [
        {
            name: value
            for name, value in identify_hasher(encoded).safe_summary(encoded).items()
            if name not in {"salt", "hash"}
        }
        for encoded in reset_schedule
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
@pytest.mark.parametrize("hasher_algorithm", SUPPORTED_HASHER_ALGORITHMS)
def test_wrong_and_unknown_credentials_perform_equivalent_hash_work(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    hasher_algorithm: str,
) -> None:
    """Perform the same complete hasher schedule for known and unknown accounts.

    Stores the known account under each supported algorithm in turn, records Django model password
    checks, and proves both paths execute every configured hasher with equal work factors.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture observing password verification calls.
        hasher_algorithm: Algorithm used by the known account for this case.

    Returns:
        None.

    Raises:
        AssertionError: If either path skips, reorders, or changes password-hash work.
    """
    username = f"token-hash-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    account.password = make_password(PASSWORD, hasher=hasher_algorithm)
    account.save(using="default", update_fields=["password"])
    encoded_hashes: list[str] = []
    original_verify_password = credentials_module.verify_encoded_password

    def observe_password(
        raw_password: str,
        encoded: str,
        *,
        preferred: str = "default",
    ) -> bool:
        """Record and execute one encoded password verification.

        Preserves Django's real hash path while exposing only encoded metadata needed to compare
        algorithms and work factors.

        Arguments:
            raw_password: Submitted password.
            encoded: Stored or dummy encoded password.
            preferred: Hasher defining current runtime-hardening parameters.

        Returns:
            Whether the password matches the encoded value.
        """
        encoded_hashes.append(encoded)

        return original_verify_password(raw_password, encoded, preferred=preferred)

    monkeypatch.setattr(credentials_module, "verify_encoded_password", observe_password)

    with override_settings(**_unlimited_login_settings()):
        _post_credentials(
            client,
            username,
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        _post_credentials(
            client,
            f"token-unknown-{uuid.uuid4().hex}",
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    expected_verifications = len(SUPPORTED_HASHER_ALGORITHMS)
    assert len(encoded_hashes) == expected_verifications * EXPECTED_HASH_VERIFICATIONS
    wrong_hashes = encoded_hashes[:expected_verifications]
    unknown_hashes = encoded_hashes[expected_verifications:]
    assert tuple(identify_hasher(encoded).algorithm for encoded in wrong_hashes) == (
        SUPPORTED_HASHER_ALGORITHMS
    )
    assert tuple(identify_hasher(encoded).algorithm for encoded in unknown_hashes) == (
        SUPPORTED_HASHER_ALGORITHMS
    )
    summaries = [identify_hasher(encoded).safe_summary(encoded) for encoded in encoded_hashes]
    comparable = [
        {name: value for name, value in summary.items() if name not in {"salt", "hash"}}
        for summary in summaries
    ]
    assert comparable[:expected_verifications] == comparable[expected_verifications:]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("hasher_algorithm", RECOGNIZED_LOWER_HASHERS)
def test_recognized_lower_password_hashes_receive_runtime_hardening(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    hasher_algorithm: str,
) -> None:
    """Top up wrong-password work for every accepted lower-iteration hash.

    Records the complete password-check schedule and proves both PBKDF2 variants use Django runtime
    hardening while retaining the same configured-algorithm schedule as an unknown account.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture observing password verification calls.
        hasher_algorithm: Recognized lower-iteration algorithm under test.

    Returns:
        None.

    Raises:
        AssertionError: If lower-iteration work is not hardened to current cost.
    """
    username = f"token-lower-schedule-{hasher_algorithm}-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    account.password = _recognized_lower_password_hash(hasher_algorithm)
    account.save(using="default", update_fields=["password"])
    original_hash = account.password
    checked_hashes: list[str] = []
    hardened_hashes: list[str] = []
    original_verify_password = credentials_module.verify_encoded_password
    hasher_type = type(identify_hasher(original_hash))
    original_harden_runtime = hasher_type.harden_runtime

    def observe_password(
        raw_password: str,
        encoded: str,
        *,
        preferred: str = "default",
    ) -> bool:
        """Record and execute one encoded password verification.

        Preserves Django's real verification while exposing the encoded schedule needed to
        distinguish stale and current work parameters.

        Arguments:
            raw_password: Submitted password.
            encoded: Stored or dummy encoded password.
            preferred: Hasher defining current runtime-hardening parameters.

        Returns:
            Whether the password matches the encoded value.
        """
        checked_hashes.append(encoded)

        return original_verify_password(raw_password, encoded, preferred=preferred)

    def observe_harden_runtime(
        hasher: BasePasswordHasher,
        password: str,
        encoded: str,
    ) -> None:
        """Record and execute one Django runtime-hardening operation.

        Captures the stored encoding whose stale work is supplemented while preserving the real
        hasher behavior used by the HTTP credential boundary.

        Arguments:
            hasher: Configured hasher performing the runtime top-up.
            password: Submitted raw password.
            encoded: Stored stale password encoding.

        Returns:
            None.
        """
        hardened_hashes.append(encoded)
        original_harden_runtime(hasher, password, encoded)

    monkeypatch.setattr(credentials_module, "verify_encoded_password", observe_password)
    monkeypatch.setattr(hasher_type, "harden_runtime", observe_harden_runtime)

    with override_settings(**_unlimited_login_settings()):
        response = _post_credentials(
            client,
            username,
            f"{PASSWORD}-wrong",
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    account.refresh_from_db(using="default")
    stale_index = SUPPORTED_HASHER_ALGORITHMS.index(hasher_algorithm)
    stored_hash = checked_hashes[stale_index]
    configured_hasher = identify_hasher(stored_hash)
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert account.password == original_hash
    assert len(checked_hashes) == len(SUPPORTED_HASHER_ALGORITHMS)
    assert configured_hasher.must_update(stored_hash) is True
    assert [identify_hasher(encoded).algorithm for encoded in checked_hashes] == list(
        SUPPORTED_HASHER_ALGORITHMS
    )
    assert hardened_hashes == [original_hash]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("hasher_algorithm", RECOGNIZED_LOWER_HASHERS)
def test_inactive_recognized_lower_passwords_harden_without_mutating_the_account(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    hasher_algorithm: str,
) -> None:
    """Equalize inactive lower-iteration rejection without upgrading stored state.

    Submits the correct secret for an inactive lower-iteration PBKDF2 account, records the complete
    schedule, and verifies runtime hardening preserves the original encoding.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture observing password verification calls.
        hasher_algorithm: Recognized lower-iteration algorithm under test.

    Returns:
        None.

    Raises:
        AssertionError: If rejection skips hardening or mutates the inactive account.
    """
    username = f"token-inactive-lower-{hasher_algorithm}-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
    )
    original_hash = _recognized_lower_password_hash(hasher_algorithm)
    account.password = original_hash
    account.save(using="default", update_fields=["password"])
    checked_hashes: list[str] = []
    hardened_hashes: list[str] = []
    original_verify_password = credentials_module.verify_encoded_password
    hasher_type = type(identify_hasher(original_hash))
    original_harden_runtime = hasher_type.harden_runtime

    def observe_password(
        raw_password: str,
        encoded: str,
        *,
        preferred: str = "default",
    ) -> bool:
        """Record and execute one encoded password verification.

        Preserves real encoded-password verification while exposing the schedule used before the
        public account persistence assertion.

        Arguments:
            raw_password: Submitted password.
            encoded: Stored or dummy encoded password.
            preferred: Hasher defining current runtime-hardening parameters.

        Returns:
            Whether the password matches the encoded value.
        """
        checked_hashes.append(encoded)

        return original_verify_password(raw_password, encoded, preferred=preferred)

    monkeypatch.setattr(credentials_module, "verify_encoded_password", observe_password)

    def observe_harden_runtime(
        hasher: BasePasswordHasher,
        password: str,
        encoded: str,
    ) -> None:
        """Record and execute explicit hardening for a correct inactive password.

        Captures the accepted lower encoding while preserving the real PBKDF2 delta calculation
        used to equalize the public rejection path.

        Arguments:
            hasher: PBKDF2 hasher performing the runtime top-up.
            password: Submitted correct password.
            encoded: Stored lower-iteration encoding.

        Returns:
            None.
        """
        hardened_hashes.append(encoded)
        original_harden_runtime(hasher, password, encoded)

    monkeypatch.setattr(hasher_type, "harden_runtime", observe_harden_runtime)

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
    assert len(checked_hashes) == len(SUPPORTED_HASHER_ALGORITHMS)
    assert [identify_hasher(encoded).algorithm for encoded in checked_hashes] == list(
        SUPPORTED_HASHER_ALGORITHMS
    )
    assert hardened_hashes == [original_hash]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("hasher_algorithm", SHORT_SALT_HASHERS)
def test_inactive_current_cost_short_salt_passwords_do_not_add_hash_work(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    hasher_algorithm: str,
) -> None:
    """Reject inactive short-salt credentials without redundant current-cost work.

    Submits the correct secret for a current-iteration PBKDF2 hash whose salt still requires an
    upgrade, records the complete schedule, and proves rejection preserves the stored encoding.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture observing password verification calls.
        hasher_algorithm: Current-cost PBKDF2 algorithm under test.

    Returns:
        None.

    Raises:
        AssertionError: If salt staleness adds hash work or mutates the inactive account.
    """
    username = f"token-inactive-short-salt-{hasher_algorithm}-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
    )
    original_hash = _current_cost_short_salt_password_hash(hasher_algorithm)
    account.password = original_hash
    account.save(using="default", update_fields=["password"])
    checked_hashes: list[str] = []
    original_verify_password = credentials_module.verify_encoded_password

    def observe_password(
        raw_password: str,
        encoded: str,
        *,
        preferred: str = "default",
    ) -> bool:
        """Record and execute one encoded password verification.

        Preserves real verification while exposing the public credential path's complete algorithm
        schedule for deterministic comparison.

        Arguments:
            raw_password: Submitted password.
            encoded: Stored or dummy encoded password.
            preferred: Hasher defining current runtime-hardening parameters.

        Returns:
            Whether the password matches the encoded value.
        """
        checked_hashes.append(encoded)

        return original_verify_password(raw_password, encoded, preferred=preferred)

    monkeypatch.setattr(credentials_module, "verify_encoded_password", observe_password)

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
    assert identify_hasher(original_hash).must_update(original_hash) is True
    assert len(checked_hashes) == len(SUPPORTED_HASHER_ALGORITHMS)
    assert [identify_hasher(encoded).algorithm for encoded in checked_hashes] == list(
        SUPPORTED_HASHER_ALGORITHMS
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(360)
@pytest.mark.security_timing
@pytest.mark.parametrize("hasher_algorithm", SUPPORTED_HASHER_ALGORITHMS)
def test_wrong_and_unknown_credentials_meet_the_timing_criterion(
    client: Client,
    django_user_model: type[User],
    hasher_algorithm: str,
) -> None:
    """Keep known and unknown credential failures within the approved timing bound.

    Warms both paths, alternates thirty measured requests for each, and compares medians against the
    larger of twenty percent or ten milliseconds. The dedicated timing stage bounds concurrent
    password benchmarks while distributing independent parameter cases.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        hasher_algorithm: Algorithm used by the known account for this case.

    Returns:
        None.

    Raises:
        AssertionError: If median response time discloses whether the account exists.
    """
    username = f"token-timing-{uuid.uuid4().hex}"
    unknown_username = f"token-unknown-timing-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    account.password = make_password(PASSWORD, hasher=hasher_algorithm)
    account.save(using="default", update_fields=["password"])

    def measure(candidate: str) -> float:
        """Measure one rejected credential exchange.

        Times the full request and verifies it reached authentication failure rather than a
        throttle, ensuring every sample represents the intended security path.

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
            "token credential timing measured hasher=%s wrong=%.6fs unknown=%.6fs "
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
def test_concurrent_wrong_and_unknown_token_batches_meet_the_timing_criterion(
    django_user_model: type[User],
) -> None:
    """Keep concurrent existing and unknown rejection batches within the approved bound.

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
    username = f"token-concurrent-timing-{uuid.uuid4().hex}"
    unknown_username = f"token-concurrent-unknown-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    def measure_batch(candidate: str, batch_index: int) -> float:
        """Measure one synchronized rejected credential batch.

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
            close_old_connections()
            try:
                barrier.wait()
                response = _post_credentials(
                    DjangoClient(),
                    candidate,
                    f"{PASSWORD}-wrong",
                    remote_address=f"198.18.{batch_index}.{request_index + 1}",
                )
                return response.status_code
            finally:
                close_old_connections()

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
            "token concurrent credential timing batch=%d samples=%d wrong=%.6fs "
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
@pytest.mark.timeout(360)
@pytest.mark.security_timing
@pytest.mark.parametrize("hasher_algorithm", RECOGNIZED_LOWER_HASHERS)
def test_recognized_lower_wrong_passwords_meet_the_timing_criterion(
    client: Client,
    django_user_model: type[User],
    hasher_algorithm: str,
) -> None:
    """Keep recognized lower-iteration credential failures within the approved timing bound.

    Stores a lower PBKDF2 iteration count, warms five requests per path, then alternates thirty
    measured wrong-password and unknown-account requests before comparing medians.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        hasher_algorithm: Recognized lower-iteration algorithm under test.

    Returns:
        None.

    Raises:
        AssertionError: If lower iterations disclose whether the account exists.
    """
    username = f"token-lower-timing-{hasher_algorithm}-{uuid.uuid4().hex}"
    unknown_username = f"token-lower-unknown-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    account.password = _recognized_lower_password_hash(hasher_algorithm)
    account.save(using="default", update_fields=["password"])

    def measure(candidate: str) -> float:
        """Measure one lower-iteration credential rejection.

        Times the complete HTTP request and verifies authentication, rather than throttling, made
        the observable decision represented by the sample.

        Arguments:
            candidate: Known or unknown username to submit.

        Returns:
            Elapsed request duration in seconds.

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
            "lower token timing measured hasher=%s wrong=%.6fs unknown=%.6fs "
            "delta=%.6fs allowed=%.6fs"
        ),
        hasher_algorithm,
        wrong_median,
        unknown_median,
        median_delta,
        allowed_delta,
    )

    assert median_delta <= allowed_delta, (
        f"lower={hasher_algorithm} wrong={wrong_median:.6f}s "
        f"unknown={unknown_median:.6f}s delta={median_delta:.6f}s "
        f"allowed={allowed_delta:.6f}s"
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(360)
@pytest.mark.security_timing
@pytest.mark.parametrize(
    "profile_name",
    ["stronger-pbkdf2", "mixed-argon2", "mixed-scrypt"],
)
def test_reset_required_profiles_meet_the_unknown_account_timing_criterion(
    client: Client,
    django_user_model: type[User],
    profile_name: str,
) -> None:
    """Keep stronger and mixed reset-required profiles inside the approved timing bound.

    Preserves one attacker-controlled encoding, warms five requests per path, then alternates
    thirty measured reset-required and unknown HTTP rejections before comparing their medians.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        profile_name: Stronger or mixed reset-required profile under test.

    Returns:
        None.

    Raises:
        AssertionError: If the stored profile changes or discloses account existence by timing.
    """
    username = f"token-reset-timing-{profile_name}-{uuid.uuid4().hex}"
    unknown_username = f"token-reset-unknown-timing-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    original_hash = _reset_required_password_hash(profile_name)
    account.password = original_hash
    account.save(using="default", update_fields=["password"])

    def measure(candidate: str) -> float:
        """Measure one reset-required or unknown credential rejection.

        Times the complete HTTP request and verifies authentication, rather than throttling, made
        the observable rejection.

        Arguments:
            candidate: Reset-required or unknown username to submit.

        Returns:
            Elapsed request duration in seconds.

        Raises:
            AssertionError: If the request returns another status.
        """
        started = time.perf_counter()
        response = _post_credentials(
            client,
            candidate,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        elapsed = time.perf_counter() - started

        assert response.status_code == HTTPStatus.UNAUTHORIZED

        return elapsed

    with override_settings(**_unlimited_login_settings()):
        for _warmup in range(TIMING_WARMUP_REQUESTS):
            measure(username)
            measure(unknown_username)

        reset_samples: list[float] = []
        unknown_samples: list[float] = []
        for attempt in range(TIMING_MEASURED_REQUESTS):
            first, second = (
                (username, unknown_username) if attempt % 2 == 0 else (unknown_username, username)
            )
            first_elapsed = measure(first)
            second_elapsed = measure(second)

            if first == username:
                reset_samples.append(first_elapsed)
                unknown_samples.append(second_elapsed)
            else:
                unknown_samples.append(first_elapsed)
                reset_samples.append(second_elapsed)

    account.refresh_from_db(using="default")
    reset_median = statistics.median(reset_samples)
    unknown_median = statistics.median(unknown_samples)
    median_delta = abs(reset_median - unknown_median)
    allowed_delta = max(
        max(reset_median, unknown_median) * TIMING_RELATIVE_LIMIT,
        TIMING_ABSOLUTE_LIMIT_SECONDS,
    )
    timing_logger.info(
        (
            "reset-required token timing measured profile=%s reset=%.6fs unknown=%.6fs "
            "delta=%.6fs allowed=%.6fs"
        ),
        profile_name,
        reset_median,
        unknown_median,
        median_delta,
        allowed_delta,
    )

    assert account.password == original_hash
    assert median_delta <= allowed_delta, (
        f"reset-required={profile_name} reset={reset_median:.6f}s "
        f"unknown={unknown_median:.6f}s delta={median_delta:.6f}s "
        f"allowed={allowed_delta:.6f}s"
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(360)
@pytest.mark.security_timing
@pytest.mark.parametrize("hasher_algorithm", RECOGNIZED_LOWER_HASHERS)
def test_inactive_recognized_lower_passwords_meet_the_timing_criterion(
    client: Client,
    django_user_model: type[User],
    hasher_algorithm: str,
) -> None:
    """Keep inactive lower-iteration correct-password rejection within the approved timing bound.

    Preserves one lower-iteration inactive encoding, warms five requests per path, then alternates
    thirty measured inactive and unknown HTTP rejections before comparing their observable medians.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        hasher_algorithm: Recognized lower-iteration algorithm under test.

    Returns:
        None.

    Raises:
        AssertionError: If correct credentials disclose inactive account existence or mutate it.
    """
    username = f"token-inactive-lower-timing-{hasher_algorithm}-{uuid.uuid4().hex}"
    unknown_username = f"token-inactive-lower-unknown-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
    )
    original_hash = _recognized_lower_password_hash(hasher_algorithm)
    account.password = original_hash
    account.save(using="default", update_fields=["password"])

    def measure(candidate: str) -> float:
        """Measure one inactive or unknown credential rejection.

        Times the complete HTTP request and verifies authentication, rather than throttling, made
        the observable rejection.

        Arguments:
            candidate: Inactive or unknown username to submit.

        Returns:
            Elapsed request duration in seconds.

        Raises:
            AssertionError: If the request returns another status.
        """
        started = time.perf_counter()
        response = _post_credentials(
            client,
            candidate,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        elapsed = time.perf_counter() - started

        assert response.status_code == HTTPStatus.UNAUTHORIZED

        return elapsed

    with override_settings(**_unlimited_login_settings()):
        for _warmup in range(TIMING_WARMUP_REQUESTS):
            measure(username)
            measure(unknown_username)

        inactive_samples: list[float] = []
        unknown_samples: list[float] = []
        for attempt in range(TIMING_MEASURED_REQUESTS):
            first, second = (
                (username, unknown_username) if attempt % 2 == 0 else (unknown_username, username)
            )
            first_elapsed = measure(first)
            second_elapsed = measure(second)

            if first == username:
                inactive_samples.append(first_elapsed)
                unknown_samples.append(second_elapsed)
            else:
                unknown_samples.append(first_elapsed)
                inactive_samples.append(second_elapsed)

    account.refresh_from_db(using="default")
    inactive_median = statistics.median(inactive_samples)
    unknown_median = statistics.median(unknown_samples)
    median_delta = abs(inactive_median - unknown_median)
    allowed_delta = max(
        max(inactive_median, unknown_median) * TIMING_RELATIVE_LIMIT,
        TIMING_ABSOLUTE_LIMIT_SECONDS,
    )
    timing_logger.info(
        (
            "inactive lower token timing measured hasher=%s inactive=%.6fs unknown=%.6fs "
            "delta=%.6fs allowed=%.6fs"
        ),
        hasher_algorithm,
        inactive_median,
        unknown_median,
        median_delta,
        allowed_delta,
    )

    assert account.password == original_hash
    assert median_delta <= allowed_delta, (
        f"inactive-lower={hasher_algorithm} inactive={inactive_median:.6f}s "
        f"unknown={unknown_median:.6f}s delta={median_delta:.6f}s "
        f"allowed={allowed_delta:.6f}s"
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(360)
@pytest.mark.security_timing
@pytest.mark.parametrize("hasher_algorithm", SHORT_SALT_HASHERS)
def test_inactive_current_cost_short_salt_passwords_meet_the_timing_criterion(
    client: Client,
    django_user_model: type[User],
    hasher_algorithm: str,
) -> None:
    """Keep inactive current-cost short-salt rejection within the approved timing bound.

    Preserves one inactive short-salt encoding, warms five requests per path, then alternates
    thirty measured inactive and unknown HTTP rejections before comparing observable medians.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        hasher_algorithm: Current-cost PBKDF2 algorithm under test.

    Returns:
        None.

    Raises:
        AssertionError: If obsolete salt metadata leaks account state or mutates the account.
    """
    username = f"token-inactive-short-salt-timing-{hasher_algorithm}-{uuid.uuid4().hex}"
    unknown_username = f"token-inactive-short-salt-unknown-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
    )
    original_hash = _current_cost_short_salt_password_hash(hasher_algorithm)
    account.password = original_hash
    account.save(using="default", update_fields=["password"])

    def measure(candidate: str) -> float:
        """Measure one inactive short-salt or unknown credential rejection.

        Times the complete HTTP request and verifies authentication, rather than throttling, made
        the observable rejection.

        Arguments:
            candidate: Inactive or unknown username to submit.

        Returns:
            Elapsed request duration in seconds.

        Raises:
            AssertionError: If the request returns another status.
        """
        started = time.perf_counter()
        response = _post_credentials(
            client,
            candidate,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        elapsed = time.perf_counter() - started

        assert response.status_code == HTTPStatus.UNAUTHORIZED

        return elapsed

    with override_settings(**_unlimited_login_settings()):
        for _warmup in range(TIMING_WARMUP_REQUESTS):
            measure(username)
            measure(unknown_username)

        inactive_samples: list[float] = []
        unknown_samples: list[float] = []
        for attempt in range(TIMING_MEASURED_REQUESTS):
            first, second = (
                (username, unknown_username) if attempt % 2 == 0 else (unknown_username, username)
            )
            first_elapsed = measure(first)
            second_elapsed = measure(second)

            if first == username:
                inactive_samples.append(first_elapsed)
                unknown_samples.append(second_elapsed)
            else:
                unknown_samples.append(first_elapsed)
                inactive_samples.append(second_elapsed)

    account.refresh_from_db(using="default")
    inactive_median = statistics.median(inactive_samples)
    unknown_median = statistics.median(unknown_samples)
    median_delta = abs(inactive_median - unknown_median)
    allowed_delta = max(
        max(inactive_median, unknown_median) * TIMING_RELATIVE_LIMIT,
        TIMING_ABSOLUTE_LIMIT_SECONDS,
    )
    timing_logger.info(
        (
            "inactive short-salt token timing measured hasher=%s inactive=%.6fs unknown=%.6fs "
            "delta=%.6fs allowed=%.6fs"
        ),
        hasher_algorithm,
        inactive_median,
        unknown_median,
        median_delta,
        allowed_delta,
    )

    assert account.password == original_hash
    assert median_delta <= allowed_delta, (
        f"inactive-short-salt={hasher_algorithm} inactive={inactive_median:.6f}s "
        f"unknown={unknown_median:.6f}s delta={median_delta:.6f}s "
        f"allowed={allowed_delta:.6f}s"
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("hasher_algorithm", SUPPORTED_HASHER_ALGORITHMS[1:])
def test_successful_login_upgrades_legacy_password_hashes_on_the_primary(
    client: Client,
    django_user_model: type[User],
    hasher_algorithm: str,
) -> None:
    """Upgrade every accepted legacy password hash through Django's model path.

    Stores one active account with each non-preferred configured hasher, logs in successfully, and
    reloads from the primary to prove Django persisted the preferred hash before issuing a token.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        hasher_algorithm: Legacy algorithm used before successful login.

    Returns:
        None.

    Raises:
        AssertionError: If login fails or the primary retains the legacy hash.
    """
    username = f"token-upgrade-{hasher_algorithm}-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    account.password = make_password(PASSWORD, hasher=hasher_algorithm)
    account.save(using="default", update_fields=["password"])

    response = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.OK
    assert identify_hasher(account.password).algorithm == SUPPORTED_HASHER_ALGORITHMS[0]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("hasher_algorithm", RECOGNIZED_LOWER_HASHERS)
def test_successful_active_login_upgrades_recognized_lower_parameters_on_the_primary(
    client: Client,
    django_user_model: type[User],
    hasher_algorithm: str,
) -> None:
    """Upgrade accepted lower-iteration parameters only after active authentication.

    Stores a lower-iteration active encoding, completes the public exchange, and reloads from the
    primary to prove acceptance persisted the preferred current-cost hash before token issuance.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        hasher_algorithm: Recognized lower-iteration algorithm used before successful login.

    Returns:
        None.

    Raises:
        AssertionError: If login fails or the primary retains lower parameters.
    """
    username = f"token-lower-upgrade-{hasher_algorithm}-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    account.password = _recognized_lower_password_hash(hasher_algorithm)
    account.save(using="default", update_fields=["password"])

    response = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    account.refresh_from_db(using="default")
    upgraded_hasher = identify_hasher(account.password)

    assert response.status_code == HTTPStatus.OK
    assert upgraded_hasher.algorithm == SUPPORTED_HASHER_ALGORITHMS[0]
    assert upgraded_hasher.must_update(account.password) is False


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="list"),
        pytest.param(None, id="null"),
        pytest.param("credentials", id="string"),
        pytest.param(7, id="number"),
        pytest.param(False, id="boolean"),
    ],
)
def test_login_rejects_non_object_json_with_a_correlated_validation_envelope(
    client: Client,
    payload: object,
) -> None:
    """Reject every valid JSON representation that is not an object.

    Posts public wire representations through the real throttle and serializer boundary, proving
    account throttling never assumes mapping input and validation retains request correlation.

    Arguments:
        client: Django test client supplied by the framework.
        payload: Valid non-object JSON representation.

    Returns:
        None.

    Raises:
        AssertionError: If input crashes throttling or escapes the validation envelope.
    """
    response = client.post(
        "/api/v1/token/login/",
        data=json.dumps(payload),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    body = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert body["code"] == ErrorCode.VALIDATION_ERROR
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert set(cast("dict[str, object]", body["details"])) == {"non_field_errors"}


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "username",
    [
        pytest.param([], id="list"),
        pytest.param({}, id="object"),
        pytest.param(False, id="boolean"),
        pytest.param(None, id="null"),
    ],
)
def test_login_rejects_non_string_usernames_with_a_correlated_validation_envelope(
    client: Client,
    username: object,
) -> None:
    """Reject username types the serializer cannot coerce without crashing throttling.

    Posts invalid field representations through account-identity normalization and verifies the
    serializer retains ownership of the correlated validation response.

    Arguments:
        client: Django test client supplied by the framework.
        username: Parsed username value rejected by DRF's declared character field.

    Returns:
        None.

    Raises:
        AssertionError: If throttling crashes or validation loses request correlation.
    """
    response = client.post(
        "/api/v1/token/login/",
        data=json.dumps({"username": username, "password": PASSWORD}),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    body = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert body["code"] == ErrorCode.VALIDATION_ERROR
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert set(cast("dict[str, object]", body["details"])) == {"username"}


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("payload", "missing_field"),
    [
        pytest.param({"password": PASSWORD}, "username", id="missing-username"),
        pytest.param({"username": "token-missing"}, "password", id="missing-password"),
    ],
)
def test_login_rejects_missing_credential_fields(
    client: Client,
    payload: dict[str, str],
    missing_field: str,
) -> None:
    """Return field-specific validation without echoing credentials.

    Omits each required field in turn and verifies the standard correlated envelope names only the
    missing field while preserving the submitted value outside every response field.

    Arguments:
        client: Django test client supplied by the framework.
        payload: Incomplete login representation.
        missing_field: Field expected in validation details.

    Returns:
        None.

    Raises:
        AssertionError: If validation loses its envelope or echoes a credential value.
    """
    response = client.post(
        "/api/v1/token/login/",
        payload,
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    body = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert body["code"] == ErrorCode.VALIDATION_ERROR
    assert set(cast("dict[str, object]", body["details"])) == {missing_field}
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert all(value.encode() not in response.content for value in payload.values())


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_logout_revokes_the_token_and_immediate_reuse_fails(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject a token immediately after logout destroys it.

    Logs in, logs out, and repeats the authenticated request with the same header, proving deletion
    is committed before the successful response and the revoked value receives the shared
    authentication failure envelope.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If logout retains the token or a revoked value authenticates.
    """
    username = f"token-revoke-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    login = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    token = cast("str", cast("dict[str, Any]", login.json())["token"])
    authorization = {"authorization": f"Token {token}"}

    logout = client.post("/api/v1/token/logout/", headers=authorization)
    reused = client.post("/api/v1/token/logout/", headers=authorization)
    reused_body = cast("dict[str, Any]", reused.json())

    assert logout.status_code == HTTPStatus.NO_CONTENT
    assert reused.status_code == HTTPStatus.UNAUTHORIZED
    assert reused_body == {
        "code": ErrorCode.AUTHENTICATION_FAILED,
        "message": "Authentication failed.",
        "details": {},
        "request_id": reused.headers[REQUEST_ID_HEADER],
    }
    assert token.encode() not in reused.content


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_logout_rejects_a_token_after_its_account_is_deactivated(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject a persisted token whose account is no longer active.

    Creates the accepted plaintext token row directly, then presents it after deactivation and
    verifies primary-backed token authentication enforces current account state before logout can
    reach its deletion body.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If an inactive account's existing token authenticates.
    """
    username = f"token-deactivated-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    account.is_active = False
    account.save(update_fields=["is_active", "updated_at"])

    response = client.post(
        "/api/v1/token/logout/",
        headers={"authorization": f"Token {token.key}"},
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.AUTHENTICATION_FAILED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_repeated_login_returns_the_account_single_non_expiring_token(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reuse the one DRF token assigned to an account.

    Exchanges the same active credentials twice and verifies the table and both responses identify
    one token, recording the accepted non-expiring single-token behavior rather than silently
    rotating on every login.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If login creates multiple tokens for one account.
    """
    username = f"token-single-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    with override_settings(**_unlimited_login_settings()):
        first = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        second = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    assert first.json() == second.json()
    assert Token.objects.using("default").filter(user=account).count() == 1


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_token_reissuance_bypasses_replica_routing_after_immediate_revocation(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Issue a replacement token entirely through the primary after revocation.

    Revokes one real token, installs a router that would send every unpinned token operation to
    stale replica state, and captures both connections while login creates the replacement.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If any issuance read, write, or retry can reach the replica.
    """
    username = f"token-primary-issuance-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    with override_settings(**_unlimited_login_settings()):
        initial = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
    revoked_key = cast("str", cast("dict[str, Any]", initial.json())["token"])
    logout = client.post(
        "/api/v1/token/logout/",
        headers={"authorization": f"Token {revoked_key}"},
    )

    with (
        override_settings(
            DATABASE_ROUTERS=(ReplicaLagTokenRouter(),),
            **_unlimited_login_settings(),
        ),
        CaptureQueriesContext(connections["default"]) as primary,
        CaptureQueriesContext(connections["replica"]) as replica,
    ):
        replacement = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    replacement_key = cast("str", cast("dict[str, Any]", replacement.json())["token"])
    primary_token_queries = [
        query["sql"] for query in primary.captured_queries if "authtoken_token" in query["sql"]
    ]
    replica_token_queries = [
        query["sql"] for query in replica.captured_queries if "authtoken_token" in query["sql"]
    ]

    assert initial.status_code == HTTPStatus.OK
    assert logout.status_code == HTTPStatus.NO_CONTENT
    assert replacement.status_code == HTTPStatus.OK
    assert replacement_key != revoked_key
    assert Token.objects.using("default").filter(key=replacement_key, user=account).exists()
    assert any("SELECT" in query for query in primary_token_queries)
    assert any("INSERT" in query for query in primary_token_queries)
    assert not replica_token_queries


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_token_login_locks_account_before_token_issuance(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Acquire the account row before reading or writing its DRF token.

    Captures a successful public login and verifies the credential transaction locks the account
    before the first token-table operation, matching every password-replacement revocation path.

    Arguments:
        client: Django test client issuing token login.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If token state is touched before the account lock.
    """
    username = f"token-lock-order-{uuid.uuid4().hex}"
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
    account_lock = next(
        index
        for index, statement in enumerate(statements)
        if '"accounts_user"' in statement and "FOR UPDATE" in statement
    )
    token_access = next(
        index for index, statement in enumerate(statements) if '"authtoken_token"' in statement
    )

    assert response.status_code == HTTPStatus.OK
    assert account_lock < token_access


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_token_login_persistence_loss_returns_service_unavailable(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contain DRF token persistence loss inside the dependency envelope.

    Authenticates an active account, fails only token retrieval or insertion inside the locked
    transaction, and proves no partial credential survives.

    Arguments:
        client: Django test client issuing token login.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture failing the token persistence boundary.

    Returns:
        None.

    Raises:
        AssertionError: If persistence loss escapes or leaves a token.
    """
    username = f"token-persistence-outage-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    original_get_or_create = QuerySet.get_or_create

    def fail_token_persistence(
        queryset: QuerySet[Any],
        defaults: dict[str, object] | None = None,
        **kwargs: object,
    ) -> tuple[object, bool]:
        """Fail only the DRF token persistence query.

        Preserves every other queryset operation while raising the database failure handled by the
        public login transaction.

        Arguments:
            queryset: Model queryset retrieving or creating a row.
            defaults: Optional Django creation defaults.
            **kwargs: Lookup values supplied by the caller.

        Returns:
            Real object and creation flag for models outside token persistence.

        Raises:
            DatabaseError: For the DRF token model.
        """
        if queryset.model is Token:
            raise DatabaseError
        return cast(
            "tuple[object, bool]",
            original_get_or_create(queryset, defaults=defaults, **kwargs),
        )

    monkeypatch.setattr(QuerySet, "get_or_create", fail_token_persistence)
    response = _post_credentials(
        client,
        username,
        PASSWORD,
        remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert not Token.objects.using("default").filter(user=account).exists()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_authtoken_migrations_are_applied(client: Client) -> None:
    """Apply token and authoritative throttle schemas to the primary database.

    Reads Django's migration recorder through the primary connection, proving the bundled token
    table and project admission table are integrated rather than created lazily.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If the latest authtoken migration was not applied.
    """
    del client
    applied = MigrationRecorder.Migration.objects.using("default").filter(app="authtoken")
    accounts_applied = MigrationRecorder.Migration.objects.using("default").filter(app="accounts")

    assert applied.filter(name="0004_alter_tokenproxy_options").exists()
    assert accounts_applied.filter(name="0002_loginthrottleevent").exists()
    assert accounts_applied.filter(name="0003_login_throttle_retention_index").exists()


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
def test_token_routes_document_every_reachable_response() -> None:
    """Expose the token login and logout contracts in the OpenAPI document.

    Generates the public schema and verifies each operation lists its successful and framework
    outcomes with concrete examples, while no logout or error example contains a token field.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a route, status, example, or token-leak constraint is missing.
    """
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    paths = cast("dict[str, Any]", schema["paths"])
    login = cast("dict[str, Any]", paths["/api/v1/token/login/"]["post"])
    logout = cast("dict[str, Any]", paths["/api/v1/token/logout/"]["post"])
    login_responses = cast("dict[str, Any]", login["responses"])
    logout_responses = cast("dict[str, Any]", logout["responses"])

    assert set(login_responses) == {
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
    }
    assert set(logout_responses) == {
        "204",
        "400",
        "401",
        "405",
        "406",
        "413",
        "429",
        "500",
        "503",
    }
    assert "token-persistence" in login_responses["503"]["description"]
    assert "Authoritative token or account state" in logout_responses["503"]["description"]

    for status, response in {**login_responses, **logout_responses}.items():
        if status == "204":
            continue

        content = cast("dict[str, Any]", response["content"])
        examples = cast("dict[str, Any]", content["application/json"]["examples"])

        assert examples

    rendered_errors = json.dumps(
        {
            "login": {
                status: value for status, value in login_responses.items() if status != "200"
            },
            "logout": logout_responses,
        },
        sort_keys=True,
    ).lower()

    assert '"token"' not in rendered_errors


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_login_observed_statuses_exactly_match_its_documented_contract(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare every empirically reachable token-login status with OpenAPI.

    Exercises success, validation, authentication, method, negotiation, body-size, media,
    throttling, dependency, and unexpected-failure paths through the public endpoint.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing contained operation failures.

    Returns:
        None.

    Raises:
        AssertionError: If observed and documented statuses differ.
    """
    username = f"token-login-contract-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    high_rate = "1000/minute"
    with override_settings(
        TOKEN_LOGIN_ADDRESS_THROTTLE_RATE=high_rate,
        TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE=high_rate,
    ):
        success = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        invalid = client.post(
            "/api/v1/token/login/",
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
        method_not_allowed = client.get("/api/v1/token/login/")
        not_acceptable = client.post(
            "/api/v1/token/login/",
            {"username": username, "password": PASSWORD},
            content_type="application/json",
            headers={"accept": "text/plain"},
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
            too_large = client.post(
                "/api/v1/token/login/",
                data="oversized",
                content_type="text/plain",
                REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )
        unsupported = client.post(
            "/api/v1/token/login/",
            data="unsupported",
            content_type="text/plain",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    throttle_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        1_800_000_050_000,
    )
    with override_settings(
        API_ANONYMOUS_THROTTLE_RATE=high_rate,
        API_AUTHENTICATION_THROTTLE_RATE="1/minute",
        TOKEN_LOGIN_ADDRESS_THROTTLE_RATE=high_rate,
        TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE=high_rate,
    ):
        _post_credentials(
            client,
            f"token-login-throttle-first-{uuid.uuid4().hex}",
            PASSWORD,
            remote_address=throttle_address,
        )
        throttled = _post_credentials(
            client,
            f"token-login-throttle-second-{uuid.uuid4().hex}",
            PASSWORD,
            remote_address=throttle_address,
        )

    def fail_unexpected(_view: object, _request: object) -> None:
        """Raise one contained token-login server failure.

        Replaces only the selected operation after framework admission so routing and response
        boundaries remain production-shaped.

        Arguments:
            _view: Token-login view instance.
            _request: Admitted REST request.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        message = "induced token-login failure"
        raise RuntimeError(message)

    def fail_unavailable(_view: object, _request: object) -> None:
        """Raise one token-login dependency failure.

        Replaces only the selected operation after framework admission so the shared correlated
        service-unavailable conversion remains executable.

        Arguments:
            _view: Token-login view instance.
            _request: Admitted REST request.

        Returns:
            Never returns.

        Raises:
            ServiceUnavailable: Always.
        """
        raise ServiceUnavailable

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(token_authentication_module.TokenLoginView, "post", fail_unexpected)
        client.raise_request_exception = False
        unexpected = client.post(
            "/api/v1/token/login/",
            {"username": username, "password": PASSWORD},
            content_type="application/json",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    with monkeypatch.context() as outage_patch:
        outage_patch.setattr(token_authentication_module.TokenLoginView, "post", fail_unavailable)
        unavailable = client.post(
            "/api/v1/token/login/",
            {"username": username, "password": PASSWORD},
            content_type="application/json",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
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
            cast("dict[str, Any]", schema["paths"])["/api/v1/token/login/"]["post"]["responses"],
        )
    }

    assert observed == documented


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_logout_observed_statuses_exactly_match_its_documented_contract(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare every empirically reachable logout status with OpenAPI.

    Exercises host validation, authentication, method, negotiation, body-size, success, primary
    authentication outage, and unexpected-failure paths through HTTP, including a successful
    unsupported-content-type body proving 415 is absent.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing the documented unexpected database failure.

    Returns:
        None.

    Raises:
        AssertionError: If observed and documented statuses differ or 415 becomes reachable.
    """
    username = f"token-logout-contract-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    authorization = {"authorization": f"Token {token.key}"}
    with override_settings(ALLOWED_HOSTS=["allowed.test"]):
        invalid_host = client.post("/api/v1/token/logout/", HTTP_HOST="invalid host")
    unauthorized = client.post("/api/v1/token/logout/")
    method_not_allowed = client.get("/api/v1/token/logout/", headers=authorization)
    not_acceptable = client.post(
        "/api/v1/token/logout/",
        headers={**authorization, "accept": "text/plain"},
    )
    with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
        too_large = client.post(
            "/api/v1/token/logout/",
            data="ignored",
            content_type="text/plain",
            headers=authorization,
        )
    success = client.post(
        "/api/v1/token/logout/",
        data="ignored",
        content_type="text/plain",
        headers=authorization,
    )
    outage_token = Token.objects.using("default").create(user=account)
    original_get = QuerySet.get

    def fail_primary_token_authentication(
        queryset: QuerySet[Any],
        *args: object,
        **kwargs: object,
    ) -> object:
        """Fail the real primary token lookup used by logout authentication.

        Raises only for the DRF token model while preserving unrelated querysets, proving the
        authenticator converts authoritative database loss before the view executes.

        Arguments:
            queryset: Queryset performing one object lookup.
            *args: Positional lookup arguments.
            **kwargs: Keyword lookup arguments.

        Returns:
            Object returned by non-token querysets.

        Raises:
            DatabaseError: For the DRF token authentication query.
        """
        if queryset.model is Token:
            raise DatabaseError
        return original_get(queryset, *args, **kwargs)

    with monkeypatch.context() as outage_patch:
        outage_patch.setattr(QuerySet, "get", fail_primary_token_authentication)
        authentication_unavailable = client.post(
            "/api/v1/token/logout/",
            headers={"authorization": f"Token {outage_token.key}"},
        )
    throttle_account = django_user_model.objects.create_user(
        f"{username}-throttle",
        f"{username}-throttle@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    throttle_token = Token.objects.using("default").create(user=throttle_account)
    throttle_authorization = {"authorization": f"Token {throttle_token.key}"}
    throttle_address = f"2001:db8::{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(
        "accounts.api_throttling.TEST_SERVER_TIME_MILLISECONDS",
        1_800_000_050_000,
    )

    def preserve_token(_token: Token) -> None:
        """Keep the authenticated token after one admitted logout.

        Replaces only deletion so two requests can share the same authenticated account and
        composite throttle dimensions without changing the route's admission behavior.

        Arguments:
            _token: Authenticated token whose deletion is suppressed.

        Returns:
            None.
        """

    monkeypatch.setattr(Token, "delete", preserve_token)
    with override_settings(
        API_BOUNDARY_ADDRESS_THROTTLE_RATE="1000/minute",
        API_AUTHENTICATION_THROTTLE_RATE="1/minute",
    ):
        client.post(
            "/api/v1/token/logout/",
            headers=throttle_authorization,
            REMOTE_ADDR=throttle_address,
        )
        throttled = client.post(
            "/api/v1/token/logout/",
            headers=throttle_authorization,
            REMOTE_ADDR=throttle_address,
        )
    failing_token = outage_token

    def fail_delete(_token: Token) -> None:
        """Raise the unexpected persistence failure documented by the route.

        Replaces only the database deletion boundary while the request still crosses routing,
        authentication, dispatch, error containment, and response correlation.

        Arguments:
            _token: Authenticated token whose deletion is being induced to fail.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always, representing an unexpected database failure.
        """
        message = "induced token deletion failure"
        raise RuntimeError(message)

    monkeypatch.setattr(Token, "delete", fail_delete)
    client.raise_request_exception = False
    unexpected = client.post(
        "/api/v1/token/logout/",
        headers={"authorization": f"Token {failing_token.key}"},
    )
    observed = {
        response.status_code
        for response in (
            unauthorized,
            invalid_host,
            method_not_allowed,
            not_acceptable,
            too_large,
            success,
            throttled,
            unexpected,
            authentication_unavailable,
        )
    }
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])["/api/v1/token/logout/"]["post"]["responses"],
        )
    }

    assert observed == documented
    assert HTTPStatus.UNSUPPORTED_MEDIA_TYPE not in observed
    assert success.status_code == HTTPStatus.NO_CONTENT
    assert cast("dict[str, Any]", unexpected.json())["code"] == ErrorCode.INTERNAL_SERVER_ERROR
    assert (
        cast("dict[str, Any]", authentication_unavailable.json())["code"]
        == ErrorCode.SERVICE_UNAVAILABLE
    )


@pytest.mark.integration
@pytest.mark.api_runtime
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_logout_maps_primary_token_deletion_failure_to_service_unavailable(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contain an authoritative token-deletion database failure.

    Authenticates through the public logout route, fails only primary token deletion, and verifies
    the documented correlated service-unavailable envelope rather than a generic server error.

    Arguments:
        client: Django client issuing the logout request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing token deletion.

    Returns:
        None.

    Raises:
        AssertionError: If deletion loss returns another status or code.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"token-delete-outage-{suffix}",
        f"token-delete-outage-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)

    def fail_database_delete(_token: Token) -> None:
        """Raise one authoritative token-deletion database failure.

        Replaces only the authenticated token's primary deletion boundary so the public response
        proves database loss is converted without changing authentication or routing behavior.

        Arguments:
            _token: Authenticated token whose deletion fails.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always.
        """
        raise DatabaseError

    monkeypatch.setattr(Token, "delete", fail_database_delete)
    response = client.post(
        "/api/v1/token/logout/",
        headers={"authorization": f"Token {token.key}"},
    )
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_issued_token_appears_only_in_the_successful_login_body(
    client: Client,
    django_user_model: type[User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep the issued credential out of URLs, headers, logs, and later responses.

    Captures a real login, logout, and revoked-token failure, then renders every emitted record
    through the production formatter and proves only the successful login body contains the token.

    Arguments:
        client: Django test client supplied by the framework.
        django_user_model: Configured custom user model class.
        caplog: Fixture collecting records emitted during the request sequence.

    Returns:
        None.

    Raises:
        AssertionError: If the issued value escapes its one permitted response body.
    """
    username = f"token-leak-{uuid.uuid4().hex}"
    django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    with caplog.at_level(logging.INFO):
        login = _post_credentials(
            client,
            username,
            PASSWORD,
            remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        token = cast("str", cast("dict[str, Any]", login.json())["token"])
        authorization = {"authorization": f"Token {token}"}
        logout = client.post("/api/v1/token/logout/", headers=authorization)
        reused = client.post("/api/v1/token/logout/", headers=authorization)

    rendered_logs = "\n".join(StructuredFormatter().format(record) for record in caplog.records)
    later_responses = b"\n".join(
        [
            logout.content,
            reused.content,
            repr(dict(logout.headers)).encode(),
            repr(dict(reused.headers)).encode(),
        ]
    )

    assert token.encode() in login.content
    assert "Location" not in login.headers
    assert token not in rendered_logs
    assert token.encode() not in later_responses
