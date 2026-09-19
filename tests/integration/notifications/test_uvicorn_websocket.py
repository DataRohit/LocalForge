"""Live Uvicorn tests for client-visible WebSocket close behavior.

Serves the deployed ASGI application with the pinned protocol backend so handshake translation and
parser failures are verified beyond the in-process communicator seam.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from notifications.websocket import ExactWebSocketOriginValidator, NotificationConsumer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope
    from websockets.typing import Origin

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SERVER_START_TIMEOUT_SECONDS = 20
SERVER_STOP_TIMEOUT_SECONDS = 10
RECEIVE_TIMEOUT_SECONDS = 10
PERMISSION_DENIED_CLOSE_CODE = 4406
MALFORMED_FRAME_CLOSE_CODE = 4400
TEST_ACCOUNT_ID = UUID("018f22e2-7d42-7f74-9d8a-123456789abc")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.services("valkey-channels"),
]


class FixedAccountScope:
    """Install one immutable account identity for transport-only live tests.

    Wraps an ASGI application and supplies the authenticated scope state Ticket 39 proves
    independently, allowing these tests to isolate Uvicorn framing and protocol behavior.

    Attributes:
        application: Inner transport test application receiving the account scope.

    Members:
        __call__: Copy one scope, install the account identity, and delegate.
    """

    def __init__(self, application: ASGI3Application) -> None:
        """Store the transport test application.

        Keeps authentication replacement local to the test process while the deployed application
        remains protected by the production JSON web token middleware.

        Arguments:
            application: Inner ASGI application receiving the test account.

        Returns:
            None.
        """
        self.application = application

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Install the fixed account identity and delegate one connection.

        Copies the incoming scope before adding the immutable primary key so no transport-owned
        state is mutated in place.

        Arguments:
            scope: Incoming ASGI WebSocket scope.
            receive: Callable yielding connection events.
            send: Callable emitting connection events.

        Returns:
            None.
        """
        authenticated_scope = dict(scope)
        authenticated_scope["user"] = SimpleNamespace(pk=TEST_ACCOUNT_ID)
        await self.application(cast("Scope", authenticated_scope), receive, send)


live_protocol_application = ProtocolTypeRouter(
    {
        "websocket": ExactWebSocketOriginValidator(
            FixedAccountScope(
                URLRouter([path("ws/notifications/", NotificationConsumer.as_asgi())])
            )
        )
    }
)


def worker_server_port(worker_id: str) -> int:
    """Choose one deterministic loopback port per parallel worker.

    Separates live Uvicorn processes across xdist workers while retaining a fixed serial fallback,
    preventing one test process from connecting to another's server.

    Arguments:
        worker_id: Pytest worker identifier such as ``gw3`` or ``main``.

    Returns:
        Loopback port reserved by convention for this test worker.
    """
    if worker_id == "main":
        return 28999

    return 28900 + int(worker_id.removeprefix("gw"))


async def wait_for_server(process: subprocess.Popen[str], port: int) -> None:
    """Wait until the child Uvicorn listener accepts a loopback connection.

    Polls with a bounded delay and surfaces captured process output when startup exits early, making
    a failed live seam diagnosable without leaving a child process behind.

    Arguments:
        process: Uvicorn child process being observed.
        port: Loopback port the child must bind.

    Returns:
        None.

    Raises:
        AssertionError: If the process exits or does not listen before the timeout.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SERVER_START_TIMEOUT_SECONDS

    while loop.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = await asyncio.to_thread(process.communicate)
            message = f"Uvicorn exited before listening.\nstdout:\n{stdout}\nstderr:\n{stderr}"
            raise AssertionError(message)

        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
        else:
            writer.close()
            await writer.wait_closed()
            return

    message = f"Uvicorn did not listen on port {port} within the startup timeout"
    raise AssertionError(message)


@asynccontextmanager
async def running_uvicorn(
    worker_id: str,
    application_target: str = (
        "tests.integration.notifications.test_uvicorn_websocket:live_protocol_application"
    ),
) -> AsyncIterator[str]:
    """Run the pinned Uvicorn WebSocket backend for one test.

    Starts the real ASGI entry point with the same sans-I/O backend used by the container, yields
    its loopback URI, and terminates the exact child process on every exit path.

    Arguments:
        worker_id: Pytest worker identifier used to choose an isolated port.
        application_target: Import path of the ASGI application the child serves.

    Yields:
        Base WebSocket URI for the child server.

    Raises:
        AssertionError: If the server cannot start or stop cleanly.
    """
    port = worker_server_port(worker_id)
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.testing",
        "PYTHONPATH": f"{REPOSITORY_ROOT / 'src'}{os.pathsep}{REPOSITORY_ROOT}",
    }
    process = cast(
        "subprocess.Popen[str]",
        await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "-m",
                "uvicorn",
                application_target,
                "--app-dir",
                "src",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--log-level",
                "warning",
                "--no-access-log",
                "--ws",
                "websockets-sansio",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ),
    )

    try:
        await wait_for_server(process, port)
        yield f"ws://127.0.0.1:{port}/ws/notifications/"
    finally:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, SERVER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            process.kill()
            await asyncio.to_thread(process.wait, SERVER_STOP_TIMEOUT_SECONDS)
            message = "Uvicorn did not stop within the timeout"
            raise AssertionError(message) from error


async def observed_close_code(
    uri: str,
    *,
    origin: str,
    frame: str | None = None,
) -> int:
    """Return the close code a real client observes from Uvicorn.

    Connects with one origin, optionally sends a literal frame, and waits for the deployed server
    to close, converting the client library's exception into the protocol evidence under test.

    Arguments:
        uri: Complete WebSocket URI served by the child process.
        origin: Browser origin supplied during the handshake.
        frame: Optional literal text frame sent after connection.

    Returns:
        Close code carried by the server's close frame.

    Raises:
        AssertionError: If the server does not close within the timeout.
    """
    async with connect(
        uri,
        origin=cast("Origin", origin),
        open_timeout=RECEIVE_TIMEOUT_SECONDS,
    ) as websocket:
        if frame is not None:
            await websocket.send(frame)

        with pytest.raises(ConnectionClosedError) as captured:
            await asyncio.wait_for(websocket.recv(), timeout=RECEIVE_TIMEOUT_SECONDS)

    close_frame = captured.value.rcvd
    assert close_frame is not None

    return close_frame.code


async def repeated_origin_status(uri: str) -> int:
    """Return Uvicorn's transport status for an ambiguous Origin handshake.

    Supplies two Origin fields so the pinned protocol backend rejects the invalid handshake before
    ASGI dispatch, documenting the one origin shape that cannot carry an application close frame.

    Arguments:
        uri: Complete WebSocket URI served by the child process.

    Returns:
        HTTP status code returned by Uvicorn.

    Raises:
        AssertionError: If the handshake is not rejected by the transport.
    """
    port = int(uri.split(":", maxsplit=2)[2].split("/", maxsplit=1)[0])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = (
        "GET /ws/notifications/ HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: http://localhost:8080\r\n"
        "Origin: http://localhost:8080\r\n"
        "\r\n"
    )

    try:
        writer.write(request.encode("ascii"))
        await writer.drain()
        response = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"),
            timeout=RECEIVE_TIMEOUT_SECONDS,
        )
    finally:
        writer.close()
        await writer.wait_closed()

    status_line = response.split(b"\r\n", maxsplit=1)[0]

    return int(status_line.split()[1])


async def invalid_subprotocol_status(uri: str) -> int:
    """Return Uvicorn's status for a syntactically invalid subprotocol.

    Sends a non-ASCII protocol byte through a raw handshake so the pinned transport parser, rather
    than a client library or ASGI communicator, owns the rejection under test.

    Arguments:
        uri: Complete WebSocket URI served by the child process.

    Returns:
        HTTP status code returned before ASGI dispatch.

    Raises:
        AssertionError: If the transport does not return a complete HTTP response.
    """
    port = int(uri.split(":", maxsplit=2)[2].split("/", maxsplit=1)[0])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = (
        "GET /ws/notifications/ HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: http://localhost:8080\r\n"
        "Sec-WebSocket-Protocol: invalid-\N{LATIN SMALL LETTER Y WITH DIAERESIS}\r\n"
        "\r\n"
    )

    try:
        writer.write(request.encode("latin-1"))
        await writer.drain()
        response = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"),
            timeout=RECEIVE_TIMEOUT_SECONDS,
        )
    finally:
        writer.close()
        await writer.wait_closed()

    status_line = response.split(b"\r\n", maxsplit=1)[0]

    return int(status_line.split()[1])


@pytest.mark.asyncio
async def test_uvicorn_exposes_origin_rejections(worker_id: str) -> None:
    """Deliver both deployed origin rejection forms through the real server.

    Proves an unlisted origin receives the application close code while repeated Origin fields are
    rejected by the pinned transport before ASGI dispatch.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If a client observes any code other than the documented one.
    """
    async with running_uvicorn(worker_id) as uri:
        ambiguous_origin_status = await repeated_origin_status(uri)
        origin_code = await observed_close_code(uri, origin="http://untrusted.invalid")

    assert origin_code == PERMISSION_DENIED_CLOSE_CODE
    assert ambiguous_origin_status == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_uvicorn_rejects_invalid_subprotocol_before_authentication(worker_id: str) -> None:
    """Reject non-ASCII subprotocol syntax before the production ASGI application.

    Serves the deployed authentication stack and proves the pinned transport returns HTTP ``400``
    before middleware can classify or log the invalid header.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If the invalid header reaches ASGI or receives another status.
    """
    async with running_uvicorn(worker_id, "config.asgi:application") as uri:
        status = await invalid_subprotocol_status(uri)

    assert status == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_uvicorn_rejects_nonstandard_and_duplicate_json(worker_id: str) -> None:
    """Contain non-standard constants and ambiguous object members as malformed.

    Exercises the strict decoder through the pinned server so invalid numeric spellings and
    duplicate envelope or payload members all deliver close code ``4400`` to the client.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If any malformed frame receives another close code.
    """
    async with running_uvicorn(worker_id) as uri:
        nan_code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
            frame='{"type":"unsupported","payload":{"value":NaN}}',
        )
        duplicate_code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
            frame='{"type":"first","type":"second","payload":{}}',
        )
        nested_duplicate_code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
            frame='{"type":"unsupported","payload":{"key":1,"key":2}}',
        )

    assert nan_code == MALFORMED_FRAME_CLOSE_CODE
    assert duplicate_code == MALFORMED_FRAME_CLOSE_CODE
    assert nested_duplicate_code == MALFORMED_FRAME_CLOSE_CODE


@pytest.mark.asyncio
async def test_uvicorn_contains_parser_depth_failure(worker_id: str) -> None:
    """Close deeply nested JSON without destabilizing the server.

    Drives the parser recursion failure through the real protocol backend and observes the
    application malformed-frame code rather than an abnormal transport closure.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If parser recursion escapes the application boundary.
    """
    async with running_uvicorn(worker_id) as uri:
        depth_code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
            frame="[" * 20000 + "]" * 20000,
        )

    assert depth_code == MALFORMED_FRAME_CLOSE_CODE
