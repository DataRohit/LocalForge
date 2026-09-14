"""Dependency readiness gate for the application container.

Blocks until every named service answers a real readiness probe rather than merely accepting a
socket, so a container that reports healthy has genuinely working dependencies behind it.
"""

import argparse
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from os import environ
from typing import Protocol

import amqp
import psycopg
import redis

DEFAULT_TIMEOUT_SECONDS = 120
RETRY_INTERVAL_SECONDS = 2
PROBE_TIMEOUT_SECONDS = 5

EXIT_OK = 0
EXIT_TIMEOUT = 1

POSTGRES = "postgres"
POSTGRES_REPLICA = "postgres-replica"
VALKEY_CACHE = "valkey-cache"
VALKEY_CHANNELS = "valkey-channels"
RABBITMQ = "rabbitmq"
SEAWEEDFS = "seaweedfs"
MAILPIT = "mailpit"

SERVICE_NAMES = (
    POSTGRES,
    POSTGRES_REPLICA,
    VALKEY_CACHE,
    VALKEY_CHANNELS,
    RABBITMQ,
    SEAWEEDFS,
    MAILPIT,
)


class Probes(Protocol):
    """Readiness probe surface for every service the application depends on.

    Isolates each protocol-level check behind one method, so the waiting loop can be exercised
    against a fabricated set of services without any of them running. Inherits Protocol, so any
    object providing these methods satisfies it structurally.

    Members:
        postgres: Run a trivial statement against a PostgreSQL node.
        valkey: Ping a Valkey instance.
        rabbitmq: Complete an AMQP handshake with the broker.
        http: Fetch a readiness endpoint over HTTP.
    """

    def postgres(self, host: str, port: int) -> None:
        """Run a trivial statement against a PostgreSQL node.

        Connects with the configured credentials and issues one statement, which proves the server
        is accepting queries rather than merely listening.

        Arguments:
            host: Host the node listens on.
            port: Port the node listens on.

        Returns:
            None.

        Raises:
            Exception: If the node cannot be reached or refuses the statement.
        """

    def valkey(self, host: str, port: int, password: str) -> None:
        """Ping a Valkey instance.

        Authenticates and issues a ping, which proves the instance is past loading and ready to
        serve rather than only bound to its port.

        Arguments:
            host: Host the instance listens on.
            port: Port the instance listens on.
            password: Password the instance requires.

        Returns:
            None.

        Raises:
            Exception: If the instance cannot be reached or rejects the credential.
        """

    def rabbitmq(self, host: str, port: int, user: str, password: str, virtual_host: str) -> None:
        """Complete an AMQP handshake with the broker.

        Opens and closes a connection, which proves the broker has finished booting its virtual
        host rather than only accepting TCP.

        Arguments:
            host: Host the broker listens on.
            port: Port the broker listens on.
            user: Broker user to authenticate as.
            password: Password for that user.
            virtual_host: Virtual host to open.

        Returns:
            None.

        Raises:
            Exception: If the handshake fails.
        """

    def http(self, url: str) -> None:
        """Fetch a readiness endpoint over HTTP.

        Requests the endpoint and discards the body, treating any non-success response as not ready
        so a service that answers with an error is not mistaken for a working one.

        Arguments:
            url: Readiness endpoint to fetch.

        Returns:
            None.

        Raises:
            Exception: If the endpoint cannot be fetched or answers with an error.
        """


class RealProbes:
    """Readiness probes backed by the real client libraries.

    Implements the Probes surface using the same drivers the application itself uses, so a probe
    that passes proves the application's own client can connect. Inherits nothing; it satisfies
    Probes structurally.

    Members:
        postgres: Run a trivial statement against a PostgreSQL node.
        valkey: Ping a Valkey instance.
        rabbitmq: Complete an AMQP handshake with the broker.
        http: Fetch a readiness endpoint over HTTP.
    """

    def postgres(self, host: str, port: int) -> None:
        """Run a trivial statement against a PostgreSQL node.

        Connects with the credentials from the environment and issues one statement, closing the
        connection immediately so the probe leaves nothing behind.

        Arguments:
            host: Host the node listens on.
            port: Port the node listens on.

        Returns:
            None.

        Raises:
            Exception: If the node cannot be reached or refuses the statement.
        """
        with psycopg.connect(
            host=host,
            port=port,
            dbname=environ["POSTGRES_DB"],
            user=environ["POSTGRES_USER"],
            password=environ["POSTGRES_PASSWORD"],
            connect_timeout=PROBE_TIMEOUT_SECONDS,
            options=f"-c statement_timeout={PROBE_TIMEOUT_SECONDS * 1000}",
        ) as connection:
            connection.execute("SELECT 1")

    def valkey(self, host: str, port: int, password: str) -> None:
        """Ping a Valkey instance.

        Authenticates with the supplied password and issues a ping, closing the client so the probe
        holds no connection open.

        Arguments:
            host: Host the instance listens on.
            port: Port the instance listens on.
            password: Password the instance requires.

        Returns:
            None.

        Raises:
            Exception: If the instance cannot be reached or rejects the credential.
        """
        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
            socket_timeout=PROBE_TIMEOUT_SECONDS,
        )
        try:
            client.ping()
        finally:
            client.close()

    def rabbitmq(self, host: str, port: int, user: str, password: str, virtual_host: str) -> None:
        """Complete an AMQP handshake with the broker.

        Opens a connection with the broker credentials and closes it, which is the cheapest check
        that proves the virtual host is available.

        Arguments:
            host: Host the broker listens on.
            port: Port the broker listens on.
            user: Broker user to authenticate as.
            password: Password for that user.
            virtual_host: Virtual host to open.

        Returns:
            None.

        Raises:
            Exception: If the handshake fails.
        """
        connection = amqp.Connection(
            host=f"{host}:{port}",
            userid=user,
            password=password,
            virtual_host=virtual_host,
            connect_timeout=PROBE_TIMEOUT_SECONDS,
            read_timeout=PROBE_TIMEOUT_SECONDS,
            write_timeout=PROBE_TIMEOUT_SECONDS,
        )
        try:
            connection.connect()
        finally:
            connection.close()

    def http(self, url: str) -> None:
        """Fetch a readiness endpoint over HTTP.

        Requests the endpoint with a bounded timeout and discards the body, so a slow service costs
        one interval rather than blocking the whole gate.

        Arguments:
            url: Readiness endpoint to fetch.

        Returns:
            None.

        Raises:
            Exception: If the endpoint cannot be fetched or answers with an error.
        """
        request = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310
            response.read()


@dataclass(frozen=True)
class Service:
    """One dependency the gate waits for.

    Pairs the registry name an operator asks for with the check that proves it ready, so the
    waiting loop treats every service identically.

    Attributes:
        name: Name the service is requested by.
        check: Callable that raises while the service is not ready.

    Members:
        None beyond those the dataclass generates.
    """

    name: str
    check: Callable[[], None]


def build_services(probes: Probes) -> Mapping[str, Service]:
    """Build the checks for every service the platform defines.

    Reads each host, port, and credential from the environment inventory so the gate follows the
    environment file it was started with rather than carrying an address of its own.

    Arguments:
        probes: Probe surface the checks run through.

    Returns:
        Service name mapped to the service it describes.

    Raises:
        KeyError: If a variable the requested services need is absent.
    """
    return {
        POSTGRES: Service(
            POSTGRES,
            lambda: probes.postgres(environ["POSTGRES_HOST"], int(environ["POSTGRES_PORT"])),
        ),
        POSTGRES_REPLICA: Service(
            POSTGRES_REPLICA,
            lambda: probes.postgres(
                environ["POSTGRES_REPLICA_HOST"],
                int(environ["POSTGRES_REPLICA_PORT"]),
            ),
        ),
        VALKEY_CACHE: Service(
            VALKEY_CACHE,
            lambda: probes.valkey(
                environ["VALKEY_CACHE_HOST"],
                int(environ["VALKEY_CACHE_PORT"]),
                environ["VALKEY_CACHE_PASSWORD"],
            ),
        ),
        VALKEY_CHANNELS: Service(
            VALKEY_CHANNELS,
            lambda: probes.valkey(
                environ["VALKEY_CHANNELS_HOST"],
                int(environ["VALKEY_CHANNELS_PORT"]),
                environ["VALKEY_CHANNELS_PASSWORD"],
            ),
        ),
        RABBITMQ: Service(
            RABBITMQ,
            lambda: probes.rabbitmq(
                environ["RABBITMQ_HOST"],
                int(environ["RABBITMQ_PORT"]),
                environ["RABBITMQ_DEFAULT_USER"],
                environ["RABBITMQ_DEFAULT_PASS"],
                environ["RABBITMQ_DEFAULT_VHOST"],
            ),
        ),
        SEAWEEDFS: Service(
            SEAWEEDFS,
            lambda: probes.http(f"{environ['S3_ENDPOINT_URL'].rstrip('/')}/healthz"),
        ),
        MAILPIT: Service(
            MAILPIT,
            lambda: probes.http(
                f"http://{environ['EMAIL_HOST']}:{environ['MAILPIT_WEB_PORT']}/readyz"
            ),
        ),
    }


def wait_for(
    service: Service,
    deadline: float,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> str | None:
    """Wait for one service to answer its readiness probe.

    Retries the check until it succeeds or the deadline passes, never sleeping past the deadline,
    so a service that is still starting costs a few seconds and one that never answers costs no
    more than the budget it was given.

    Arguments:
        service: Service to wait for.
        deadline: Monotonic time after which waiting stops.
        sleep: Callable that pauses between attempts.
        now: Callable returning the current monotonic time.

    Returns:
        None when the service became ready, or the last error text when the deadline passed.

    Raises:
        None.
    """
    last_error = "no attempt completed"

    while True:
        try:
            service.check()
        except Exception as error:  # noqa: BLE001
            last_error = f"{type(error).__name__}: {error}".strip()
        else:
            return None

        remaining = deadline - now()
        if remaining <= 0:
            return last_error

        sleep(min(RETRY_INTERVAL_SECONDS, remaining))


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Declares the service list and the timeout, keeping the contract in one place so the entrypoint
    and an operator invoke the gate the same way.

    Arguments:
        None.

    Returns:
        The configured parser.

    Raises:
        None.
    """
    parser = argparse.ArgumentParser(description="Wait for platform services to become ready.")
    parser.add_argument("services", nargs="+", choices=SERVICE_NAMES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)

    return parser


def main(
    argv: Sequence[str] | None = None,
    probes: Probes | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> int:
    """Wait for every requested service and report the result.

    Waits for each service in turn against one shared deadline, printing which service is being
    waited for and, on failure, the last error it produced.

    Arguments:
        argv: Command-line arguments, or None to read them from the process.
        probes: Probe surface to use, or None for the real client libraries.
        sleep: Callable that pauses between attempts.
        now: Callable returning the current monotonic time.

    Returns:
        Zero when every service is ready, one when the timeout passed.

    Raises:
        KeyError: If a variable the requested services need is absent.
    """
    arguments = build_parser().parse_args(argv)
    services = build_services(probes if probes is not None else RealProbes())
    deadline = now() + arguments.timeout

    for name in arguments.services:
        print(f"waiting for {name}")
        failure = wait_for(services[name], deadline, sleep, now)

        if failure is not None:
            print(f"timed out waiting for {name} within the {arguments.timeout}s budget: {failure}")
            return EXIT_TIMEOUT

        print(f"{name} is ready")

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
