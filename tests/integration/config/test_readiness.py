"""Integration tests for application readiness.

Exercises the public health route against the configured development dependencies, proving load
balancers receive bounded machine-readable readiness without infrastructure details.
"""

import secrets
import smtplib
import threading
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from django.conf import settings
from django.test import Client, override_settings
from redis.exceptions import TimeoutError as RedisTimeoutError

import config.health as health_module
from config.api import ErrorCode
from config.health import ReadinessCoordinatorState
from config.logs import REQUEST_ID_HEADER

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

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
CONCURRENT_HEALTH_REQUESTS = 8
CONCURRENT_HEALTH_TIMEOUT_SECONDS = 15


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
def test_health_route_coalesces_overlapping_pollers(
    mocker: MockerFixture,
) -> None:
    """Return one shared ready result to concurrent health pollers.

    Starts eight requests together with stale mail evidence, reproducing Docker and Traefik overlap
    while requiring one aggregate dependency collection and no capacity-shaped 503 response.

    Arguments:
        mocker: Fixture isolating coordinator and mail cache state while spying on collection.

    Returns:
        None.

    Raises:
        AssertionError: If requests duplicate collection or report false dependency unavailability.
    """
    state = ReadinessCoordinatorState()
    mocker.patch.object(health_module, "readiness_coordinator_state", state)
    mocker.patch.object(health_module.mail_readiness_state, "success_at", None)
    collect = mocker.spy(health_module, "_collect_readiness")
    barrier = threading.Barrier(CONCURRENT_HEALTH_REQUESTS)

    def request_health() -> tuple[int, dict[str, Any]]:
        """Request readiness after every polling thread is prepared.

        Uses an independent Django client per thread and returns only after the barrier creates the
        overlapping load-balancer request pattern.

        Arguments:
            None.

        Returns:
            HTTP status and parsed readiness body.

        Raises:
            threading.BrokenBarrierError: If pollers do not become concurrent.
        """
        barrier.wait(timeout=CONCURRENT_HEALTH_TIMEOUT_SECONDS)
        response = Client().get("/health/", headers={"accept": "application/json"})
        return response.status_code, cast("dict[str, Any]", response.json())

    with ThreadPoolExecutor(max_workers=CONCURRENT_HEALTH_REQUESTS) as executor:
        results = list(
            executor.map(
                lambda _index: request_health(),
                range(CONCURRENT_HEALTH_REQUESTS),
            )
        )

    assert collect.call_count == 1
    assert {status for status, _payload in results} == {HTTPStatus.OK}
    assert all(
        payload["checks"] == dict.fromkeys(sorted(EXPECTED_CHECKS), "working")
        for _, payload in results
    )


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
    mocker.patch("config.health.mail_readiness_state.success_at", None)
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
def test_health_observed_statuses_exactly_match_its_documented_contract(
    client: Client,
    mocker: MockerFixture,
) -> None:
    """Compare every empirically reachable health status with OpenAPI.

    Exercises ready, degraded, method, and negotiation outcomes through the public route, compares
    framework bodies with their examples, and requires the observed and documented status sets.

    Arguments:
        client: Django client supplied by the test framework.
        mocker: Fixture inducing one unexpected dependency-check failure.

    Returns:
        None.

    Raises:
        AssertionError: If observed status, body, documentation, or correlation changes.
    """
    ready = client.get("/health/", headers={"accept": "application/json"})
    caches = cast("dict[str, dict[str, object]]", settings.CACHES)
    unreachable = {
        **caches,
        "default": {
            **caches["default"],
            "LOCATION": "redis://127.0.0.1:1/0",
        },
    }
    with override_settings(CACHES=unreachable):
        degraded = client.get("/health/", headers={"accept": "application/json"})
    unsupported_method = client.post("/health/")
    unsupported_representation = client.get(
        "/health/",
        headers={"accept": "text/plain"},
    )
    with override_settings(ALLOWED_HOSTS=["allowed.test"]):
        invalid_host = client.get("/health/", HTTP_HOST="invalid host")
    redis_from_url = mocker.patch(
        "config.health.Redis.from_url",
        side_effect=RuntimeError("private health failure"),
    )
    client.raise_request_exception = False
    unexpected = client.get("/health/", headers={"accept": "application/json"})
    mocker.stop(redis_from_url)
    method_payload = cast("dict[str, object]", unsupported_method.json())
    representation_payload = cast("dict[str, object]", unsupported_representation.json())
    unexpected_payload = cast("dict[str, object]", unexpected.json())
    documented_method = _documented_health_example(HTTPStatus.METHOD_NOT_ALLOWED)
    documented_representation = _documented_health_example(HTTPStatus.NOT_ACCEPTABLE)
    documented_unexpected = _documented_health_example(HTTPStatus.INTERNAL_SERVER_ERROR)
    generator_factory = cast(
        "Callable[[], SchemaGeneratorProtocol]",
        import_module("drf_spectacular.generators").SchemaGenerator,
    )
    schema = generator_factory().get_schema(request=None, public=True)
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])["/health/"]["get"]["responses"],
        )
    }
    observed = {
        response.status_code
        for response in (
            ready,
            degraded,
            unsupported_method,
            unsupported_representation,
            invalid_host,
            unexpected,
        )
    }

    assert observed == documented
    assert unsupported_method.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert unsupported_representation.status_code == HTTPStatus.NOT_ACCEPTABLE
    assert invalid_host.status_code == HTTPStatus.BAD_REQUEST
    assert unexpected.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert method_payload == documented_method | {
        "request_id": method_payload["request_id"],
    }
    assert representation_payload == documented_representation | {
        "request_id": representation_payload["request_id"],
    }
    assert unexpected_payload == documented_unexpected | {
        "request_id": unexpected_payload["request_id"],
    }
    assert method_payload["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert representation_payload["code"] == ErrorCode.NOT_ACCEPTABLE
    assert unexpected_payload["code"] == ErrorCode.INTERNAL_SERVER_ERROR
    assert method_payload["request_id"] == unsupported_method.headers[REQUEST_ID_HEADER]
    assert (
        representation_payload["request_id"]
        == unsupported_representation.headers[REQUEST_ID_HEADER]
    )
    assert unexpected_payload["request_id"] == unexpected.headers[REQUEST_ID_HEADER]
    assert "private health failure" not in unexpected.content.decode()


@pytest.mark.integration
@pytest.mark.services(
    "postgres",
    "valkey-cache",
    "valkey-channels",
    "rabbitmq",
    "seaweedfs",
)
def test_health_head_observed_statuses_exactly_match_its_documented_contract(
    client: Client,
    mocker: MockerFixture,
) -> None:
    """Compare every reachable health HEAD status with its bodyless OpenAPI contract.

    Exercises ready, degraded, invalid-host, negotiation, and unexpected-failure outcomes through
    the implicit safe method and requires every wire response to suppress its representation.

    Arguments:
        client: Django client issuing health HEAD requests.
        mocker: Fixture inducing one unexpected dependency-check failure.

    Returns:
        None.

    Raises:
        AssertionError: If a status drifts or any HEAD response returns a body.
    """
    ready = client.head("/health/", headers={"accept": "application/json"})
    caches = cast("dict[str, dict[str, object]]", settings.CACHES)
    unreachable = {
        **caches,
        "default": {
            **caches["default"],
            "LOCATION": "redis://127.0.0.1:1/0",
        },
    }
    with override_settings(CACHES=unreachable):
        degraded = client.head("/health/", headers={"accept": "application/json"})
    with override_settings(ALLOWED_HOSTS=["allowed.test"]):
        invalid_host = client.head("/health/", HTTP_HOST="invalid host")
    unsupported_representation = client.head(
        "/health/",
        headers={"accept": "text/plain"},
    )
    redis_from_url = mocker.patch(
        "config.health.Redis.from_url",
        side_effect=RuntimeError("private health HEAD failure"),
    )
    client.raise_request_exception = False
    unexpected = client.head("/health/", headers={"accept": "application/json"})
    mocker.stop(redis_from_url)
    responses = (
        ready,
        invalid_host,
        unsupported_representation,
        unexpected,
        degraded,
    )
    generator_factory = cast(
        "Callable[[], SchemaGeneratorProtocol]",
        import_module("drf_spectacular.generators").SchemaGenerator,
    )
    schema = generator_factory().get_schema(request=None, public=True)
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])["/health/"]["head"]["responses"],
        )
    }

    assert {response.status_code for response in responses} == documented
    assert all(response.content == b"" for response in responses)
    assert all(
        response.headers["Content-Type"].startswith("application/json") for response in responses
    )
