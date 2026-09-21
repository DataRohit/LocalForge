"""Celery worker health command with a transient broker round trip.

Confirms PID 1 is the registered Celery worker, then publishes one bounded probe and requires its
opaque reply through an exclusive auto-deleting queue without creating result-backend state.
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from queue import Empty
from typing import Protocol, cast

import django
from celery.exceptions import CeleryError
from django.conf import settings
from kombu import Producer
from kombu.exceptions import KombuError

from config.celery import app

EXIT_OK = 0
EXIT_UNHEALTHY = 1
PROC_COMMAND = Path("/proc/1/cmdline")
REPLY_PREFIX = "localforge.worker-health"


class ReplyMessage(Protocol):
    """Transient reply message surface.

    Inherits from ``Protocol`` and exposes only the decoded payload needed to compare the worker's
    response without depending on Kombu's dynamic message implementation.

    Attributes:
        payload: Decoded JSON reply.

    Members:
        None.
    """

    payload: object


class ReplyQueue(Protocol):
    """Transient reply queue operations.

    Inherits from ``Protocol`` and exposes bounded reply retrieval while queue lifecycle remains
    owned by its context manager.

    Members:
        get: Wait for one reply message.
    """

    def get(self, *, block: bool, timeout: float) -> ReplyMessage:
        """Wait for one worker reply.

        Uses a bounded blocking read so the health process exits unhealthy when no worker completes
        the probe before the configured deadline.

        Arguments:
            block: Whether to wait for a message.
            timeout: Maximum seconds to wait.

        Returns:
            Reply message received from the transient queue.

        Raises:
            Empty: If no reply arrives before the timeout.
        """
        ...


class BrokerConnection(Protocol):
    """Broker connection operations used by health.

    Inherits from ``Protocol`` and exposes construction of one exclusive reply queue without
    depending on Kombu's untyped concrete connection class.

    Members:
        SimpleQueue: Build the transient reply queue.
    """

    def SimpleQueue(  # noqa: N802, PLR0913
        self,
        name: str,
        *,
        no_ack: bool,
        queue_opts: Mapping[str, object],
        exchange_opts: Mapping[str, object],
        serializer: str,
        accept: Sequence[str],
    ) -> AbstractContextManager[ReplyQueue]:
        """Build one transient reply queue.

        Uses the generated name for both direct exchange and queue, and returns a context manager
        whose close removes the exclusive auto-deleting resources.

        Arguments:
            name: Unique reply exchange and queue name.
            no_ack: Whether reply delivery requires acknowledgement.
            queue_opts: Queue durability, exclusivity, and deletion options.
            exchange_opts: Exchange durability and deletion options.
            serializer: Reply serializer.
            accept: Accepted reply content types.

        Returns:
            Context-managed transient reply queue.
        """
        ...


class CeleryApplication(Protocol):
    """Celery application write-connection surface.

    Inherits from ``Protocol`` and exposes only the context-managed broker connection required for
    task publication and reply consumption.

    Members:
        connection_for_write: Open one broker connection.
    """

    def connection_for_write(self) -> AbstractContextManager[BrokerConnection]:
        """Open a broker connection for the health round trip.

        Returns a context manager so publishing, consuming, and transient queue deletion complete
        before the health process exits.

        Arguments:
            None.

        Returns:
            Context-managed broker connection.
        """
        ...


class WorkerProbe(Protocol):
    """Worker-health task publication surface.

    Inherits from ``Protocol`` and defines bounded task publication with no result-backend
    dependency, using the reply queue's explicit producer.

    Members:
        apply_async: Enqueue one bounded health probe.
    """

    def apply_async(
        self,
        args: tuple[str, str],
        *,
        producer: object,
        expires: float,
        soft_time_limit: float,
        time_limit: float,
    ) -> object:
        """Publish one bounded health probe.

        Supplies the transient reply name, delivery expiry, and execution limits while deliberately
        ignoring Celery's result handle because the task is registered with ``ignore_result``.

        Arguments:
            args: Opaque probe identifier and transient reply name.
            producer: Reply queue's context-managed Kombu producer.
            expires: Broker delivery expiry.
            soft_time_limit: Cooperative execution limit.
            time_limit: Hard execution limit.

        Returns:
            Celery's unused publication result handle.
        """
        ...


@dataclass(frozen=True)
class WorkerHealthRuntime:
    """External boundaries used by one worker health round trip.

    Uses frozen dataclass semantics and inherits from ``object`` to keep the Celery application,
    probe task, and identifier generation together as one immutable seam.

    Attributes:
        celery_app: Configured Celery application.
        probe: Worker-health probe task.
        producer_factory: Context-managed task producer constructor.
        probe_id_factory: Opaque identifier constructor.

    Members:
        None.
    """

    celery_app: CeleryApplication
    probe: WorkerProbe
    producer_factory: Callable[[object], AbstractContextManager[object]]
    probe_id_factory: Callable[[], str]


django.setup()
DEPLOYED_WORKER_PROBE = cast("WorkerProbe", import_module("config.tasks").worker_health_probe)
HEALTH_PROBE_SOFT_TIME_LIMIT_SECONDS = float(
    settings.CELERY_WORKER_HEALTH_TASK_SOFT_TIME_LIMIT_SECONDS
)
HEALTH_PROBE_TIME_LIMIT_SECONDS = float(settings.CELERY_WORKER_HEALTH_TASK_TIME_LIMIT_SECONDS)


def worker_identity_matches(command: bytes, destination: str) -> bool:
    """Return whether PID 1 is the exact registered Celery worker.

    Accepts executable paths while requiring the worker subcommand and configured hostname, so a
    replacement process cannot inherit a healthy result by reaching the same dependencies.

    Arguments:
        command: Null-delimited PID 1 command line.
        destination: Registered Celery node name.

    Returns:
        Whether the command identifies the expected worker.
    """
    arguments = {
        argument.decode("utf-8", errors="surrogateescape")
        for argument in command.split(b"\x00")
        if argument
    }
    executables = {argument.rsplit("/", maxsplit=1)[-1] for argument in arguments}
    return (
        "celery" in executables
        and "worker" in arguments
        and f"--hostname={destination}" in arguments
    )


def _probe_id() -> str:
    """Create one opaque worker health identifier.

    Uses a random UUID value so concurrent or delayed probes cannot satisfy one another's reply
    comparison.

    Arguments:
        None.

    Returns:
        Unique hexadecimal probe identifier.
    """
    return uuid.uuid4().hex


def worker_round_trip(
    runtime: WorkerHealthRuntime,
    destination: str,
    timeout: float,
    *,
    command: bytes,
) -> bool:
    """Return whether the registered worker completes one transient round trip.

    Verifies local worker identity, declares an exclusive reply queue, expires task delivery before
    the caller deadline, and accepts only the exact opaque reply from bounded task execution.

    Arguments:
        runtime: External Celery, task, and identifier boundaries.
        destination: Registered Celery node name.
        timeout: Maximum seconds to wait for the reply.
        command: Null-delimited PID 1 command line.

    Returns:
        Whether the exact worker probe reply arrived successfully.

    Raises:
        Empty: If no worker reply arrives before the deadline.
        CeleryError: If Celery cannot publish the probe.
        KombuError: If broker messaging or transient queue operations fail.
    """
    if not worker_identity_matches(command, destination):
        return False
    if timeout <= HEALTH_PROBE_TIME_LIMIT_SECONDS:
        error_message = "worker health timeout must exceed the probe execution time limit"
        raise ValueError(error_message)

    probe_id = runtime.probe_id_factory()
    reply_name = f"{REPLY_PREFIX}.{probe_id}"
    delivery_timeout = timeout - HEALTH_PROBE_TIME_LIMIT_SECONDS
    with (
        runtime.celery_app.connection_for_write() as connection,
        connection.SimpleQueue(
            reply_name,
            no_ack=True,
            queue_opts={
                "durable": False,
                "exclusive": True,
                "auto_delete": True,
            },
            exchange_opts={
                "durable": False,
                "auto_delete": True,
                "delivery_mode": "transient",
            },
            serializer="json",
            accept=["json"],
        ) as reply_queue,
        runtime.producer_factory(connection) as producer,
    ):
        runtime.probe.apply_async(
            args=(probe_id, reply_name),
            producer=producer,
            expires=delivery_timeout,
            soft_time_limit=HEALTH_PROBE_SOFT_TIME_LIMIT_SECONDS,
            time_limit=HEALTH_PROBE_TIME_LIMIT_SECONDS,
        )
        reply = reply_queue.get(block=True, timeout=timeout)

    return reply.payload == {"probe_id": probe_id}


def parser() -> argparse.ArgumentParser:
    """Build the worker health command parser.

    Requires the registered Celery node name and accepts the bounded reply timeout supplied by the
    environment-backed Compose health check.

    Arguments:
        None.

    Returns:
        Configured argument parser.
    """
    command = argparse.ArgumentParser(description="Check one LocalForge Celery worker")
    command.add_argument("--destination", required=True)
    command.add_argument("--timeout", required=True, type=float)
    return command


def main(argv: Sequence[str] | None = None) -> int:
    """Run one worker health check.

    Reads PID 1, performs the transient broker round trip, and converts expected process, broker,
    worker, and timeout failures into Docker's unhealthy exit code.

    Arguments:
        argv: Optional argument sequence, defaulting to process arguments.

    Returns:
        Zero when the exact worker reply arrives, otherwise one.
    """
    arguments = parser().parse_args(argv)
    try:
        healthy = worker_round_trip(
            WorkerHealthRuntime(
                celery_app=cast("CeleryApplication", app),
                probe=DEPLOYED_WORKER_PROBE,
                producer_factory=Producer,
                probe_id_factory=_probe_id,
            ),
            arguments.destination,
            arguments.timeout,
            command=PROC_COMMAND.read_bytes(),
        )
    except (CeleryError, Empty, KombuError, KeyError, OSError, ValueError) as health_error:
        del health_error
        return EXIT_UNHEALTHY
    return EXIT_OK if healthy else EXIT_UNHEALTHY


if __name__ == "__main__":
    raise SystemExit(main())
