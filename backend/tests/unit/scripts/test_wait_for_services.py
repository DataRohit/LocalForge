"""Unit tests for the dependency readiness gate.

Covers the waiting loop, the documented exit codes, and the probe each service is checked with,
using a fabricated probe surface so no test requires a running service.
"""

import os
import runpy
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import amqp
import psycopg
import pytest
import redis
from scripts import wait_for_services

if TYPE_CHECKING:
    from collections.abc import Sequence

ENVIRONMENT = {
    "POSTGRES_DB": "localforge",
    "POSTGRES_USER": "localforge_app",
    "POSTGRES_PASSWORD": "probe-password",
    "POSTGRES_HOST": "postgres-pg3ka",
    "POSTGRES_PORT": "5432",
    "POSTGRES_REPLICA_HOST": "postgres-replica-pg6vy",
    "POSTGRES_REPLICA_PORT": "5432",
    "VALKEY_CACHE_HOST": "valkey-cache-vc5tn",
    "VALKEY_CACHE_PORT": "6379",
    "VALKEY_CACHE_PASSWORD": "probe-cache",
    "VALKEY_CHANNELS_HOST": "valkey-channels-vh8dm",
    "VALKEY_CHANNELS_PORT": "6379",
    "VALKEY_CHANNELS_PASSWORD": "probe-channels",
    "RABBITMQ_HOST": "rabbitmq-rq4sx",
    "RABBITMQ_PORT": "5672",
    "RABBITMQ_DEFAULT_USER": "localforge_broker",
    "RABBITMQ_DEFAULT_PASS": "probe-broker",
    "RABBITMQ_DEFAULT_VHOST": "localforge",
    "S3_ENDPOINT_URL": "http://seaweedfs-sw9cr:8333",
    "EMAIL_HOST": "mailpit-mp6gb",
    "MAILPIT_WEB_PORT": "8025",
}

ATTEMPTS_BEFORE_READY = 3
EXPECTED_ATTEMPTS = ATTEMPTS_BEFORE_READY + 1
DOCUMENTED_TIMEOUT_SECONDS = 120


@dataclass
class FakeProbes:
    """Probe surface that records calls and fails on demand.

    Stands in for the real client libraries so every branch of the waiting loop can be exercised
    without a service running. Inherits nothing; it satisfies the Probes surface structurally.

    Attributes:
        failures: Number of times each probe should fail before succeeding.
        calls: Description of every probe invocation, in order.

    Members:
        postgres: Record a database probe.
        valkey: Record a cache probe.
        rabbitmq: Record a broker probe.
        http: Record an HTTP probe.
    """

    failures: int = 0
    calls: list[str] = field(default_factory=list)

    def _record(self, description: str) -> None:
        """Record one probe call and fail while failures remain.

        Counts down the configured failures so a test can describe a service that becomes ready
        after a known number of attempts.

        Arguments:
            description: Text identifying the probe and its target.

        Returns:
            None.

        Raises:
            ConnectionError: While the configured failures have not been exhausted.
        """
        self.calls.append(description)

        if self.failures > 0:
            self.failures -= 1
            message = f"probe refused: {description}"
            raise ConnectionError(message)

    def postgres(self, host: str, port: int) -> None:
        """Record a database probe.

        Notes the node that was probed, so a test can assert the gate used the address from the
        environment rather than one of its own.

        Arguments:
            host: Host the node listens on.
            port: Port the node listens on.

        Returns:
            None.

        Raises:
            ConnectionError: While the configured failures have not been exhausted.
        """
        self._record(f"postgres {host}:{port}")

    def valkey(self, host: str, port: int, password: str) -> None:
        """Record a cache probe.

        Notes the instance and the credential length, so a test can assert the gate passed the
        password belonging to that instance without recording the secret itself.

        Arguments:
            host: Host the instance listens on.
            port: Port the instance listens on.
            password: Password the instance requires.

        Returns:
            None.

        Raises:
            ConnectionError: While the configured failures have not been exhausted.
        """
        self._record(f"valkey {host}:{port} {password}")

    def rabbitmq(self, host: str, port: int, user: str, password: str, virtual_host: str) -> None:
        """Record a broker probe.

        Notes the broker, the user, and the virtual host, so a test can assert every part of the
        handshake came from the environment inventory.

        Arguments:
            host: Host the broker listens on.
            port: Port the broker listens on.
            user: Broker user to authenticate as.
            password: Password for that user.
            virtual_host: Virtual host to open.

        Returns:
            None.

        Raises:
            ConnectionError: While the configured failures have not been exhausted.
        """
        self._record(f"rabbitmq {host}:{port} {user} {password} {virtual_host}")

    def http(self, url: str) -> None:
        """Record an HTTP probe.

        Notes the endpoint, so a test can assert the readiness path the gate builds matches the one
        the service actually serves.

        Arguments:
            url: Readiness endpoint to fetch.

        Returns:
            None.

        Raises:
            ConnectionError: While the configured failures have not been exhausted.
        """
        self._record(f"http {url}")


def _run(
    argv: Sequence[str],
    probes: FakeProbes,
    elapsed: list[float] | None = None,
) -> int:
    """Run the gate against fabricated probes and a controlled clock.

    Advances a fake clock only when the gate sleeps, so a test describes a timeout without waiting
    for one.

    Arguments:
        argv: Command-line arguments for the gate.
        probes: Probe surface the gate runs through.
        elapsed: Mutable single-element clock, created when not supplied.

    Returns:
        The exit code the gate returned.

    Raises:
        KeyError: If a variable the requested services need is absent.
    """
    clock = elapsed if elapsed is not None else [0.0]

    def sleep(seconds: float) -> None:
        """Advance the clock instead of waiting.

        Moves the fake clock forward by the requested interval, which is what lets a timeout be
        reached in no real time at all.

        Arguments:
            seconds: Interval the gate asked to wait.

        Returns:
            None.

        Raises:
            None.
        """
        clock[0] += seconds

    with patch.dict(os.environ, ENVIRONMENT, clear=True):
        return wait_for_services.main(argv, probes, sleep, lambda: clock[0])


@pytest.mark.unit
def test_a_ready_service_passes_on_the_first_attempt() -> None:
    """Report success as soon as a service answers.

    Confirms a service that is already ready is probed once and reported ready, so the gate costs
    nothing when the stack is already up.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the gate does not succeed after one probe.
    """
    probes = FakeProbes()

    assert _run(["postgres"], probes) == wait_for_services.EXIT_OK
    assert probes.calls == ["postgres postgres-pg3ka:5432"]


@pytest.mark.unit
def test_a_service_that_starts_late_is_waited_for() -> None:
    """Keep waiting while a service is still starting.

    Confirms the gate retries until the service answers, which is the whole reason it exists rather
    than a single connection attempt.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the gate gives up before the service becomes ready.
    """
    probes = FakeProbes(failures=ATTEMPTS_BEFORE_READY)

    assert _run(["postgres", "--timeout", "60"], probes) == wait_for_services.EXIT_OK
    assert len(probes.calls) == EXPECTED_ATTEMPTS


@pytest.mark.unit
def test_a_service_that_never_arrives_reports_the_timeout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Give up on a service that never answers.

    Confirms the documented timeout code is returned and the message names the service and the last
    error, because an operator reading a failed start needs both.

    Arguments:
        capsys: Capture fixture supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code or the message is wrong.
    """
    probes = FakeProbes(failures=1000)

    code = _run(["valkey-cache", "--timeout", "10"], probes)
    printed = capsys.readouterr().out

    assert code == wait_for_services.EXIT_TIMEOUT
    assert "timed out waiting for valkey-cache" in printed
    assert "ConnectionError" in printed


@pytest.mark.unit
def test_every_service_shares_one_deadline() -> None:
    """Bound the whole gate rather than each service.

    Confirms a slow first service consumes the budget the later ones would have had, so the total
    wait matches the timeout an operator configured.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the gate does not stop once the shared deadline passes.
    """
    probes = FakeProbes(failures=1000)

    code = _run(["postgres", "valkey-cache"], probes, [0.0])

    assert code == wait_for_services.EXIT_TIMEOUT
    assert all(call.startswith("postgres") for call in probes.calls)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("postgres", "postgres postgres-pg3ka:5432"),
        ("postgres-replica", "postgres postgres-replica-pg6vy:5432"),
        ("valkey-cache", "valkey valkey-cache-vc5tn:6379 probe-cache"),
        ("valkey-channels", "valkey valkey-channels-vh8dm:6379 probe-channels"),
        (
            "rabbitmq",
            "rabbitmq rabbitmq-rq4sx:5672 localforge_broker probe-broker localforge",
        ),
        ("seaweedfs", "http http://seaweedfs-sw9cr:8333/healthz"),
        ("mailpit", "http http://mailpit-mp6gb:8025/readyz"),
    ],
)
def test_each_service_is_probed_at_its_documented_address(service: str, expected: str) -> None:
    """Probe each service the way its own protocol requires.

    Confirms every registered service is checked at the address the environment inventory gives it,
    with the readiness path the service inventory records rather than a plain socket connect.

    Arguments:
        service: Service name under test.
        expected: Description the fabricated probe records for it.

    Returns:
        None.

    Raises:
        AssertionError: If the service is probed differently.
    """
    probes = FakeProbes()

    assert _run([service], probes) == wait_for_services.EXIT_OK
    assert probes.calls == [expected]


@pytest.mark.unit
def test_the_registry_covers_every_name_the_parser_accepts() -> None:
    """Keep the parser and the registry in step.

    Confirms every accepted service name has a check behind it, since a name the parser allows but
    the registry lacks would fail with a lookup error rather than a readiness failure.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a name has no check.
    """
    with patch.dict(os.environ, ENVIRONMENT, clear=True):
        services = wait_for_services.build_services(FakeProbes())

    assert set(services) == set(wait_for_services.SERVICE_NAMES)


@pytest.mark.unit
def test_an_unknown_service_is_refused() -> None:
    """Refuse a service the platform does not define.

    Confirms an unrecognised name fails at argument parsing, so a typo in the entrypoint surfaces
    immediately rather than as a missing dependency later.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the unknown name is accepted.
    """
    with pytest.raises(SystemExit):
        wait_for_services.build_parser().parse_args(["not-a-service"])


@pytest.mark.unit
def test_the_default_timeout_matches_the_documented_contract() -> None:
    """Wait for the documented number of seconds by default.

    Confirms the default timeout is the one the script contract records, so the entrypoint and an
    operator running the gate by hand behave identically.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the default differs from the contract.
    """
    module_root = Path(wait_for_services.__file__).resolve()
    repository_root = next(parent for parent in module_root.parents if (parent / "docs").is_dir())
    document = (repository_root / "docs" / "platform" / "conventions.md").read_text(
        encoding="utf-8"
    )

    timeout = wait_for_services.build_parser().parse_args(["postgres"]).timeout

    assert timeout == DOCUMENTED_TIMEOUT_SECONDS
    assert f"`--timeout`, default {DOCUMENTED_TIMEOUT_SECONDS}" in document


@pytest.mark.unit
def test_the_script_guard_runs_the_entry_point() -> None:
    """Run the gate as a script.

    Executes the module under the name Python gives a directly executed script and confirms it
    surfaces the exit code, which is the path the container entrypoint takes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the script guard does not surface the exit code.
    """
    module_path = Path(str(wait_for_services.__file__))

    with (
        patch.object(sys, "argv", ["wait_for_services.py", "not-a-service"]),
        pytest.raises(SystemExit) as raised,
    ):
        runpy.run_path(str(module_path), run_name="__main__")

    assert raised.value.code != wait_for_services.EXIT_OK


@pytest.mark.unit
def test_the_database_probe_issues_a_statement() -> None:
    """Ask the database to answer a query.

    Confirms the real probe connects with the inventory credentials and runs a statement, which is
    what distinguishes a ready server from one that merely accepts a socket.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not connect and query.
    """
    connection = MagicMock()
    connect = MagicMock()
    connect.return_value.__enter__.return_value = connection

    with (
        patch.dict(os.environ, ENVIRONMENT, clear=True),
        patch.object(psycopg, "connect", connect),
    ):
        wait_for_services.RealProbes().postgres("postgres-pg3ka", 5432)

    assert connect.call_args.kwargs["host"] == "postgres-pg3ka"
    assert connect.call_args.kwargs["user"] == ENVIRONMENT["POSTGRES_USER"]
    connection.execute.assert_called_once_with("SELECT 1")


@pytest.mark.unit
def test_the_cache_probe_pings_and_closes() -> None:
    """Ask the cache to answer a ping.

    Confirms the real probe authenticates, pings, and closes the client, so a probe leaves no
    connection behind on a service that is about to be used properly.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not ping or does not close.
    """
    client = MagicMock()

    with patch.object(redis, "Redis", return_value=client) as factory:
        wait_for_services.RealProbes().valkey("valkey-cache-vc5tn", 6379, "probe-cache")

    assert factory.call_args.kwargs["password"] == ENVIRONMENT["VALKEY_CACHE_PASSWORD"]
    client.ping.assert_called_once_with()
    client.close.assert_called_once_with()


@pytest.mark.unit
def test_the_broker_probe_completes_a_handshake() -> None:
    """Ask the broker to complete a handshake.

    Confirms the real probe opens and closes an AMQP connection against the configured virtual
    host, which proves the broker finished booting rather than only bound its port.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not connect or does not close.
    """
    connection = MagicMock()

    with patch.object(amqp, "Connection", return_value=connection) as factory:
        wait_for_services.RealProbes().rabbitmq(
            "rabbitmq-rq4sx",
            5672,
            "localforge_broker",
            "probe-broker",
            "localforge",
        )

    assert factory.call_args.kwargs["virtual_host"] == "localforge"
    connection.connect.assert_called_once_with()
    connection.close.assert_called_once_with()


@pytest.mark.unit
def test_the_http_probe_reads_the_readiness_endpoint() -> None:
    """Ask an HTTP service for its readiness endpoint.

    Confirms the real probe fetches the endpoint and reads the body, so a service answering with an
    error is not mistaken for a working one.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the endpoint is not fetched and read.
    """
    response = MagicMock()
    opener = MagicMock()
    opener.return_value.__enter__.return_value = response

    with patch.object(urllib.request, "urlopen", opener):
        wait_for_services.RealProbes().http("http://seaweedfs-sw9cr:8333/healthz")

    assert opener.call_args.args[0].full_url == "http://seaweedfs-sw9cr:8333/healthz"
    response.read.assert_called_once_with()
