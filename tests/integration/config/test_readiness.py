"""Integration tests for application readiness.

Exercises the public health route against the configured development dependencies, proving load
balancers receive bounded machine-readable readiness without infrastructure details.
"""

import secrets
import smtplib
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from django.conf import settings
from django.test import override_settings
from redis.exceptions import TimeoutError as RedisTimeoutError

if TYPE_CHECKING:
    from django.test import Client
    from pytest_mock import MockerFixture

    from accounts.models import User

EXPECTED_CHECKS = {
    "broker",
    "cache",
    "channel_layer",
    "database_primary",
    "database_replica",
    "mail",
    "object_storage",
}


@pytest.mark.integration
@pytest.mark.services(
    "postgres",
    "valkey-cache",
    "valkey-channels",
    "rabbitmq",
    "seaweedfs",
)
@pytest.mark.django_db(transaction=True)
def test_health_route_reports_every_required_dependency(client: Client) -> None:
    """Report ready only when every configured dependency responds.

    Calls the public route through Django's complete middleware stack and validates the stable
    load-balancer body without accepting hostnames, versions, or diagnostic messages.

    Arguments:
        client: Django client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the healthy response omits a dependency or leaks diagnostics.
    """
    response = client.get("/health/", headers={"accept": "application/json"})
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.OK
    assert payload == {
        "status": "ready",
        "liveness": "alive",
        "readiness": "ready",
        "checks": dict.fromkeys(sorted(EXPECTED_CHECKS), "working"),
    }


@pytest.mark.integration
@pytest.mark.services(
    "postgres",
    "valkey-cache",
    "valkey-channels",
    "rabbitmq",
    "seaweedfs",
)
@pytest.mark.django_db(transaction=True)
def test_health_route_reports_a_degraded_dependency(client: Client) -> None:
    """Return service unavailable when one required dependency cannot respond.

    Repoints only the cache probe at a refused local port, proving status and per-dependency state
    change without exposing the failed hostname or client exception.

    Arguments:
        client: Django client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If degradation returns success, hides the failed check, or leaks details.
    """
    caches = cast("dict[str, dict[str, object]]", settings.CACHES)
    unreachable = {
        **caches,
        "default": {
            **caches["default"],
            "LOCATION": "redis://127.0.0.1:1/0",
        },
    }

    with override_settings(CACHES=unreachable):
        response = client.get("/health/", headers={"accept": "application/json"})

    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["status"] == "not_ready"
    assert payload["readiness"] == "not_ready"
    assert payload["liveness"] == "alive"
    assert payload["checks"]["cache"] == "unavailable"
    assert set(payload["checks"]) == EXPECTED_CHECKS
    assert "127.0.0.1" not in response.content.decode()
    assert "details" not in payload


@pytest.mark.integration
@pytest.mark.services(
    "postgres",
    "valkey-cache",
    "valkey-channels",
    "rabbitmq",
    "seaweedfs",
)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_staff_health_response_includes_only_bounded_diagnostics(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Expose bounded dependency diagnostics only to authenticated staff.

    Authenticates through the configured session backend and verifies details contain duration and
    generic state without infrastructure addresses, versions, credentials, or raw exceptions.

    Arguments:
        client: Django client supplied by the test framework.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If staff detail is absent, unbounded, or contains infrastructure data.
    """
    user = django_user_model.objects.create_user(
        username="health-operator",
        email="health-operator@example.invalid",
        password=secrets.token_urlsafe(16),
        is_active=True,
        is_staff=True,
    )
    client.force_login(user)

    response = client.get("/health/", headers={"accept": "application/json"})
    payload = cast("dict[str, Any]", response.json())
    details = cast("dict[str, dict[str, object]]", payload["details"])

    assert response.status_code == HTTPStatus.OK
    assert set(details) == EXPECTED_CHECKS
    assert all(set(detail) == {"status", "duration_ms", "error"} for detail in details.values())
    assert all(detail["status"] == "working" for detail in details.values())
    assert all(detail["error"] is None for detail in details.values())
    assert all(isinstance(detail["duration_ms"], float) for detail in details.values())
    rendered = response.content.decode()
    assert settings.DATABASES["default"]["HOST"] not in rendered
    assert settings.CELERY_BROKER_URL not in rendered


@pytest.mark.integration
@pytest.mark.services(
    "postgres",
    "valkey-cache",
    "valkey-channels",
    "rabbitmq",
    "seaweedfs",
)
@pytest.mark.django_db(transaction=True)
def test_health_route_contains_valkey_cleanup_failure(
    client: Client,
    mocker: MockerFixture,
) -> None:
    """Return bounded JSON degradation when a Valkey client cannot close.

    Lets both pings succeed before cleanup times out, proving dependency cleanup failure becomes
    readiness 503 rather than escaping as a framework 500 response.

    Arguments:
        client: Django client supplied by the test framework.
        mocker: Fixture replacing short-lived readiness clients.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup failure escapes or exposes its raw diagnostic.
    """
    redis_client = AsyncMock()
    redis_client.ping.return_value = True
    redis_client.aclose.side_effect = RedisTimeoutError("cleanup diagnostic")
    mocker.patch("config.health.Redis.from_url", return_value=redis_client)

    response = client.get("/health/", headers={"accept": "application/json"})
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["checks"]["cache"] == "unavailable"
    assert payload["checks"]["channel_layer"] == "unavailable"
    assert "cleanup diagnostic" not in response.content.decode()


@pytest.mark.integration
@pytest.mark.services(
    "postgres",
    "valkey-cache",
    "valkey-channels",
    "rabbitmq",
    "seaweedfs",
)
@pytest.mark.django_db(transaction=True)
def test_health_route_contains_mail_cleanup_failure(
    client: Client,
    mocker: MockerFixture,
) -> None:
    """Return bounded JSON degradation when the mail backend cannot close.

    Opens the configured backend successfully before close raises, proving cleanup errors use the
    same stable unavailable state as connection failures.

    Arguments:
        client: Django client supplied by the test framework.
        mocker: Fixture replacing the mail backend connection.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup failure escapes or exposes its raw diagnostic.
    """
    connection = Mock()
    connection.close.side_effect = smtplib.SMTPException("cleanup diagnostic")
    mocker.patch("config.health.get_connection", return_value=connection)

    response = client.get("/health/", headers={"accept": "application/json"})
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["checks"]["mail"] == "unavailable"
    assert "cleanup diagnostic" not in response.content.decode()


@pytest.mark.integration
@pytest.mark.services(
    "postgres",
    "valkey-cache",
    "valkey-channels",
    "rabbitmq",
    "seaweedfs",
)
def test_health_route_rejects_unsupported_methods_and_representations(client: Client) -> None:
    """Return the framework's bounded method and representation errors.

    Exercises the additional statuses reachable before dependency evaluation so the OpenAPI
    contract remains complete for the public route.

    Arguments:
        client: Django client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If either reachable framework status changes.
    """
    unsupported_method = client.post("/health/")
    unsupported_representation = client.get(
        "/health/",
        headers={"accept": "text/plain"},
    )

    assert unsupported_method.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert unsupported_representation.status_code == HTTPStatus.NOT_ACCEPTABLE
