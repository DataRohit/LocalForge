"""Cross-process integration test for background task notification fan-out.

Exercises the authenticated username-change request through RabbitMQ and a real worker process,
then observes the completion event on the deployed notification WebSocket.
"""

from __future__ import annotations

import asyncio
import gc
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client as DjangoClient
from kombu.exceptions import OperationalError
from rest_framework.authtoken.models import Token

from accounts.jwt_authentication import PrimaryRefreshToken
from config.asgi import application
from config.celery import app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from celery.result import AsyncResult

    from accounts.models import User

ROUTE = "/ws/notifications/"
HEADERS = [(b"host", b"localhost"), (b"origin", b"http://localhost:8080")]
WORKER_START_TIMEOUT_SECONDS = 40
WORKER_STOP_TIMEOUT_SECONDS = 15
RECEIVE_TIMEOUT_SECONDS = 30
NO_MESSAGE_TIMEOUT_SECONDS = 0.2
STATE_POLL_SECONDS = 0.1
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TASK_ID_PATTERN = re.compile(
    r"Task accounts\.send_username_changed_email\["
    r"(?P<task_id>[0-9a-f-]{36})\] received"
)
EAGER_DISABLED = False


def _wait_for_worker(node_name: str) -> None:
    """Wait for one named worker to answer remote control.

    Polls the real broker until the process is ready or the bounded startup budget expires. This
    proves task consumption can begin before the request is issued.

    Arguments:
        node_name: Exact Celery node name assigned to the process.

    Returns:
        None.

    Raises:
        AssertionError: If the worker never answers before the deadline.
    """
    deadline = time.monotonic() + WORKER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if app.control.ping(destination=[node_name], timeout=1):
            return
        time.sleep(STATE_POLL_SECONDS)

    pytest.fail(f"worker {node_name} did not answer before the startup timeout")


def _stop_worker(process: subprocess.Popen[bytes], node_name: str) -> None:
    """Stop one worker process within the cleanup budget.

    Requests ordinary termination and escalates only when the worker exceeds the bounded wait. The
    helper never leaves a child process behind after a failed assertion.

    Arguments:
        process: Worker process to stop.
        node_name: Exact Celery node name assigned to the process.

    Returns:
        None.

    Raises:
        AssertionError: If the graceful shutdown request cannot reach the worker.
    """
    if process.poll() is not None:
        return
    shutdown_error: OSError | OperationalError | None = None
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

    if shutdown_error is not None:
        msg = "worker graceful shutdown request failed"
        raise AssertionError(msg) from shutdown_error


@contextmanager
def _worker(queue_name: str, node_name: str, log_path: Path) -> Iterator[None]:
    """Run one real worker against the active pytest database.

    Overrides only environment-specific database and queue values, starts the same Celery
    application as development, and guarantees process cleanup.

    Arguments:
        queue_name: Per-test queue consumed by the worker.
        node_name: Per-test Celery node name.
        log_path: File receiving worker output and the completed task identifier.

    Yields:
        None while the worker is ready.
    """
    environment = dict(os.environ)
    environment["CELERY_TASK_ALWAYS_EAGER"] = "false"
    environment["CELERY_DEFAULT_QUEUE"] = queue_name
    environment["POSTGRES_DB"] = str(settings.DATABASES["default"]["NAME"])
    python_path = [str(REPOSITORY_ROOT / "src"), str(REPOSITORY_ROOT)]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "config",
                "worker",
                "--pool=solo",
                "--concurrency=1",
                f"--queues={queue_name}",
                f"--hostname={node_name}",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_worker(node_name)
            yield
        finally:
            _stop_worker(process, node_name)


async def _issue_access_token(account: User) -> str:
    """Issue the REST-compatible access credential for one account.

    Uses the same primary-backed token implementation as the public endpoint so the socket crosses
    deployed authentication rather than a fabricated scope.

    Arguments:
        account: Active account receiving the credential.

    Returns:
        Encoded access token accepted by REST and WebSocket authentication.
    """
    refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)
    return str(refresh.access_token)


async def _connect_socket(access: str) -> WebsocketCommunicator:
    """Open one authenticated notification socket.

    Presents the REST credential as the sole WebSocket subprotocol and verifies the deployed
    application echoes it before returning.

    Arguments:
        access: REST-compatible access credential.

    Returns:
        Connected communicator.

    Raises:
        AssertionError: If routing or authentication fails.
    """
    communicator = WebsocketCommunicator(
        application,
        ROUTE,
        headers=HEADERS,
        subprotocols=[access],
    )
    connected, accepted = cast(
        "tuple[bool, str | int | None]",
        await communicator.connect(),
    )
    assert connected
    assert accepted == access
    return communicator


def _change_username(authorization: str, password: str, username: str) -> tuple[int, bytes]:
    """Submit one authenticated username change through the public HTTP route.

    Creates an isolated synchronous client for the worker thread and returns only response fields
    safe to inspect back on the event loop.

    Arguments:
        authorization: Complete API authorization header.
        password: Current account password.
        username: New unique username.

    Returns:
        Response status and body.
    """
    client = DjangoClient()
    response = client.post(
        "/api/v1/users/set_username/",
        {"current_password": password, "new_username": username},
        content_type="application/json",
        headers={"authorization": authorization},
    )
    return response.status_code, response.content


def _task_id_from_log(log_path: Path) -> str:
    """Read the notification task identifier from worker logs.

    Polls the separate process log until the task receipt record appears, retaining a bounded
    cleanup handle for the result backend.

    Arguments:
        log_path: Worker log containing the receipt record.

    Returns:
        Notification task identifier.

    Raises:
        AssertionError: If the receipt record is absent before the timeout.
    """
    deadline = time.monotonic() + RECEIVE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        match = TASK_ID_PATTERN.search(log_path.read_text(encoding="utf-8"))
        if match is not None:
            task_id = match.group("task_id")
            assert isinstance(task_id, str)
            return task_id
        time.sleep(STATE_POLL_SECONDS)

    pytest.fail("notification task receipt was absent from worker logs")


@pytest.mark.integration
@pytest.mark.services("postgres", "rabbitmq", "valkey-cache", "valkey-channels")
@pytest.mark.serial
@pytest.mark.xdist_group("serial")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_username_change_request_fans_out_after_real_worker_completion(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    worker_namespace: str,
) -> None:
    """Deliver the username-change completion event across request, worker, and socket processes.

    Opens an authenticated socket, submits the existing request, executes the queued email task in
    a separate real worker, and requires the exact public notification envelope. This test is
    serial because concurrent real-worker control startup saturates the shared testing broker.

    Arguments:
        django_user_model: Configured account model used to create the recipient.
        monkeypatch: Fixture selecting real broker execution and a per-test queue.
        tmp_path: Directory receiving the worker log.
        worker_namespace: Per-worker prefix isolating broker state.

    Returns:
        None.

    Raises:
        AssertionError: If any boundary fails, the event is missing, or cleanup leaks state.
    """
    suffix = uuid.uuid4().hex
    password = f"Valid-Password-{suffix}"
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        f"fanout-before-{suffix}",
        f"fanout-{suffix}@localforge.invalid",
        password,
        is_active=True,
    )
    other_account = await database_sync_to_async(django_user_model.objects.create_user)(
        f"fanout-other-{suffix}",
        f"fanout-other-{suffix}@localforge.invalid",
        password,
        is_active=True,
    )
    access = await _issue_access_token(account)
    other_access = await _issue_access_token(other_account)
    api_token = await database_sync_to_async(Token.objects.using("default").create)(user=account)
    queue_name = f"{worker_namespace}-fanout-{suffix}"
    node_name = f"fanout-{suffix}@localforge"
    log_path = tmp_path / "worker.log"
    monkeypatch.setitem(app.conf, "CELERY_TASK_ALWAYS_EAGER", EAGER_DISABLED)
    monkeypatch.setitem(app.conf, "CELERY_TASK_DEFAULT_QUEUE", queue_name)
    result: AsyncResult | None = None
    communicator: WebsocketCommunicator | None = None
    other_communicator: WebsocketCommunicator | None = None

    try:
        communicator = await _connect_socket(access)
        other_communicator = await _connect_socket(other_access)
        with _worker(queue_name, node_name, log_path):
            status, body = await asyncio.to_thread(
                _change_username,
                f"Token {api_token.key}",
                password,
                f"fanout-after-{suffix}",
            )
            event = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)
            task_id = await asyncio.to_thread(_task_id_from_log, log_path)
            result = cast("AsyncResult", app.AsyncResult(task_id))
            assert await asyncio.to_thread(result.get, timeout=RECEIVE_TIMEOUT_SECONDS) is True

        assert status == HTTPStatus.NO_CONTENT
        assert body == b""
        assert event == {
            "type": "notification",
            "payload": {
                "event": "account.username_changed",
                "data": {},
            },
        }
        assert await communicator.receive_nothing(timeout=NO_MESSAGE_TIMEOUT_SECONDS)
        assert await other_communicator.receive_nothing(timeout=NO_MESSAGE_TIMEOUT_SECONDS)
        await database_sync_to_async(account.refresh_from_db)(using="default")
        assert account.username == f"fanout-after-{suffix}"
    finally:
        if result is not None:
            result.forget()
            app.backend.remove_pending_result(result)
            result = None
            gc.collect()
        if communicator is not None and not communicator.future.done():
            await communicator.disconnect()
        if other_communicator is not None and not other_communicator.future.done():
            await other_communicator.disconnect()
        with app.connection_for_write() as connection:
            connection.channel().queue_delete(queue_name)
