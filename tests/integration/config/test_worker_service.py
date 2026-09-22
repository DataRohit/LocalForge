"""Separate-process integration tests for the deployed worker contract.

Starts the real Celery command against the testing broker and result backend, proving task
execution and worker-loss redelivery without relying on Celery's in-process test worker.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from kombu import Exchange, Queue
from kombu.exceptions import OperationalError

from config.celery import app
from config.tasks import slow_worker_probe, worker_probe

if TYPE_CHECKING:
    from collections.abc import Iterator

    from celery.result import AsyncResult

WORKER_START_TIMEOUT_SECONDS = 40
RESULT_TIMEOUT_SECONDS = 25
WORKER_STOP_TIMEOUT_SECONDS = 15
STATE_POLL_SECONDS = 0.1
SLOW_PROBE_SECONDS = 8.0
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EAGER_DISABLED = False
THREAD_WORKER_CONCURRENCY = 2


def _dispose_result(result: AsyncResult | None) -> None:
    """Release one result consumer before leaving the service-backed test scope.

    Forgets persisted state and removes the asynchronous consumer so forced collection cannot
    reconnect after another test activates the unit network guard.

    Arguments:
        result: Real asynchronous result to release, or None before publication.

    Returns:
        None.

    Raises:
        AssertionError: If the graceful shutdown request cannot reach the worker.
    """
    if result is None:
        return

    result.forget()
    app.backend.remove_pending_result(result)


def _wait_for_worker(node_name: str) -> None:
    """Wait for one named worker to answer remote control.

    Polls the broker-backed ping until the separate process is ready or the bounded startup budget
    expires.

    Arguments:
        node_name: Exact Celery node name assigned to the process.

    Returns:
        None.

    Raises:
        AssertionError: If the worker never answers before the deadline.
    """
    deadline = time.monotonic() + WORKER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        replies = app.control.ping(destination=[node_name], timeout=1)
        if replies:
            return
        time.sleep(STATE_POLL_SECONDS)

    pytest.fail(f"worker {node_name} did not answer before the startup timeout")


def _stop_worker(
    process: subprocess.Popen[bytes],
    node_name: str,
    *,
    abrupt: bool,
) -> None:
    """Stop one worker process within a bounded cleanup window.

    Uses an abrupt stop only for worker-loss verification and otherwise asks Celery to finish and
    exit, escalating only if the process exceeds the cleanup budget.

    Arguments:
        process: Worker process to stop.
        node_name: Exact Celery node name assigned to the process.
        abrupt: Whether to simulate immediate worker loss.

    Returns:
        None.

    Raises:
        AssertionError: If the graceful shutdown request cannot reach the worker.
    """
    if process.poll() is not None:
        return

    shutdown_error: OSError | OperationalError | None = None
    if abrupt:
        process.kill()
    else:
        try:
            app.control.shutdown(destination=[node_name])
        except (OSError, OperationalError) as error:
            shutdown_error = error
            process.terminate()

    try:
        process.wait(timeout=WORKER_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=WORKER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=WORKER_STOP_TIMEOUT_SECONDS)

    if not abrupt and shutdown_error is not None:
        msg = "worker graceful shutdown request failed"
        raise AssertionError(msg) from shutdown_error


@contextmanager
def _worker(
    queue_names: str,
    node_name: str,
    log_path: Path,
    *,
    pool: str = "solo",
    wait_for_ping: bool = True,
) -> Iterator[subprocess.Popen[bytes]]:
    """Run one real Celery worker process for an isolated queue.

    Starts the same project application object the deployed service uses with a solo pool so the
    test behaves consistently on Windows and Linux, then guarantees process cleanup.

    Arguments:
        queue_names: Comma-separated per-test queues consumed by the worker.
        node_name: Per-test Celery node name.
        log_path: File receiving worker output for failure diagnosis.
        pool: Worker execution pool selected for the process.
        wait_for_ping: Whether startup must complete a remote-control round trip before yielding.

    Yields:
        Running worker process.
    """
    environment = dict(os.environ)
    environment["CELERY_TASK_ALWAYS_EAGER"] = "false"
    queue_list = queue_names.split(",")
    default_queue = queue_list[0]
    slow_queue = queue_list[1] if len(queue_list) > 1 else f"{default_queue}.slow"
    environment["LOCALFORGE_TEST_CELERY_DEFAULT_QUEUE"] = default_queue
    environment["LOCALFORGE_TEST_CELERY_SLOW_QUEUE"] = slow_queue
    environment["LOCALFORGE_TEST_CELERY_DEAD_LETTER_QUEUE"] = f"{default_queue}.dead"
    python_path = [str(REPOSITORY_ROOT / "src"), str(REPOSITORY_ROOT)]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    concurrency = THREAD_WORKER_CONCURRENCY if pool == "threads" else 1
    with app.connection_for_write() as connection:
        channel = connection.channel()
        for queue_name in {default_queue, slow_queue}:
            Queue(
                queue_name,
                Exchange(queue_name, type="topic", durable=True),
                routing_key=queue_name,
                durable=True,
                queue_arguments={"x-queue-type": "quorum"},
            )(channel).declare()
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "config",
                "worker",
                f"--pool={pool}",
                f"--concurrency={concurrency}",
                f"--queues={queue_names}",
                f"--hostname={node_name}",
                "--loglevel=WARNING",
                "--without-gossip",
                "--without-mingle",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            if wait_for_ping:
                _wait_for_worker(node_name)
            yield process
        finally:
            _stop_worker(process, node_name, abrupt=False)
            if slow_queue not in queue_list:
                _delete_queue(slow_queue)


def _delete_queue(queue_name: str) -> None:
    """Delete one test-owned broker queue.

    Removes the durable queue after its worker is gone so parallel and later runs cannot inherit
    pending probe work.

    Arguments:
        queue_name: Queue to remove.

    Returns:
        None.
    """
    with app.connection_for_write() as connection:
        channel = connection.channel()
        channel.queue_delete(queue_name)
        channel.exchange_delete(queue_name)
    app.amqp.queues.pop(queue_name, None)


@pytest.mark.integration
@pytest.mark.services("rabbitmq", "valkey-cache")
@pytest.mark.serial
@pytest.mark.xdist_group("serial")
@pytest.mark.timeout(90)
def test_a_real_worker_process_executes_a_brokered_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    worker_namespace: str,
) -> None:
    """Execute one task in the real worker command and retrieve its result.

    Publishes to a per-test queue consumed only by the separate worker process, proving the broker,
    worker application, task import, execution, and Valkey result path together. This test is
    serial because concurrent real-worker control startup saturates the shared testing broker.

    Arguments:
        monkeypatch: Fixture disabling the testing environment's eager default.
        tmp_path: Directory receiving bounded worker logs.
        worker_namespace: Per-worker prefix for queue and node isolation.

    Returns:
        None.

    Raises:
        AssertionError: If the separate worker does not return the probe result.
    """
    monkeypatch.setitem(app.conf, "CELERY_TASK_ALWAYS_EAGER", EAGER_DISABLED)
    suffix = uuid.uuid4().hex
    queue_name = f"{worker_namespace}-worker-{suffix}"
    node_name = f"worker-{suffix}@localforge"
    probe_id = f"probe-{suffix}"

    result: AsyncResult | None = None
    try:
        with _worker(queue_name, node_name, tmp_path / "worker.log"):
            result = worker_probe.apply_async(args=(probe_id,), queue=queue_name)

            assert result.get(timeout=RESULT_TIMEOUT_SECONDS) == probe_id
            assert result.successful()
    finally:
        _dispose_result(result)
        result = None
        gc.collect()
        _delete_queue(queue_name)


@pytest.mark.integration
@pytest.mark.services("rabbitmq", "valkey-cache")
@pytest.mark.serial
@pytest.mark.xdist_group("serial")
@pytest.mark.timeout(120)
def test_worker_loss_redelivers_an_in_flight_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    worker_namespace: str,
) -> None:
    """Redeliver one acknowledged-late task after abruptly losing its worker.

    Waits for the result backend's started state, kills the real worker process, starts a
    replacement on the same queue, and requires the original task identifier to complete. This test
    is serial because concurrent real-worker control startup saturates the shared testing broker.

    Arguments:
        monkeypatch: Fixture disabling the testing environment's eager default.
        tmp_path: Directory receiving both worker logs.
        worker_namespace: Per-worker prefix for queue and node isolation.

    Returns:
        None.

    Raises:
        AssertionError: If the task is lost, duplicated into another result, or never completes.
    """
    monkeypatch.setitem(app.conf, "CELERY_TASK_ALWAYS_EAGER", EAGER_DISABLED)
    suffix = uuid.uuid4().hex
    queue_name = f"{worker_namespace}-restart-{suffix}"
    node_name = f"restart-{suffix}@localforge"
    probe_id = f"restart-probe-{suffix}"

    redelivered_result: AsyncResult | None = None
    result: AsyncResult | None = None
    try:
        with _worker(queue_name, node_name, tmp_path / "worker-first.log") as first:
            result = slow_worker_probe.apply_async(
                args=(probe_id, SLOW_PROBE_SECONDS),
                queue=queue_name,
            )
            deadline = time.monotonic() + RESULT_TIMEOUT_SECONDS
            while result.state != "STARTED" and time.monotonic() < deadline:
                time.sleep(STATE_POLL_SECONDS)

            assert result.state == "STARTED"
            _stop_worker(first, node_name, abrupt=True)

        with _worker(
            queue_name,
            node_name,
            tmp_path / "worker-second.log",
            wait_for_ping=False,
        ):
            redelivered_result = cast("AsyncResult", app.AsyncResult(result.id))
            assert redelivered_result.get(timeout=RESULT_TIMEOUT_SECONDS) == probe_id
            assert redelivered_result.successful()
    finally:
        _dispose_result(redelivered_result)
        _dispose_result(result)
        redelivered_result = None
        result = None
        gc.collect()
        _delete_queue(queue_name)


@pytest.mark.integration
@pytest.mark.services("rabbitmq", "valkey-cache")
@pytest.mark.serial
@pytest.mark.xdist_group("serial")
@pytest.mark.timeout(90)
def test_one_slow_task_does_not_starve_fast_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    worker_namespace: str,
) -> None:
    """Complete fast work while one isolated slow task occupies another worker slot.

    Runs the real worker command with the production minimum concurrency and both explicit queue
    classes, then requires the fast result before the slow task can finish. This test is serial
    because concurrent real-worker control startup saturates the shared testing broker.

    Arguments:
        monkeypatch: Fixture disabling the testing environment's eager default.
        tmp_path: Directory receiving bounded worker logs.
        worker_namespace: Per-worker prefix for queue and node isolation.

    Returns:
        None.

    Raises:
        AssertionError: If one slow task blocks the fast queue.
    """
    monkeypatch.setitem(app.conf, "CELERY_TASK_ALWAYS_EAGER", EAGER_DISABLED)
    suffix = uuid.uuid4().hex
    fast_queue = f"{worker_namespace}-fast-{suffix}"
    slow_queue = f"{worker_namespace}-slow-{suffix}"
    node_name = f"isolation-{suffix}@localforge"
    slow_id = f"slow-{suffix}"
    fast_id = f"fast-{suffix}"

    fast: AsyncResult | None = None
    slow: AsyncResult | None = None
    try:
        with _worker(
            f"{fast_queue},{slow_queue}",
            node_name,
            tmp_path / "worker-isolation.log",
            pool="threads",
        ):
            slow = slow_worker_probe.apply_async(
                args=(slow_id, SLOW_PROBE_SECONDS),
                queue=slow_queue,
            )
            deadline = time.monotonic() + RESULT_TIMEOUT_SECONDS
            while slow.state != "STARTED" and time.monotonic() < deadline:
                time.sleep(STATE_POLL_SECONDS)

            assert slow.state == "STARTED"
            fast = worker_probe.apply_async(args=(fast_id,), queue=fast_queue)

            assert fast.get(timeout=RESULT_TIMEOUT_SECONDS / 2) == fast_id
            assert not slow.ready()
            assert slow.get(timeout=RESULT_TIMEOUT_SECONDS) == slow_id
    finally:
        _dispose_result(fast)
        _dispose_result(slow)
        fast = None
        slow = None
        gc.collect()
        _delete_queue(fast_queue)
        _delete_queue(slow_queue)
