"""Unit tests for the deployed Celery worker health probe.

Exercises exact worker identity, transient queue configuration, bounded task publication, reply
classification, cleanup, and command exits without opening a socket.
"""

from __future__ import annotations

import runpy
import sys
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from queue import Empty
from typing import TYPE_CHECKING, cast, override

import pytest
from scripts import celery_worker_health

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import TracebackType

pytestmark = pytest.mark.unit
DESTINATION = "celery@celery-worker-cw8rt"
PROBE_ID = "worker-health-probe"
HEALTH_TIMEOUT_SECONDS = 10.0
UUID_HEX_LENGTH = 32
WORKER_COMMAND = (
    b"/opt/venv/bin/python\x00/opt/venv/bin/celery\x00-A\x00config\x00worker\x00"
    b"--hostname=celery@celery-worker-cw8rt\x00"
)


@dataclass
class FakeMessage:
    """Carry one decoded transient reply.

    Inherits from ``object`` and exposes the payload shape consumed by the health classifier.
    Keeps message transport details outside focused assertions.

    Attributes:
        payload: Decoded reply payload.

    Members:
        None.
    """

    payload: object


@dataclass
class FakeReplyQueue(AbstractContextManager[celery_worker_health.ReplyQueue]):
    """Record transient queue lifecycle and retrieval.

    Inherits from ``AbstractContextManager`` and acts as its own producer so tests can prove task
    publication and reply consumption share one managed broker channel.

    Attributes:
        message: Reply returned by ``get``.
        get_error: Optional timeout raised by ``get``.
        entered: Whether the queue context was entered.
        exited: Whether the queue context was exited.
        get_calls: Blocking and timeout arguments observed.

    Members:
        __enter__: Mark the queue entered.
        __exit__: Mark the queue exited.
        get: Return or fail one reply.
    """

    message: FakeMessage | None = None
    get_error: BaseException | None = None
    entered: bool = False
    exited: bool = False
    get_calls: list[tuple[bool, float]] = field(default_factory=list)

    @override
    def __enter__(self) -> celery_worker_health.ReplyQueue:
        """Enter the transient queue context.

        Marks the queue active and returns its public reply-queue surface.
        Makes declaration and deletion lifecycle observable.

        Arguments:
            None.

        Returns:
            This queue as the reply protocol.
        """
        self.entered = True
        return cast("celery_worker_health.ReplyQueue", self)

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the transient queue context.

        Records auto-deleting resource cleanup while preserving any timeout or broker failure.
        Mirrors the real SimpleQueue context boundary.

        Arguments:
            exc_type: Exception type leaving the context, when present.
            exc_value: Exception value leaving the context, when present.
            traceback: Exception traceback leaving the context, when present.

        Returns:
            None.
        """
        del exc_type, exc_value, traceback
        self.exited = True

    def get(self, *, block: bool, timeout: float) -> FakeMessage:
        """Return or fail one transient reply.

        Records the bounded blocking read before returning the configured message or raising the
        configured timeout.

        Arguments:
            block: Whether the health command requested a blocking read.
            timeout: Maximum reply wait.

        Returns:
            Configured reply message.

        Raises:
            BaseException: Configured retrieval failure, when present.
            AssertionError: If no message or failure was configured.
        """
        self.get_calls.append((block, timeout))
        if self.get_error is not None:
            raise self.get_error
        assert self.message is not None
        return self.message


@dataclass
class FakeProducer(AbstractContextManager[object]):
    """Record task producer lifecycle.

    Inherits from ``AbstractContextManager`` and retains the broker connection plus reply queue so
    an echoing task can place its fabricated response on the caller's transient queue.

    Attributes:
        connection: Broker connection supplied by the runtime.
        reply_queue: Queue receiving an echoed response.
        entered: Whether the producer context was entered.
        exited: Whether the producer context was exited.

    Members:
        __enter__: Mark the producer entered.
        __exit__: Mark the producer exited.
    """

    connection: object
    reply_queue: FakeReplyQueue
    entered: bool = False
    exited: bool = False

    @override
    def __enter__(self) -> object:
        """Enter the fabricated task producer.

        Marks the producer active and returns the object passed to task publication.
        Makes producer ownership observable without opening a channel.

        Arguments:
            None.

        Returns:
            This producer instance.
        """
        self.entered = True
        return self

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the fabricated task producer.

        Records cleanup while preserving any publication or reply timeout already in flight.
        Mirrors Kombu producer context semantics.

        Arguments:
            exc_type: Exception type leaving the context, when present.
            exc_value: Exception value leaving the context, when present.
            traceback: Exception traceback leaving the context, when present.

        Returns:
            None.
        """
        del exc_type, exc_value, traceback
        self.exited = True


@dataclass
class FakeConnection(AbstractContextManager[celery_worker_health.BrokerConnection]):
    """Record broker and reply-queue lifecycle.

    Inherits from ``AbstractContextManager`` and exposes the SimpleQueue construction seam used by
    the health command while retaining every declaration option.

    Attributes:
        reply_queue: Queue returned to the health command.
        entered: Whether the connection context was entered.
        exited: Whether the connection context was exited.
        queue_calls: Reply queue construction arguments observed.

    Members:
        __enter__: Mark the connection entered.
        __exit__: Mark the connection exited.
        SimpleQueue: Return the fabricated reply queue.
    """

    reply_queue: FakeReplyQueue
    entered: bool = False
    exited: bool = False
    queue_calls: list[
        tuple[str, bool, Mapping[str, object], Mapping[str, object], str, Sequence[str]]
    ] = field(default_factory=list)

    @override
    def __enter__(self) -> celery_worker_health.BrokerConnection:
        """Enter the fabricated broker connection.

        Marks the connection active and returns its public broker protocol.
        Makes connection cleanup observable without a socket.

        Arguments:
            None.

        Returns:
            This connection as the broker protocol.
        """
        self.entered = True
        return cast("celery_worker_health.BrokerConnection", self)

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the fabricated broker connection.

        Records cleanup while preserving any reply timeout or publication failure.
        Mirrors Kombu connection context semantics.

        Arguments:
            exc_type: Exception type leaving the context, when present.
            exc_value: Exception value leaving the context, when present.
            traceback: Exception traceback leaving the context, when present.

        Returns:
            None.
        """
        del exc_type, exc_value, traceback
        self.exited = True

    def SimpleQueue(  # noqa: N802, PLR0913
        self,
        name: str,
        *,
        no_ack: bool,
        queue_opts: Mapping[str, object],
        exchange_opts: Mapping[str, object],
        serializer: str,
        accept: Sequence[str],
    ) -> AbstractContextManager[celery_worker_health.ReplyQueue]:
        """Return the fabricated transient reply queue.

        Records every durability, exclusivity, serializer, and content-type option so tests can
        verify no persistent reply resource is introduced.

        Arguments:
            name: Generated reply exchange and queue name.
            no_ack: Whether reply delivery requires acknowledgement.
            queue_opts: Queue declaration options.
            exchange_opts: Exchange declaration options.
            serializer: Reply serializer.
            accept: Accepted content types.

        Returns:
            Fabricated reply queue context.
        """
        self.queue_calls.append((name, no_ack, queue_opts, exchange_opts, serializer, accept))
        return self.reply_queue


@dataclass
class FakeCeleryApplication:
    """Expose one context-managed broker connection.

    Inherits from ``object`` and supplies the Celery application seam needed by the health command.
    Keeps the unit layer independent of Celery's concrete application class.

    Attributes:
        connection: Fabricated broker connection.

    Members:
        connection_for_write: Return the broker connection context.
    """

    connection: FakeConnection

    def connection_for_write(self) -> FakeConnection:
        """Return the broker connection context.

        Reuses one observable connection for reply queue, publication, and cleanup assertions.
        Matches the public Celery application seam.

        Arguments:
            None.

        Returns:
            Fabricated connection context.
        """
        return self.connection


@dataclass
class FakeProbeTask:
    """Record one bounded worker-health publication.

    Inherits from ``object`` and can echo the probe identifier into the fabricated reply queue,
    retaining every publication option for assertions.

    Attributes:
        echo_argument: Whether publication creates the expected reply.
        calls: Task publication arguments observed.

    Members:
        apply_async: Record one bounded probe.
    """

    echo_argument: bool = False
    calls: list[tuple[tuple[str, str], object, float, float, float]] = field(default_factory=list)

    def apply_async(
        self,
        args: tuple[str, str],
        *,
        producer: object,
        expires: float,
        soft_time_limit: float,
        time_limit: float,
    ) -> object:
        """Publish one fabricated worker-health probe.

        Records expiry and execution limits and optionally writes the opaque reply to the producer,
        which is the fabricated transient reply queue.

        Arguments:
            args: Probe identifier and transient reply name.
            producer: Reply queue's producer.
            expires: Broker delivery expiry.
            soft_time_limit: Cooperative execution limit.
            time_limit: Hard execution limit.

        Returns:
            Unused publication handle.
        """
        self.calls.append((args, producer, expires, soft_time_limit, time_limit))
        if self.echo_argument:
            task_producer = cast("FakeProducer", producer)
            task_producer.reply_queue.message = FakeMessage({"probe_id": args[0]})
        return object()


def runtime(
    reply_queue: FakeReplyQueue,
    task: FakeProbeTask,
) -> tuple[
    celery_worker_health.WorkerHealthRuntime,
    FakeConnection,
    list[FakeProducer],
]:
    """Build one health runtime from fabricated broker boundaries.

    Binds the supplied reply queue and task to one observable connection while fixing the probe
    identifier for deterministic assertions.

    Arguments:
        reply_queue: Transient reply queue double.
        task: Worker-health task double.

    Returns:
        Worker health runtime, broker connection, and created producers.
    """
    connection = FakeConnection(reply_queue)
    application = FakeCeleryApplication(connection)
    producers: list[FakeProducer] = []

    def producer_factory(active_connection: object) -> FakeProducer:
        """Build one task producer for the reply queue.

        Retains each producer so tests can assert its connection binding and context cleanup.
        Keeps producer construction at the external messaging boundary.

        Arguments:
            active_connection: Broker connection supplied by the runtime.

        Returns:
            Fabricated task producer.
        """
        producer = FakeProducer(active_connection, reply_queue)
        producers.append(producer)
        return producer

    return (
        celery_worker_health.WorkerHealthRuntime(
            celery_app=cast("celery_worker_health.CeleryApplication", application),
            probe=cast("celery_worker_health.WorkerProbe", task),
            producer_factory=producer_factory,
            probe_id_factory=lambda: PROBE_ID,
        ),
        connection,
        producers,
    )


def test_worker_identity_requires_the_registered_celery_command() -> None:
    """Accept only PID 1 for the registered worker node.

    Covers executable paths, the worker subcommand, and the exact hostname while rejecting another
    node or unrelated process before broker resources are created.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If worker identity is classified incorrectly.
    """
    assert celery_worker_health.worker_identity_matches(WORKER_COMMAND, DESTINATION) is True
    assert (
        celery_worker_health.worker_identity_matches(
            b"python\x00-c\x00sleep\x00",
            DESTINATION,
        )
        is False
    )
    assert (
        celery_worker_health.worker_identity_matches(
            b"celery\x00worker\x00--hostname=celery@other\x00",
            DESTINATION,
        )
        is False
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"probe_id": PROBE_ID}, True), ({"probe_id": "other"}, False)],
)
def test_worker_round_trip_uses_transient_bounded_resources(
    payload: object,
    *,
    expected: bool,
) -> None:
    """Classify the exact reply and delete every transient resource.

    Verifies queue and exchange options, delivery expiry margin, execution limits, publication
    producer, reply timeout, and connection cleanup for matching and mismatched payloads.

    Arguments:
        payload: Decoded reply payload.
        expected: Health classification for the payload.

    Returns:
        None.

    Raises:
        AssertionError: If reply classification or transient resource policy differs.
    """
    reply_queue = FakeReplyQueue(message=FakeMessage(payload))
    task = FakeProbeTask()
    health_runtime, connection, producers = runtime(reply_queue, task)

    observed = celery_worker_health.worker_round_trip(
        health_runtime,
        DESTINATION,
        HEALTH_TIMEOUT_SECONDS,
        command=WORKER_COMMAND,
    )

    reply_name = f"{celery_worker_health.REPLY_PREFIX}.{PROBE_ID}"
    delivery_timeout = HEALTH_TIMEOUT_SECONDS - celery_worker_health.HEALTH_PROBE_TIME_LIMIT_SECONDS
    assert observed is expected
    assert connection.queue_calls == [
        (
            reply_name,
            True,
            {"durable": False, "exclusive": True, "auto_delete": True},
            {"durable": False, "auto_delete": True, "delivery_mode": "transient"},
            "json",
            ["json"],
        )
    ]
    assert task.calls == [
        (
            (PROBE_ID, reply_name),
            producers[0],
            delivery_timeout,
            celery_worker_health.HEALTH_PROBE_SOFT_TIME_LIMIT_SECONDS,
            celery_worker_health.HEALTH_PROBE_TIME_LIMIT_SECONDS,
        )
    ]
    assert reply_queue.get_calls == [(True, HEALTH_TIMEOUT_SECONDS)]
    assert reply_queue.entered is True
    assert reply_queue.exited is True
    assert producers[0].entered is True
    assert producers[0].exited is True
    assert producers[0].connection is connection
    assert connection.entered is True
    assert connection.exited is True


def test_worker_round_trip_times_out_after_bounded_publication() -> None:
    """Propagate a missing transient reply after bounded task publication.

    Uses an empty reply queue and verifies task expiry reserves the hard execution window before the
    caller deadline while both queue and connection contexts still close.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If timeout publication is unbounded or resources remain open.
    """
    reply_queue = FakeReplyQueue(get_error=Empty())
    task = FakeProbeTask()
    health_runtime, connection, producers = runtime(reply_queue, task)

    with pytest.raises(Empty):
        celery_worker_health.worker_round_trip(
            health_runtime,
            DESTINATION,
            HEALTH_TIMEOUT_SECONDS,
            command=WORKER_COMMAND,
        )

    assert task.calls[0][2] == (
        HEALTH_TIMEOUT_SECONDS - celery_worker_health.HEALTH_PROBE_TIME_LIMIT_SECONDS
    )
    assert reply_queue.exited is True
    assert producers[0].exited is True
    assert connection.exited is True


def test_worker_round_trip_rejects_identity_and_unsafe_timeout_before_publication() -> None:
    """Refuse local identity and deadline errors without broker side effects.

    Proves neither an unrelated process nor a deadline lacking the hard-limit reserve can declare a
    queue or publish a task.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If invalid local policy reaches the broker.
    """
    reply_queue = FakeReplyQueue()
    task = FakeProbeTask()
    health_runtime, connection, producers = runtime(reply_queue, task)

    assert (
        celery_worker_health.worker_round_trip(
            health_runtime,
            DESTINATION,
            HEALTH_TIMEOUT_SECONDS,
            command=b"python\x00-c\x00sleep\x00",
        )
        is False
    )
    with pytest.raises(ValueError, match="exceed"):
        celery_worker_health.worker_round_trip(
            health_runtime,
            DESTINATION,
            celery_worker_health.HEALTH_PROBE_TIME_LIMIT_SECONDS,
            command=WORKER_COMMAND,
        )

    assert connection.entered is False
    assert producers == []
    assert task.calls == []


@pytest.mark.parametrize(
    ("result", "expected"),
    [(True, celery_worker_health.EXIT_OK), (False, celery_worker_health.EXIT_UNHEALTHY)],
)
def test_command_exit_code_reports_worker_health(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: bool,
    expected: int,
) -> None:
    """Return the documented process exit code for worker readiness.

    Replaces the public round-trip operation so command parsing and health-to-exit translation are
    isolated from Linux process files and external services.

    Arguments:
        monkeypatch: Fixture replacing the worker round-trip operation.
        result: Worker readiness result to expose.
        expected: Process exit code required for that result.

    Returns:
        None.

    Raises:
        AssertionError: If the command reports the wrong health state.
    """
    calls: list[tuple[str, float]] = []

    def round_trip(
        _runtime: object,
        destination: str,
        timeout: float,
        *,
        command: bytes,
    ) -> bool:
        """Return one configured readiness result.

        Records the parsed destination, timeout, and PID command before returning the test outcome.
        Isolates command translation from the broker round trip.

        Arguments:
            _runtime: Ignored worker health runtime.
            destination: Parsed worker node name.
            timeout: Parsed reply timeout.
            command: PID 1 command read by the command.

        Returns:
            Configured readiness result.
        """
        assert command == WORKER_COMMAND
        calls.append((destination, timeout))
        return result

    monkeypatch.setattr(celery_worker_health, "worker_round_trip", round_trip)
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda _path: WORKER_COMMAND)

    assert (
        celery_worker_health.main(
            ["--destination", DESTINATION, "--timeout", "4"],
        )
        == expected
    )
    assert calls == [(DESTINATION, 4.0)]


def test_command_runs_the_real_transient_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete the command through its real transient reply implementation.

    Replaces only external Celery, task, and process-file boundaries while allowing the command to
    generate its production identifier, queue name, and bounded publication policy.

    Arguments:
        monkeypatch: Fixture replacing external runtime boundaries.

    Returns:
        None.

    Raises:
        AssertionError: If the command cannot complete its production round-trip path.
    """
    reply_queue = FakeReplyQueue()
    connection = FakeConnection(reply_queue)
    application = FakeCeleryApplication(connection)
    task = FakeProbeTask(echo_argument=True)
    producers: list[FakeProducer] = []

    def producer_factory(active_connection: object) -> FakeProducer:
        """Build one observable command producer.

        Retains the producer so the command-level test can verify connection binding and cleanup.
        Uses the command's transient reply queue for the echoed task response.

        Arguments:
            active_connection: Broker connection supplied by the command.

        Returns:
            Fabricated task producer.
        """
        producer = FakeProducer(active_connection, reply_queue)
        producers.append(producer)
        return producer

    monkeypatch.setattr(celery_worker_health, "app", application)
    monkeypatch.setattr(celery_worker_health, "DEPLOYED_WORKER_PROBE", task)
    monkeypatch.setattr(celery_worker_health, "Producer", producer_factory)
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda _path: WORKER_COMMAND)

    assert (
        celery_worker_health.main(
            ["--destination", DESTINATION, "--timeout", "4"],
        )
        == celery_worker_health.EXIT_OK
    )
    probe_id = task.calls[0][0][0]
    assert len(probe_id) == UUID_HEX_LENGTH
    assert task.calls[0][0][1] == f"{celery_worker_health.REPLY_PREFIX}.{probe_id}"
    assert reply_queue.exited is True
    assert producers[0].exited is True
    assert connection.exited is True


def test_command_reports_unhealthy_when_the_round_trip_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when process, broker, worker, or reply observation fails.

    Makes the public round-trip seam raise one expected queue timeout and verifies the command
    converts it to an unhealthy exit instead of a success-shaped fallback.

    Arguments:
        monkeypatch: Fixture replacing the round-trip operation and PID command read.

    Returns:
        None.

    Raises:
        AssertionError: If an unavailable dependency is reported healthy.
    """

    def fail(*_arguments: object, **_keywords: object) -> bool:
        """Raise one transient reply timeout.

        Models a worker that does not answer before the exclusive queue's bounded wait ends.
        Proves the command translates expected operational failures to unhealthy.

        Arguments:
            *_arguments: Ignored positional values.
            **_keywords: Ignored keyword values.

        Returns:
            Never returns.

        Raises:
            Empty: Always.
        """
        raise Empty

    monkeypatch.setattr(celery_worker_health, "worker_round_trip", fail)
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda _path: WORKER_COMMAND)

    assert (
        celery_worker_health.main(
            ["--destination", DESTINATION, "--timeout", "4"],
        )
        == celery_worker_health.EXIT_UNHEALTHY
    )


def test_module_entrypoint_exits_unhealthy_for_the_wrong_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit the installed script when PID 1 is not the registered worker.

    Executes the module entrypoint with an unrelated command line so the ``__main__`` branch is
    covered without constructing any external client.

    Arguments:
        monkeypatch: Fixture replacing process arguments and the PID command read.

    Returns:
        None.

    Raises:
        AssertionError: If the module entrypoint returns instead of exiting unhealthy.
    """
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda _path: b"python\x00-c\x00sleep\x00")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "celery_worker_health.py",
            "--destination",
            DESTINATION,
            "--timeout",
            "4",
        ],
    )

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(celery_worker_health.__file__, run_name="__main__")

    assert raised.value.code == celery_worker_health.EXIT_UNHEALTHY
