"""Independent process worker for login-throttle integration tests.

Bootstraps Django inside a spawned process and exercises only the public PostgreSQL store, allowing
tests to prove authoritative admission across isolated application runtimes.
"""

import os
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from importlib import import_module
from typing import Protocol, cast

import django


class ThrottleModule(Protocol):
    """Describe the coordinated server-time seam exposed by the throttle module.

    Inherits from ``Protocol`` and exposes only the mutable test-time value spawned workers set
    after Django initialization.

    Attributes:
        TEST_SERVER_TIME_MILLISECONDS: Coordinated fixed-window time.

    Members:
        None.
    """

    TEST_SERVER_TIME_MILLISECONDS: int | None


@dataclass(frozen=True, slots=True)
class AuthenticatedAdmissionCase:
    """Describe one isolated authenticated-read process workload.

    Carries every value a spawned process needs as one serializable argument, keeping process-pool
    submission explicit without a wide positional function contract.

    Attributes:
        address: Client address shared by every spawned process.
        authorization: Existing token authorization header.
        attempts: Number of profile reads attempted by this worker.
        limit: Exact shared account and composite limit.
        database_name: Pytest database name shared by spawned workers.
        fixed_time_milliseconds: Coordinated Valkey time used by every worker.

    Members:
        None.
    """

    address: str
    authorization: str
    attempts: int
    limit: int
    database_name: str
    fixed_time_milliseconds: int


def count_postgres_throttle_admissions(
    key: str,
    attempts: int,
    limit: int,
    window_seconds: int,
    database_name: str,
) -> int:
    """Count admissions made by one isolated application process.

    Initializes Django after process creation, then submits distinct requests against a shared
    rolling-window key without reading or clearing any cache state.

    Arguments:
        key: Unique opaque rolling-window key shared by every worker.
        attempts: Number of decisions this worker requests.
        limit: Exact shared admission limit.
        window_seconds: Rolling window duration.
        database_name: Pytest database name shared by the spawned workers.

    Returns:
        Number of requests admitted for this worker.

    Raises:
        DatabaseError: If PostgreSQL cannot make the admission decision.
        ValueError: If the store rejects the supplied rule.
    """
    os.environ["POSTGRES_DB"] = database_name
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.testing")
    django.setup()

    throttle_module = import_module("accounts.login_throttle")
    store = throttle_module.PostgresLoginThrottleStore("default")
    rule = throttle_module.RollingWindowRule(
        key=key,
        limit=limit,
        window_seconds=window_seconds,
    )

    return sum(
        store.admit((rule,), member=f"{os.getpid()}-{uuid.uuid4().hex}").admitted
        for _attempt in range(attempts)
    )


def count_anonymous_api_admissions(
    address: str,
    attempts: int,
    limit: int,
    fixed_time_milliseconds: int,
) -> int:
    """Count anonymous API admissions from one isolated application process.

    Boots a complete Django runtime and calls the existing token-verification route through DRF,
    proving separate processes share the same cache-backed anonymous scope.

    Arguments:
        address: Client address shared by every spawned process.
        attempts: Number of requests this worker sends.
        limit: Exact shared fixed-window admission limit.
        fixed_time_milliseconds: Coordinated Valkey time used by every worker.

    Returns:
        Number of requests admitted past throttling in this process.

    Raises:
        RuntimeError: If Django cannot initialize or dispatch the existing route.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.testing")
    django.setup()

    settings_module = import_module("django.conf").settings
    settings_module.ALLOWED_HOSTS = ["testserver"]
    settings_module.API_ANONYMOUS_THROTTLE_RATE = f"{limit}/minute"
    settings_module.API_BOUNDARY_ADDRESS_THROTTLE_RATE = "1000/minute"
    settings_module.API_AUTHENTICATION_THROTTLE_RATE = "1000/minute"
    throttle_module = cast("ThrottleModule", import_module("accounts.api_throttling"))
    throttle_module.TEST_SERVER_TIME_MILLISECONDS = fixed_time_milliseconds
    client_type = import_module("rest_framework.test").APIClient
    client = client_type()

    admitted = 0
    for _attempt in range(attempts):
        response = client.post(
            "/api/v1/jwt/verify/",
            data={},
            format="json",
            REMOTE_ADDR=address,
        )
        if int(response.status_code) != HTTPStatus.TOO_MANY_REQUESTS:
            admitted += 1

    return admitted


def count_authenticated_api_admissions(
    case: AuthenticatedAdmissionCase,
) -> int:
    """Count authenticated-read admissions from one isolated process.

    Boots the shared test database and cache, fixes every worker in the same Valkey window, and
    calls the self-profile route so account and composite dimensions race at one atomic boundary.

    Arguments:
        case: Complete spawned-process workload.

    Returns:
        Number of requests admitted past authenticated-read throttling.

    Raises:
        RuntimeError: If Django cannot initialize or dispatch the profile route.
    """
    os.environ["POSTGRES_DB"] = case.database_name
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.testing")
    django.setup()

    settings_module = import_module("django.conf").settings
    settings_module.ALLOWED_HOSTS = ["testserver"]
    settings_module.API_BOUNDARY_ADDRESS_THROTTLE_RATE = "1000/minute"
    settings_module.API_AUTHENTICATED_READ_THROTTLE_RATE = f"{case.limit}/minute"
    throttle_module = cast("ThrottleModule", import_module("accounts.api_throttling"))
    throttle_module.TEST_SERVER_TIME_MILLISECONDS = case.fixed_time_milliseconds
    client_type = import_module("rest_framework.test").APIClient
    client = client_type()

    admitted = 0
    for _attempt in range(case.attempts):
        response = client.get(
            "/api/v1/users/me/",
            HTTP_AUTHORIZATION=case.authorization,
            REMOTE_ADDR=case.address,
        )
        if int(response.status_code) != HTTPStatus.TOO_MANY_REQUESTS:
            admitted += 1

    return admitted
