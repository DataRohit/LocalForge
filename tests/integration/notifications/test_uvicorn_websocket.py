"""Live Uvicorn tests for client-visible WebSocket close behavior.

Serves the deployed ASGI application with the pinned protocol backend so handshake translation and
parser failures are verified beyond the in-process communicator seam.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast, override
from uuid import UUID, uuid4

import pytest
from channels.db import database_sync_to_async
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.db import connections
from django.urls import path
from django.utils import timezone
from freezegun import freeze_time
from redis import asyncio as aioredis
from uvicorn import Config, Server
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Frame, Opcode

from notifications.admission import WebSocketConnectionAdmissionMiddleware
from notifications.protocol import WebSocketOutcome
from notifications.websocket import (
    ExactWebSocketOriginValidator,
    NotificationConsumer,
    WebSocketFailureBoundary,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from asgiref.typing import (
        ASGI3Application,
        ASGIReceiveCallable,
        ASGISendCallable,
        ASGISendEvent,
        Scope,
    )
    from websockets.typing import Origin, Subprotocol

    from accounts.models import User

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SERVER_START_TIMEOUT_SECONDS = 40
SERVER_STOP_TIMEOUT_SECONDS = 10
RECEIVE_TIMEOUT_SECONDS = 10
TRANSPORT_MESSAGE_TOO_BIG_CLOSE_CODE = 1009
TRANSPORT_PROTOCOL_ERROR_CLOSE_CODE = 1002
TRANSPORT_INVALID_UTF8_CLOSE_CODE = 1007
TRANSPORT_INTERNAL_ERROR_CLOSE_CODE = 1011
TRANSPORT_RESTART_CLOSE_CODE = 1012
SHORT_EXTENDED_FRAME_LENGTH = 126
LONG_EXTENDED_FRAME_LENGTH = 127
CLOSE_CODE_PAYLOAD_BYTES = 2
TEST_ACCOUNT_ID = UUID("018f22e2-7d42-7f74-9d8a-123456789abc")
ADMISSION_TEST_ACCOUNT_ID = UUID("018f22e2-7d42-7f74-9d8a-abcdefabcdef")

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

    def __init__(
        self,
        application: ASGI3Application,
        account_id: UUID = TEST_ACCOUNT_ID,
    ) -> None:
        """Store the transport test application.

        Keeps authentication replacement local to the test process while the deployed application
        remains protected by the production JSON web token middleware.

        Arguments:
            application: Inner ASGI application receiving the test account.
            account_id: Immutable account identifier installed in each scope.

        Returns:
            None.
        """
        self.application = application
        self.account_id = account_id

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
        authenticated_scope["user"] = SimpleNamespace(pk=self.account_id)
        await self.application(cast("Scope", authenticated_scope), receive, send)


class ExplodingNotificationConsumer(NotificationConsumer):
    """Raise one unexpected failure after the WebSocket is accepted.

    Inherits from ``NotificationConsumer`` and replaces recoverable error delivery only in the
    child-process test application, exercising the production failure boundary over Uvicorn.

    Attributes:
        None beyond those inherited from ``NotificationConsumer``.

    Members:
        _send_error: Raise the contained failure.
    """

    @override
    async def _send_error(self, outcome: WebSocketOutcome) -> None:
        """Raise the failure handled by the outer boundary.

        Replaces only recoverable frame delivery so the accepted connection reaches the same
        production consumer lifecycle before failing.

        Arguments:
            outcome: Recoverable outcome that triggered dispatch.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        del outcome
        message = "contained live consumer failure"
        raise RuntimeError(message)


live_protocol_application = ProtocolTypeRouter(
    {
        "websocket": WebSocketFailureBoundary(
            ExactWebSocketOriginValidator(
                FixedAccountScope(
                    URLRouter([path("ws/notifications/", NotificationConsumer.as_asgi())])
                )
            )
        )
    }
)

live_failure_application = ProtocolTypeRouter(
    {
        "websocket": WebSocketFailureBoundary(
            ExactWebSocketOriginValidator(
                FixedAccountScope(
                    URLRouter(
                        [
                            path(
                                "ws/notifications/",
                                ExplodingNotificationConsumer.as_asgi(),
                            )
                        ]
                    )
                )
            )
        )
    }
)

live_admission_application = ProtocolTypeRouter(
    {
        "websocket": WebSocketFailureBoundary(
            ExactWebSocketOriginValidator(
                FixedAccountScope(
                    WebSocketConnectionAdmissionMiddleware(
                        URLRouter(
                            [
                                path(
                                    "ws/notifications/",
                                    NotificationConsumer.as_asgi(),
                                )
                            ]
                        )
                    ),
                    ADMISSION_TEST_ACCOUNT_ID,
                )
            )
        )
    }
)


async def idle_transport_application(
    scope: Scope,
    receive: ASGIReceiveCallable,
    send: ASGISendCallable,
) -> None:
    """Accept one socket and wait only for its transport disconnect.

    Provides no application failure handling or channel-layer lifecycle, isolating close codes
    generated solely by the pinned Uvicorn WebSocket implementation.

    Arguments:
        scope: Incoming ASGI WebSocket scope.
        receive: Callable yielding transport lifecycle events.
        send: Callable accepting the connection.

    Returns:
        None.

    Raises:
        AssertionError: If invoked for another protocol or before a connect event.
    """
    assert scope["type"] == "websocket"
    event = await receive()
    assert event["type"] == "websocket.connect"
    await send(cast("ASGISendEvent", {"type": "websocket.accept"}))

    while True:
        event = await receive()
        if event["type"] == "websocket.disconnect":
            return


async def clear_admission_test_key() -> None:
    """Delete the deterministic direct-Uvicorn admission test key.

    Keeps repeated test runs independent without flushing any other account or channel-layer
    state, limiting cleanup to the fixed synthetic account.

    Arguments:
        None.

    Returns:
        None.
    """
    layer = cast("dict[str, object]", settings.CHANNEL_LAYERS["default"])
    configuration = cast("dict[str, object]", layer["CONFIG"])
    host = cast("list[dict[str, object]]", configuration["hosts"])[0]
    key = f"{configuration['prefix']}:websocket-admission:{ADMISSION_TEST_ACCOUNT_ID.hex}"
    async with aioredis.Redis(
        host=cast("str", host["host"]),
        port=cast("int", host["port"]),
        password=cast("str", host["password"]),
    ) as client:
        await client.delete(key)


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
        return 29999

    return 28900 + int(worker_id.removeprefix("gw")) * 20


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
    environment_overrides: dict[str, str] | None = None,
    port_offset: int = 0,
) -> AsyncIterator[str]:
    """Run the pinned Uvicorn WebSocket backend for one test.

    Starts the real ASGI entry point with the same sans-I/O backend used by the container, yields
    its loopback URI, and terminates the exact child process on every exit path.

    Arguments:
        worker_id: Pytest worker identifier used to choose an isolated port.
        application_target: Import path of the ASGI application the child serves.
        environment_overrides: Optional child-process environment replacements.
        port_offset: Offset within the worker's reserved port range.

    Yields:
        Base WebSocket URI for the child server.

    Raises:
        AssertionError: If the server cannot start or stop cleanly.
    """
    port = worker_server_port(worker_id) + port_offset
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.testing",
        "PYTHONPATH": f"{REPOSITORY_ROOT / 'src'}{os.pathsep}{REPOSITORY_ROOT}",
        **(environment_overrides or {}),
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
                "--ws-max-size",
                os.environ["UVICORN_WEBSOCKET_MAX_SIZE_BYTES"],
                "--lifespan",
                "off",
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
        if process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, SERVER_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                process.kill()
                await asyncio.to_thread(process.wait, SERVER_STOP_TIMEOUT_SECONDS)
                message = "Uvicorn did not stop within the timeout"
                raise AssertionError(message) from error
        await asyncio.to_thread(process.communicate)


@asynccontextmanager
async def running_in_process_uvicorn(
    worker_id: str,
    *,
    port_offset: int,
    ping_interval: float | None = 20.0,
    ping_timeout: float | None = 20.0,
) -> AsyncIterator[tuple[str, Server, asyncio.Task[None]]]:
    """Run a controllable pinned Uvicorn server for transport lifecycle tests.

    Serves the transport-only application on an isolated port while exposing Uvicorn's shutdown
    switch and task so keepalive and restart outcomes are deterministic.

    Arguments:
        worker_id: Pytest worker identifier used to choose an isolated port.
        port_offset: Offset within the worker's reserved port range.
        ping_interval: Delay before Uvicorn sends a keepalive ping.
        ping_timeout: Delay Uvicorn permits before a matching pong.

    Yields:
        Base URI, server controller, and serving task.

    Raises:
        AssertionError: If the server cannot start or stop within its bounds.
    """
    port = worker_server_port(worker_id) + port_offset
    server = Server(
        Config(
            idle_transport_application,
            host="127.0.0.1",
            port=port,
            workers=1,
            log_level="warning",
            access_log=False,
            ws="websockets-sansio",
            ws_max_size=int(os.environ["UVICORN_WEBSOCKET_MAX_SIZE_BYTES"]),
            ws_ping_interval=ping_interval,
            ws_ping_timeout=ping_timeout,
            lifespan="off",
            timeout_graceful_shutdown=2,
        )
    )
    task = asyncio.create_task(server.serve())

    try:
        await wait_for_server_process(task, port)
        yield f"ws://127.0.0.1:{port}/ws/notifications/", server, task
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=SERVER_STOP_TIMEOUT_SECONDS)
        except TimeoutError as error:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            message = "in-process Uvicorn did not stop within the timeout"
            raise AssertionError(message) from error


async def wait_for_server_process(task: asyncio.Task[None], port: int) -> None:
    """Wait for an in-process Uvicorn task to bind its loopback listener.

    Polls the same bounded listener seam as child-process tests while surfacing premature task
    completion as a startup failure.

    Arguments:
        task: Uvicorn serving task being observed.
        port: Loopback port the task must bind.

    Returns:
        None.

    Raises:
        AssertionError: If the task exits or never listens.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SERVER_START_TIMEOUT_SECONDS

    while loop.time() < deadline:
        if task.done():
            await task
            message = "in-process Uvicorn exited before listening"
            raise AssertionError(message)

        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
        else:
            writer.close()
            await writer.wait_closed()
            return

    message = f"in-process Uvicorn did not listen on port {port}"
    raise AssertionError(message)


@asynccontextmanager
async def raw_websocket_connection(
    uri: str,
) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    """Open one raw WebSocket connection without client protocol safeguards.

    Performs a valid HTTP upgrade, then yields the TCP streams so tests can send malformed or
    deliberately unanswered transport frames that a conforming client library prevents.

    Arguments:
        uri: Complete loopback WebSocket URI.

    Yields:
        Reader and writer positioned immediately after a successful upgrade response.

    Raises:
        AssertionError: If Uvicorn does not accept the handshake.
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
        "\r\n"
    )

    try:
        writer.write(request.encode("ascii"))
        await writer.drain()
        response = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"),
            timeout=RECEIVE_TIMEOUT_SECONDS,
        )
        assert response.startswith(b"HTTP/1.1 101 Switching Protocols\r\n")
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def receive_raw_close_code(reader: asyncio.StreamReader) -> int:
    """Read transport frames until the server supplies a close code.

    Skips keepalive frames without answering them and decodes the unmasked server close payload,
    allowing timeout and shutdown behavior to be observed without a client library intervening.

    Arguments:
        reader: Raw upgraded WebSocket stream.

    Returns:
        Standard close code carried by the server.

    Raises:
        AssertionError: If the server closes TCP without a close frame or sends a masked frame.
    """
    while True:
        header = await asyncio.wait_for(reader.readexactly(2), timeout=RECEIVE_TIMEOUT_SECONDS)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        assert not masked
        if length == SHORT_EXTENDED_FRAME_LENGTH:
            length = int.from_bytes(await reader.readexactly(2))
        elif length == LONG_EXTENDED_FRAME_LENGTH:
            length = int.from_bytes(await reader.readexactly(8))
        payload = await reader.readexactly(length)
        if opcode == Opcode.CLOSE:
            assert len(payload) >= CLOSE_CODE_PAYLOAD_BYTES
            return int.from_bytes(payload[:CLOSE_CODE_PAYLOAD_BYTES])


async def observed_close_code(
    uri: str,
    *,
    origin: str,
    frame: str | None = None,
    subprotocols: list[str] | None = None,
) -> int:
    """Return the close code a real client observes from Uvicorn.

    Connects with one origin, optionally sends a literal frame, and waits for the deployed server
    to close, converting the client library's exception into the protocol evidence under test.

    Arguments:
        uri: Complete WebSocket URI served by the child process.
        origin: Browser origin supplied during the handshake.
        frame: Optional literal text frame sent after connection.
        subprotocols: Optional protocol values offered by the client.

    Returns:
        Close code carried by the server's close frame.

    Raises:
        AssertionError: If the server does not close within the timeout.
    """
    async with connect(
        uri,
        origin=cast("Origin", origin),
        subprotocols=cast("list[Subprotocol] | None", subprotocols),
        open_timeout=RECEIVE_TIMEOUT_SECONDS,
    ) as websocket:
        if frame is not None:
            await websocket.send(frame)

        with pytest.raises(ConnectionClosedError) as captured:
            await asyncio.wait_for(websocket.recv(), timeout=RECEIVE_TIMEOUT_SECONDS)

    close_frame = captured.value.rcvd
    assert close_frame is not None

    return close_frame.code


async def issue_access_token(account: User) -> str:
    """Issue one REST-compatible access credential.

    Uses the same primary-backed adapter as the public endpoint so the child server receives the
    complete production claim set.

    Arguments:
        account: Active account receiving the credential.

    Returns:
        Encoded access token.
    """
    from accounts.jwt_authentication import PrimaryRefreshToken  # noqa: PLC0415

    refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)

    return str(refresh.access_token)


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

    assert origin_code == WebSocketOutcome.PERMISSION_DENIED.required_close_code()
    assert ambiguous_origin_status == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_uvicorn_host_rejection_precedes_authentication(worker_id: str) -> None:
    """Reject an unlisted Host through the production Uvicorn application.

    Uses an untrusted URI authority while overriding only the TCP destination, proving Host
    admission returns permission denial before missing-credential classification.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If the request reaches authentication or uses another close code.
    """
    port = worker_server_port(worker_id)
    async with running_uvicorn(worker_id, "config.asgi:application") as uri:
        untrusted_uri = uri.replace("127.0.0.1", "untrusted.invalid")
        async with connect(
            untrusted_uri,
            host="127.0.0.1",
            port=port,
            origin=cast("Origin", "http://localhost:8080"),
            open_timeout=RECEIVE_TIMEOUT_SECONDS,
        ) as websocket:
            with pytest.raises(ConnectionClosedError) as captured:
                await asyncio.wait_for(websocket.recv(), timeout=RECEIVE_TIMEOUT_SECONDS)

        close_frame = captured.value.rcvd
        assert close_frame is not None
        code = close_frame.code

    assert code == WebSocketOutcome.PERMISSION_DENIED.required_close_code()


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
async def test_uvicorn_transport_rejects_protocol_and_utf8_errors(worker_id: str) -> None:
    """Expose malformed framing and invalid text encoding as transport closes.

    Sends one unmasked client frame and one masked text frame containing invalid UTF-8 over raw
    upgraded connections, bypassing safeguards in conforming WebSocket client libraries.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If Uvicorn emits another close outcome.
    """
    invalid_utf8 = Frame(Opcode.TEXT, b"\xff").serialize(mask=True)

    async with running_uvicorn(
        worker_id,
        "tests.integration.notifications.test_uvicorn_websocket:idle_transport_application",
    ) as uri:
        async with raw_websocket_connection(uri) as (reader, writer):
            writer.write(b"\x81\x02{}")
            await writer.drain()
            protocol_code = await receive_raw_close_code(reader)

        async with raw_websocket_connection(uri) as (reader, writer):
            writer.write(invalid_utf8)
            await writer.drain()
            utf8_code = await receive_raw_close_code(reader)

    assert protocol_code == TRANSPORT_PROTOCOL_ERROR_CLOSE_CODE
    assert utf8_code == TRANSPORT_INVALID_UTF8_CLOSE_CODE


@pytest.mark.asyncio
async def test_uvicorn_transport_exposes_keepalive_timeout(worker_id: str) -> None:
    """Close an unresponsive raw client after the pinned keepalive timeout.

    Runs Uvicorn with bounded test-only ping timing and deliberately ignores the ping so the
    transport, rather than application code, emits its standard internal-error close.

    Arguments:
        worker_id: Pytest worker identifier isolating the live listener.

    Returns:
        None.

    Raises:
        AssertionError: If Uvicorn emits another close outcome or does not stop.
    """
    async with (
        running_in_process_uvicorn(
            worker_id,
            port_offset=10,
            ping_interval=0.05,
            ping_timeout=0.05,
        ) as (uri, _server, _task),
        raw_websocket_connection(uri) as (reader, _writer),
    ):
        code = await receive_raw_close_code(reader)

    assert code == TRANSPORT_INTERNAL_ERROR_CLOSE_CODE


@pytest.mark.asyncio
async def test_uvicorn_transport_exposes_controlled_shutdown(worker_id: str) -> None:
    """Close an established socket with restart code during graceful shutdown.

    Controls the pinned Uvicorn server directly, requests graceful exit with one raw connection
    open, and observes the standard service-restart close before the serving task terminates.

    Arguments:
        worker_id: Pytest worker identifier isolating the live listener.

    Returns:
        None.

    Raises:
        AssertionError: If shutdown emits another close or leaves the server task running.
    """
    async with running_in_process_uvicorn(
        worker_id,
        port_offset=11,
    ) as (uri, server, task):
        async with raw_websocket_connection(uri) as (reader, _writer):
            server.should_exit = True
            code = await receive_raw_close_code(reader)
        await asyncio.wait_for(task, timeout=SERVER_STOP_TIMEOUT_SECONDS)

    assert code == TRANSPORT_RESTART_CLOSE_CODE


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

    assert nan_code == WebSocketOutcome.MALFORMED_FRAME.required_close_code()
    assert duplicate_code == WebSocketOutcome.MALFORMED_FRAME.required_close_code()
    assert nested_duplicate_code == WebSocketOutcome.MALFORMED_FRAME.required_close_code()


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

    assert depth_code == WebSocketOutcome.MALFORMED_FRAME.required_close_code()


@pytest.mark.asyncio
async def test_uvicorn_transport_limit_is_distinct_from_application_limit(
    worker_id: str,
) -> None:
    """Separate the 64 KiB application close from Uvicorn's larger transport close.

    Sends one message at the exact transport boundary, which reaches ASGI and receives application
    close 4407, then sends the first byte above it and observes transport close 1009 before ASGI.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If either deployed limit uses the other layer's close outcome.
    """
    transport_limit = int(os.environ["UVICORN_WEBSOCKET_MAX_SIZE_BYTES"])
    exact_transport_frame = "x" * transport_limit
    oversized_transport_frame = "x" * (transport_limit + 1)

    async with running_uvicorn(worker_id) as uri:
        application_code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
            frame=exact_transport_frame,
        )
        transport_code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
            frame=oversized_transport_frame,
        )

    assert application_code == WebSocketOutcome.FRAME_TOO_LARGE.required_close_code()
    assert transport_code == TRANSPORT_MESSAGE_TOO_BIG_CLOSE_CODE


@pytest.mark.asyncio
async def test_uvicorn_contains_unhandled_consumer_failure(worker_id: str) -> None:
    """Expose close code 4500 through the pinned Uvicorn transport.

    Serves the failure-boundary application in a child process, triggers one unexpected consumer
    exception after acceptance, and verifies no abnormal transport close replaces the contract.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If Uvicorn exposes another close code.
    """
    async with running_uvicorn(
        worker_id,
        "tests.integration.notifications.test_uvicorn_websocket:live_failure_application",
    ) as uri:
        code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
            frame='{"type":"unsupported","payload":{}}',
        )

    assert code == WebSocketOutcome.SERVER_ERROR.required_close_code()


@pytest.mark.asyncio
async def test_uvicorn_keeps_recoverable_error_frames_open(worker_id: str) -> None:
    """Deliver both recoverable outcomes through the pinned transport.

    Sends prohibited membership and unknown message types on one connection, asserting their exact
    envelopes share one request identifier and neither response closes the socket.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.

    Returns:
        None.

    Raises:
        AssertionError: If either frame or connection behavior differs.
    """
    async with (
        running_uvicorn(worker_id) as uri,
        connect(
            uri,
            origin=cast("Origin", "http://localhost:8080"),
            open_timeout=RECEIVE_TIMEOUT_SECONDS,
        ) as websocket,
    ):
        await websocket.send('{"type":"subscribe","payload":{"user_id":"another"}}')
        denied = json.loads(
            await asyncio.wait_for(websocket.recv(), timeout=RECEIVE_TIMEOUT_SECONDS)
        )
        await websocket.send('{"type":"unsupported","payload":{}}')
        unknown = json.loads(
            await asyncio.wait_for(websocket.recv(), timeout=RECEIVE_TIMEOUT_SECONDS)
        )

    assert denied["payload"]["code"] == WebSocketOutcome.PERMISSION_DENIED.code
    assert unknown["payload"]["code"] == WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.code
    assert denied["payload"]["request_id"] == unknown["payload"]["request_id"]
    UUID(denied["payload"]["request_id"])


@pytest.mark.asyncio
@pytest.mark.services("postgres", "valkey-channels")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
async def test_uvicorn_exposes_every_authentication_close(
    worker_id: str,
    django_user_model: type[User],
) -> None:
    """Deliver all five credential and account close outcomes through Uvicorn.

    Builds credentials through the REST token adapter, changes only authoritative account or time
    state, and observes each central authentication close from a real client.

    Arguments:
        worker_id: Pytest worker identifier isolating the child listener.
        django_user_model: Configured account model used to create token owners.

    Returns:
        None.

    Raises:
        AssertionError: If any authentication category receives another close code.
    """
    suffix = uuid4().hex
    expired_account = await database_sync_to_async(django_user_model.objects.create_user)(
        f"uvicorn-expired-{suffix}",
        f"uvicorn-expired-{suffix}@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    issued_at = timezone.now() - timedelta(days=1)
    with freeze_time(issued_at):
        expired = await issue_access_token(expired_account)

    inactive = await database_sync_to_async(django_user_model.objects.create_user)(
        f"uvicorn-inactive-{suffix}",
        f"uvicorn-inactive-{suffix}@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    inactive_access = await issue_access_token(inactive)
    inactive.is_active = False
    await database_sync_to_async(inactive.save)(using="default", update_fields=["is_active"])

    deleted = await database_sync_to_async(django_user_model.objects.create_user)(
        f"uvicorn-deleted-{suffix}",
        f"uvicorn-deleted-{suffix}@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    deleted_access = await issue_access_token(deleted)
    await database_sync_to_async(deleted.delete)(using="default")

    cases = [
        (None, WebSocketOutcome.CREDENTIAL_ABSENT),
        (["not-a-token"], WebSocketOutcome.CREDENTIAL_MALFORMED),
        ([expired], WebSocketOutcome.CREDENTIAL_EXPIRED),
        ([inactive_access], WebSocketOutcome.ACCOUNT_INACTIVE),
        ([deleted_access], WebSocketOutcome.ACCOUNT_NOT_FOUND),
    ]
    database_name = cast("str", connections["default"].settings_dict["NAME"])
    async with running_uvicorn(
        worker_id,
        "config.asgi:application",
        {"POSTGRES_DB": database_name},
        0,
    ) as uri:
        for subprotocols, outcome in cases:
            code = await observed_close_code(
                uri,
                origin="http://localhost:8080",
                subprotocols=subprotocols,
            )

            assert code == outcome.required_close_code()


@pytest.mark.asyncio
@pytest.mark.services("valkey-channels")
async def test_uvicorn_exposes_throttle_and_store_failure_closes(
    worker_id: str,
) -> None:
    """Expose connection throttling and admission-store failure through Uvicorn.

    Uses one-account-per-server isolation so a one-per-minute child admits once then closes 4408,
    while an unreachable dedicated Valkey endpoint fails closed with 4500.

    Arguments:
        worker_id: Pytest worker identifier isolating each child listener.

    Returns:
        None.

    Raises:
        AssertionError: If limit exhaustion or store loss receives another close code.
    """
    await clear_admission_test_key()

    async with running_uvicorn(
        worker_id,
        "tests.integration.notifications.test_uvicorn_websocket:live_admission_application",
        {"DJANGO_WEBSOCKET_CONNECTION_THROTTLE_RATE": "1/minute"},
    ) as uri:
        async with connect(
            uri,
            origin=cast("Origin", "http://localhost:8080"),
            open_timeout=RECEIVE_TIMEOUT_SECONDS,
        ):
            pass
        throttled_code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
        )

    async with running_uvicorn(
        worker_id,
        "tests.integration.notifications.test_uvicorn_websocket:live_admission_application",
        {"VALKEY_CHANNELS_PORT": "1"},
        1,
    ) as uri:
        store_failure_code = await observed_close_code(
            uri,
            origin="http://localhost:8080",
        )

    assert throttled_code == WebSocketOutcome.CONNECTION_THROTTLED.required_close_code()
    assert store_failure_code == WebSocketOutcome.SERVER_ERROR.required_close_code()
