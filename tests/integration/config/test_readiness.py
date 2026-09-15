"""Integration tests for application readiness.

Exercises the public health route against the configured development dependencies, proving load
balancers receive bounded machine-readable readiness without infrastructure details.
"""

import secrets
import smtplib
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from django.conf import settings
from django.test import override_settings
from redis.exceptions import TimeoutError as RedisTimeoutError

from config.api import ErrorCode
from config.logs import REQUEST_ID_HEADER

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from django.test import Client
    from pytest_mock import MockerFixture

    from accounts.models import User

    class SchemaGeneratorProtocol(Protocol):
        """Describe the schema generation operation used by contract tests.

        Inherits from ``Protocol`` and exposes only public OpenAPI document generation, keeping
        strict test typing independent of the untyped schema implementation.

        Attributes:
            None.

        Members:
            get_schema: Generate the current public OpenAPI document.
        """

        def get_schema(self, *, request: None, public: bool) -> dict[str, object]:
            """Generate the public route document.

            Builds the schema without an incoming request so the test can inspect stable examples.
            Exposes no implementation-specific schema generator behavior.

            Arguments:
                request: Optional schema request, unused by this test.
                public: Whether every public operation is included.

            Returns:
                Generated OpenAPI document.

            Raises:
                None.
            """
            ...


EXPECTED_CHECKS = {
    "broker",
    "cache",
    "channel_layer",
    "database_primary",
    "database_replica",
    "mail",
    "object_storage",
}


def _documented_health_example(status: HTTPStatus) -> dict[str, object]:
    """Read the single documented JSON example for one health error.

    Generates the public OpenAPI document and selects the example bound to the requested status,
    making observed behavior fail whenever its documented body drifts.

    Arguments:
        status: Health response status whose example is required.

    Returns:
        Documented JSON response example.

    Raises:
        AssertionError: If the response does not carry exactly one example.
    """
    generator_factory = cast(
        "Callable[[], SchemaGeneratorProtocol]",
        import_module("drf_spectacular.generators").SchemaGenerator,
    )
    schema = generator_factory().get_schema(request=None, public=True)
    operation = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", schema["paths"])["/health/"]["get"],
    )
    response = cast("dict[str, Any]", operation["responses"][str(status.value)])
    content = cast("dict[str, Any]", response["content"])["application/json"]
    examples = cast("dict[str, Any]", content["examples"])

    assert len(examples) == 1

    return cast("dict[str, object]", next(iter(examples.values()))["value"])


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
    """Match observed framework errors to their documented correlated envelopes.

    Exercises both statuses reachable before dependency evaluation and compares each body with its
    OpenAPI example while proving the response body and header share one request identifier.

    Arguments:
        client: Django client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If observed status, body, documentation, or correlation changes.
    """
    unsupported_method = client.post("/health/")
    unsupported_representation = client.get(
        "/health/",
        headers={"accept": "text/plain"},
    )
    method_payload = cast("dict[str, object]", unsupported_method.json())
    representation_payload = cast("dict[str, object]", unsupported_representation.json())
    documented_method = _documented_health_example(HTTPStatus.METHOD_NOT_ALLOWED)
    documented_representation = _documented_health_example(HTTPStatus.NOT_ACCEPTABLE)

    assert unsupported_method.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert unsupported_representation.status_code == HTTPStatus.NOT_ACCEPTABLE
    assert method_payload == documented_method | {
        "request_id": method_payload["request_id"],
    }
    assert representation_payload == documented_representation | {
        "request_id": representation_payload["request_id"],
    }
    assert method_payload["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert representation_payload["code"] == ErrorCode.NOT_ACCEPTABLE
    assert method_payload["request_id"] == unsupported_method.headers[REQUEST_ID_HEADER]
    assert (
        representation_payload["request_id"]
        == unsupported_representation.headers[REQUEST_ID_HEADER]
    )
