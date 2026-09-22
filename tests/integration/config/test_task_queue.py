"""Integration tests for the task queue.

Covers a task enqueued onto the real broker and read back from the result backend, the queues
remote control declares against this broker, and the credentials a worker's own logging must not
write out.
"""

from __future__ import annotations

import gc
import io
import logging
import uuid
from typing import TYPE_CHECKING, Any, cast

import pytest
from celery.contrib.testing.worker import start_worker
from celery.events.receiver import EventReceiver
from celery.result import AsyncResult, EagerResult
from django.conf import settings
from kombu import Exchange, Queue

from config import celery as celery_module
from config.celery import REDACTED, app
from config.logs import StructuredFormatter

if TYPE_CHECKING:
    from collections.abc import Iterator

    from celery import Task

RESULT_TIMEOUT_SECONDS = 15
WORKER_SHUTDOWN_SECONDS = 30
FIRST_ADDEND = 17
SECOND_ADDEND = 25
POSITIONAL_SECRET = "positional-credential-value"  # noqa: S105
KEYWORD_SECRET = "keyword-credential-value"  # noqa: S105
NESTED_SECRET = "nested-credential-value"  # noqa: S105
AUTH_SECRET = "authorization-credential-value"  # noqa: S105


def dispose_result(result: AsyncResult | None) -> None:
    """Release one real result consumer while service access is still permitted.

    Forgets persisted state and removes the asynchronous consumer before forcing collection, so a
    deferred deallocator cannot reconnect after the unit network guard becomes active.

    Arguments:
        result: Real asynchronous result to release, or None before publication.

    Returns:
        None.
    """
    if result is None:
        return

    result.forget()
    app.backend.remove_pending_result(result)


def add(first: int, second: int) -> int:
    """Add two numbers on a worker.

    Exists so a test has something to enqueue that produces a value only the worker could have
    computed, which is what proves the round trip rather than a local call.

    Arguments:
        first: Left operand.
        second: Right operand.

    Returns:
        The sum of the two operands.
    """
    return first + second


def refuse(carrier: str, *, api_key: str, payload: dict[str, str]) -> None:
    """Fail on purpose, holding a credential in every shape an argument can take.

    Exists so a test can read everything the queue logs about a failing call and prove no argument
    value reached the log stream, whether it was positional, named, or nested inside a payload.

    Arguments:
        carrier: Positional argument standing in for a credential.
        api_key: Keyword argument whose name reads like a credential.
        payload: Keyword argument holding a credential one level down.

    Returns:
        None.

    Raises:
        RuntimeError: Always, because the failure is the point.
    """
    del carrier, api_key, payload
    message = "refused on purpose"
    raise RuntimeError(message)


def retry_then_refuse(
    task: Task,
    carrier: str,
    *,
    api_key: str,
    payload: dict[str, str],
) -> None:
    """Retry twice before ending in the terminal failure path.

    Uses immediate retries so the integration test proves the configured bounded lifecycle without
    waiting for production backoff delays, while retaining credential-shaped arguments for the
    dead-letter scrub assertion.

    Arguments:
        task: Bound Celery task carrying the current retry count.
        carrier: Positional argument standing in for a credential.
        api_key: Keyword argument whose name reads like a credential.
        payload: Keyword argument holding nested credentials.

    Returns:
        None.

    Raises:
        Retry: Until the retry bound is exhausted.
        RuntimeError: When the configured retry bound is exhausted.
    """
    del carrier, api_key, payload
    failure = RuntimeError("refused after bounded retries")
    raise task.retry(exc=failure, countdown=0, max_retries=2)


add_task = app.task(name="tests.add")(add)
refuse_task = app.task(name="tests.refuse", max_retries=0, autoretry_for=())(refuse)
retry_refuse_task = app.task(
    bind=True,
    name="tests.retry_then_refuse",
    autoretry_for=(),
)(retry_then_refuse)


@pytest.fixture
def broker_queue(monkeypatch: pytest.MonkeyPatch, worker_namespace: str) -> Iterator[str]:
    """Give a test its own queue on the broker, with the eager default turned off.

    Overrides the namespaced setting Celery actually reads, because the lowercase key is shadowed
    by it, and deletes the queue afterwards so a suite does not leave queues behind on the broker.

    Arguments:
        monkeypatch: Fixture used to turn the eager default off for one test.
        worker_namespace: Per-worker prefix keeping parallel workers off each other's queue.

    Yields:
        The name of the queue this test owns.
    """
    eager = False
    monkeypatch.setitem(app.conf, "CELERY_TASK_ALWAYS_EAGER", eager)

    name = f"{worker_namespace}-{uuid.uuid4().hex}"
    with app.connection_for_write() as connection:
        channel = connection.channel()
        for configured in app.conf.task_queues:
            configured(channel).declare()

    yield name

    with app.connection_for_write() as connection:
        channel = connection.channel()
        channel.queue_delete(name)
        channel.exchange_delete(name)
    app.amqp.queues.pop(name, None)


@pytest.mark.integration
@pytest.mark.services("rabbitmq", "valkey-cache")
def test_a_task_enqueued_on_the_broker_comes_back_with_its_result(broker_queue: str) -> None:
    """Enqueue a task on the broker and read the result back.

    Runs a worker in this process against the real broker with the eager default explicitly turned
    off, because a task that never leaves the process proves nothing about the broker, the result
    backend, or the serialization the two agree on.

    Arguments:
        broker_queue: Queue this test owns on the broker.

    Returns:
        None.

    Raises:
        AssertionError: If the task ran in this process or the result does not come back.
    """
    assert app.conf.task_always_eager is False

    enqueued: AsyncResult | None = None
    try:
        with start_worker(
            app,
            queues=[broker_queue],
            perform_ping_check=False,
            shutdown_timeout=WORKER_SHUTDOWN_SECONDS,
        ):
            enqueued = cast(
                "AsyncResult",
                add_task.apply_async(
                    args=(FIRST_ADDEND, SECOND_ADDEND),
                    queue=broker_queue,
                ),
            )

            assert not isinstance(enqueued, EagerResult)
            assert enqueued.get(timeout=RESULT_TIMEOUT_SECONDS) == FIRST_ADDEND + SECOND_ADDEND
            assert enqueued.successful()
    finally:
        dispose_result(enqueued)
        enqueued = None
        gc.collect()


@pytest.mark.integration
@pytest.mark.services("rabbitmq", "valkey-cache")
def test_a_failing_task_logs_no_argument_the_caller_passed(
    broker_queue: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every argument value out of everything the queue logs.

    Reads the log stream a real worker writes for a failing task, because the queue's own receipt
    and failure records carry the arguments independently of the project's base class and would
    otherwise ship a credential to the log store.

    Arguments:
        broker_queue: Queue this test owns on the broker.
        monkeypatch: Fixture replacing the terminal-record publisher.

    Returns:
        None.

    Raises:
        AssertionError: If any argument value reaches the log stream.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    watched = [logging.getLogger("celery"), logging.getLogger(celery_module.__name__)]
    dead_letters: list[celery_module.DeadLetterRecord] = []
    monkeypatch.setattr(celery_module, "publish_dead_letter", dead_letters.append)

    for logger in watched:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    enqueued: AsyncResult | None = None
    try:
        with start_worker(
            app,
            queues=[broker_queue],
            perform_ping_check=False,
            shutdown_timeout=WORKER_SHUTDOWN_SECONDS,
        ):
            enqueued = cast(
                "AsyncResult",
                refuse_task.apply_async(
                    args=(POSITIONAL_SECRET,),
                    kwargs={
                        "api_key": KEYWORD_SECRET,
                        "payload": {"password": NESTED_SECRET, "authorization": AUTH_SECRET},
                    },
                    queue=broker_queue,
                ),
            )

            with pytest.raises(RuntimeError):
                enqueued.get(timeout=RESULT_TIMEOUT_SECONDS)
    finally:
        dispose_result(enqueued)
        enqueued = None
        gc.collect()
        for logger in watched:
            logger.removeHandler(handler)

    logged = stream.getvalue()

    assert "tests.refuse" in logged
    assert REDACTED in logged
    assert POSITIONAL_SECRET not in logged
    assert KEYWORD_SECRET not in logged
    assert NESTED_SECRET not in logged
    assert AUTH_SECRET not in logged
    assert len(dead_letters) == 1


@pytest.mark.integration
@pytest.mark.services("valkey-cache")
def test_a_task_run_in_the_caller_logs_no_argument_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep arguments out of the log stream when no broker is involved.

    Runs the task in the caller with its failure reported rather than raised, because that path
    publishes no message and so never reaches the redaction attached to publishing, and is the
    path every other test in this suite takes.

    Arguments:
        monkeypatch: Fixture used to make the eager failure reportable rather than raised.

    Returns:
        None.

    Raises:
        AssertionError: If any argument value reaches the log stream.
    """
    reported = False
    dead_letters: list[celery_module.DeadLetterRecord] = []
    monkeypatch.setitem(app.conf, "CELERY_TASK_EAGER_PROPAGATES", reported)
    monkeypatch.setattr(celery_module, "publish_dead_letter", dead_letters.append)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    watched = [logging.getLogger("celery"), logging.getLogger(celery_module.__name__)]

    for logger in watched:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    try:
        refuse_task.apply(
            args=(POSITIONAL_SECRET,),
            kwargs={
                "api_key": KEYWORD_SECRET,
                "payload": {"password": NESTED_SECRET, "authorization": AUTH_SECRET},
            },
        )
    finally:
        for logger in watched:
            logger.removeHandler(handler)

    logged = stream.getvalue()

    assert "tests.refuse" in logged
    assert POSITIONAL_SECRET not in logged
    assert KEYWORD_SECRET not in logged
    assert NESTED_SECRET not in logged
    assert AUTH_SECRET not in logged
    assert len(dead_letters) == 1


@pytest.mark.integration
@pytest.mark.services("rabbitmq")
def test_a_published_message_carries_no_argument_on_the_wire(broker_queue: str) -> None:
    """Redact the message itself, not only what this process logs.

    Reads a published message straight off the broker with no worker running, because the event
    stream a dashboard reads is built from these fields and no logging filter can reach it.

    Arguments:
        broker_queue: Queue this test owns on the broker.

    Returns:
        None.

    Raises:
        AssertionError: If the message carries an argument value.
    """
    published: AsyncResult | None = None
    wire_queue = Queue(
        broker_queue,
        Exchange(broker_queue, type="topic", durable=True),
        routing_key=broker_queue,
        durable=True,
        queue_arguments={"x-queue-type": "quorum"},
    )
    try:
        with app.connection_for_write() as connection:
            wire_queue(connection.channel()).declare()
        published = cast(
            "AsyncResult",
            refuse_task.apply_async(
                args=(POSITIONAL_SECRET,),
                kwargs={
                    "api_key": KEYWORD_SECRET,
                    "payload": {"password": NESTED_SECRET, "authorization": AUTH_SECRET},
                },
                queue=broker_queue,
            ),
        )

        with app.connection_for_read() as connection:
            message = wire_queue(connection.channel()).get(accept=["json"], no_ack=True)

        assert message is not None

        headers = message.headers

        assert headers["argsrepr"] == repr(("str",))
        assert REDACTED in headers["kwargsrepr"]
        assert POSITIONAL_SECRET not in str(headers)
        assert KEYWORD_SECRET not in str(headers)
        assert NESTED_SECRET not in str(headers)
        assert AUTH_SECRET not in str(headers)
    finally:
        dispose_result(published)
        published = None
        gc.collect()


@pytest.mark.integration
@pytest.mark.services("rabbitmq", "valkey-cache")
def test_a_terminal_failure_lands_in_one_scrubbed_dead_letter_record(
    broker_queue: str,
    monkeypatch: pytest.MonkeyPatch,
    worker_namespace: str,
) -> None:
    """Persist one safe terminal record after a task reaches its retry bound.

    Executes an immediate bounded-retry task through a real broker and worker, then reads the
    durable dead-letter queue directly to prove terminal state is observable without credentials.

    Arguments:
        broker_queue: Queue this test owns for executable work.
        monkeypatch: Fixture assigning an isolated terminal queue.
        worker_namespace: Per-worker prefix keeping terminal records isolated.

    Returns:
        None.

    Raises:
        AssertionError: If no record arrives, more than one arrives, or any argument leaks.
    """
    dead_letter_name = f"{worker_namespace}-dead-{uuid.uuid4().hex}"
    dead_letter_queue = Queue(
        dead_letter_name,
        Exchange(dead_letter_name, type="topic", durable=True),
        routing_key=dead_letter_name,
        durable=True,
        queue_arguments={"x-queue-type": "quorum"},
    )
    monkeypatch.setitem(app.conf, "CELERY_DEAD_LETTER_QUEUE", dead_letter_name)

    enqueued: AsyncResult | None = None
    try:
        with start_worker(
            app,
            queues=[broker_queue],
            perform_ping_check=False,
            shutdown_timeout=WORKER_SHUTDOWN_SECONDS,
        ):
            enqueued = cast(
                "AsyncResult",
                retry_refuse_task.apply_async(
                    args=(POSITIONAL_SECRET,),
                    kwargs={
                        "api_key": KEYWORD_SECRET,
                        "payload": {"password": NESTED_SECRET, "authorization": AUTH_SECRET},
                    },
                    queue=broker_queue,
                ),
            )

            with pytest.raises(RuntimeError):
                enqueued.get(timeout=RESULT_TIMEOUT_SECONDS)

        with app.connection_for_read() as connection:
            channel = connection.channel()
            bound = dead_letter_queue(channel)
            message = bound.get(accept=["json"], no_ack=True)
            duplicate = bound.get(accept=["json"], no_ack=True)

            assert message is not None
            assert duplicate is None
            assert message.payload == {
                "exception_type": "RuntimeError",
                "retries": 2,
                "task_args": ["str"],
                "task_id": enqueued.id,
                "task_kwargs": {
                    "api_key": REDACTED,
                    "payload": {
                        "authorization": REDACTED,
                        "password": REDACTED,
                    },
                },
                "task_name": "tests.retry_then_refuse",
            }
            assert POSITIONAL_SECRET not in str(message.payload)
            assert KEYWORD_SECRET not in str(message.payload)
            assert NESTED_SECRET not in str(message.payload)
            assert AUTH_SECRET not in str(message.payload)
    finally:
        dispose_result(enqueued)
        enqueued = None
        gc.collect()
        with app.connection_for_write() as connection:
            dead_letter_queue(connection.channel()).delete()


@pytest.mark.integration
@pytest.mark.services("rabbitmq")
def test_every_queue_a_worker_declares_is_one_the_broker_still_accepts() -> None:
    """Declare each queue a worker needs to start on the pinned broker.

    Confirms the control mailbox and the event queue are both exclusive, because this broker
    refuses a transient queue that is not exclusive and the in-process test worker cannot prove it:
    it starts with gossip and mingle disabled, so it never declares either.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the broker refuses any queue a worker declares.
    """
    mailbox = app.control.mailbox

    with app.connection_for_write() as connection:
        channel = connection.channel()
        node = mailbox.get_queue(f"probe-{uuid.uuid4().hex}")(channel)
        reply = mailbox.get_reply_queue()(channel)
        events = EventReceiver(connection, app=app).queue(channel)

        for declared in (node, reply, events):
            declared.declare()

            assert declared.exclusive

        for declared in (node, reply, events):
            declared.delete()


@pytest.mark.integration
@pytest.mark.services("rabbitmq")
def test_the_suite_runs_tasks_eagerly_unless_a_test_says_otherwise() -> None:
    """Keep the broker out of every test that did not ask for it.

    Confirms the testing environment runs tasks in the caller, so only the tests above pay for a
    worker and a broker connection.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the suite would send tasks to the broker by default.
    """
    outcome: Any = add_task.apply_async(args=(FIRST_ADDEND, SECOND_ADDEND))

    assert settings.CELERY_TASK_ALWAYS_EAGER is True
    assert app.conf.task_always_eager is True
    assert app.conf.task_eager_propagates is True
    assert isinstance(outcome, EagerResult)
