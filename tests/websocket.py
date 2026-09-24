"""Daphne-free WebSocket test communication.

Provides the narrow ASGI WebSocket test client the integration suite needs without importing the
Channels live-server package or changing the host event-loop policy.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from asgiref.typing import ASGI3Application, ASGIReceiveEvent, ASGISendEvent, Scope


class WebsocketCommunicator:
    """Drive one ASGI WebSocket application in process.

    Inherits from nothing and owns the ASGI task plus its input and output queues, with connection,
    frame, JSON, and disconnect shortcuts over them.

    Attributes:
        application: ASGI application under test.
        scope: Mutable WebSocket scope supplied to the application.
        response_headers: Headers returned by an accepted connection, when present.

    Members:
        wait: Await application shutdown.
        stop: Cancel the application task.
        send_input: Deliver one event to the application.
        receive_output: Read one event from the application.
        receive_nothing: Assert the output queue remains empty for a bounded interval.
        connect: Start the WebSocket handshake.
        send_to: Send one text or binary frame.
        send_json_to: Encode and send one JSON text frame.
        receive_from: Receive one text or binary frame.
        receive_json_from: Receive and decode one JSON text frame.
        disconnect: Send a WebSocket disconnect event and await shutdown.
    """

    def __init__(
        self,
        application: ASGI3Application,
        path: str,
        *,
        headers: list[tuple[bytes, bytes]] | None = None,
        subprotocols: list[str] | None = None,
    ) -> None:
        """Create a communicator for one WebSocket route.

        Parses the route into an ASGI scope while preserving headers and requested subprotocols for
        authentication and admission tests.

        Arguments:
            application: ASGI application to execute.
            path: WebSocket path with an optional query string.
            headers: Request headers supplied to the handshake.
            subprotocols: Requested WebSocket subprotocols.

        Returns:
            None.

        Raises:
            TypeError: If the path is not a string.
        """
        parsed = urlparse(path)
        self.application = application
        self.scope: dict[str, object] = {
            "type": "websocket",
            "path": unquote(parsed.path),
            "query_string": parsed.query.encode(),
            "headers": headers or [],
            "subprotocols": subprotocols or [],
        }
        self.response_headers: list[tuple[bytes, bytes]] | None = None
        self._future: asyncio.Task[None] | None = None
        self._input_queue: asyncio.Queue[ASGIReceiveEvent] | None = None
        self._output_queue: asyncio.Queue[ASGISendEvent] | None = None

    @property
    def input_queue(self) -> asyncio.Queue[ASGIReceiveEvent]:
        """Return the lazily bound application input queue.

        Creates the queue in the active event loop on first access and reuses it for every later
        event.

        Arguments:
            None.

        Returns:
            Queue supplying receive events to the application.

        Raises:
            None.
        """
        if self._input_queue is None:
            self._input_queue = asyncio.Queue()
        return self._input_queue

    @property
    def output_queue(self) -> asyncio.Queue[ASGISendEvent]:
        """Return the lazily bound application output queue.

        Creates the queue in the active event loop on first access and reuses it for every later
        event.

        Arguments:
            None.

        Returns:
            Queue receiving send events from the application.

        Raises:
            None.
        """
        if self._output_queue is None:
            self._output_queue = asyncio.Queue()
        return self._output_queue

    async def _receive(self) -> ASGIReceiveEvent:
        """Read one application input event.

        Bridges the ASGI receive callable to the communicator's typed input queue.
        The application awaits this boundary exactly as it would await a server.

        Arguments:
            None.

        Returns:
            Next ASGI receive event.

        Raises:
            None.
        """
        return await self.input_queue.get()

    async def _send(self, message: ASGISendEvent) -> None:
        """Store one application output event.

        Bridges the ASGI send callable to the communicator's typed output queue.
        The test client consumes this boundary in application emission order.

        Arguments:
            message: ASGI event emitted by the application.

        Returns:
            None.

        Raises:
            None.
        """
        await self.output_queue.put(message)

    @property
    def future(self) -> asyncio.Task[None]:
        """Return the application task, creating it without inherited context.

        Starts the ASGI callable on first access with the mutable scope and queue-backed receive and
        send callables.

        Arguments:
            None.

        Returns:
            Running or completed application task.

        Raises:
            None.
        """
        if self._future is None:
            coroutine = self.application(
                cast("Scope", self.scope),
                self._receive,
                self._send,
            )
            self._future = asyncio.create_task(
                cast("Coroutine[Any, Any, None]", coroutine),
                context=contextvars.Context(),
            )
        return self._future

    async def wait(self, timeout: float = 1) -> None:
        """Wait for the application task to finish.

        Cancels a task that remains active after the deadline while preserving any application
        exception from a task that completed.

        Arguments:
            timeout: Seconds to wait for application shutdown.

        Returns:
            None.

        Raises:
            TimeoutError: If the task does not stop before the deadline.
            Exception: Any exception raised by the application.
        """
        try:
            async with asyncio.timeout(timeout):
                try:
                    await asyncio.shield(self.future)
                except asyncio.CancelledError:
                    if self.future.cancelled():
                        return
                    raise
                self.future.result()
        except TimeoutError:
            if not self.future.done():
                self.future.cancel()
                with suppress(asyncio.CancelledError):
                    await self.future
            raise

    def stop(self, *, exceptions: bool = True) -> None:
        """Stop the application task.

        Cancels an active task or re-raises the completed task's exception when requested.
        An application that was never started requires no cleanup.

        Arguments:
            exceptions: Whether a completed task should expose its exception.

        Returns:
            None.

        Raises:
            Exception: Any completed application exception when requested.
        """
        future = self._future
        if future is None:
            return
        if not future.done():
            future.cancel()
        elif exceptions:
            future.result()

    def __del__(self) -> None:
        """Cancel an abandoned application task.

        Performs best-effort cleanup without surfacing an exception after the event loop has
        already closed.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        with suppress(RuntimeError):
            self.stop(exceptions=False)

    async def send_input(self, message: dict[str, Any]) -> None:
        """Deliver one ASGI receive event to the application.

        Exposes any exception from an already-completed application before queueing the event.
        Successful applications receive events in the exact order supplied.

        Arguments:
            message: ASGI receive event.

        Returns:
            None.

        Raises:
            Exception: Any exception already raised by the application.
        """
        if self.future.done():
            self.future.result()
        await self.input_queue.put(cast("ASGIReceiveEvent", message))

    async def receive_output(self, timeout: float = 1) -> dict[str, Any]:
        """Read one ASGI send event from the application.

        Waits for one output event and exposes an application exception when the task fails before
        producing output.

        Arguments:
            timeout: Seconds to wait for an output event.

        Returns:
            ASGI send event.

        Raises:
            TimeoutError: If no event arrives before the deadline.
            Exception: Any exception raised by the application.
        """
        if self.future.done():
            self.future.result()
        try:
            async with asyncio.timeout(timeout):
                return cast("dict[str, Any]", await self.output_queue.get())
        except TimeoutError:
            if self.future.done():
                self.future.result()
            else:
                self.future.cancel()
                with suppress(asyncio.CancelledError):
                    await self.future
            raise

    async def receive_nothing(
        self,
        timeout: float = 0.1,
        interval: float = 0.01,
    ) -> bool:
        """Report whether the application emits nothing for a bounded interval.

        Polls the output queue while preserving any application exception that occurs during the
        observation window.

        Arguments:
            timeout: Total seconds to observe.
            interval: Seconds between queue checks.

        Returns:
            Whether no output event was available throughout the interval.

        Raises:
            Exception: Any exception raised by the application.
        """
        if self.future.done():
            self.future.result()
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if not self.output_queue.empty():
                return False
            await asyncio.sleep(interval)
        return self.output_queue.empty()

    async def connect(self, timeout: float = 1) -> tuple[bool, str | int | None]:
        """Start the WebSocket handshake.

        Sends the ASGI connect event and distinguishes an accepted subprotocol from a rejected close
        code.

        Arguments:
            timeout: Seconds to wait for the handshake response.

        Returns:
            Acceptance plus the selected subprotocol or rejection close code.

        Raises:
            AssertionError: If the application emits another event type.
            TimeoutError: If the application does not answer before the deadline.
        """
        await self.send_input({"type": "websocket.connect"})
        response = await self.receive_output(timeout)
        if response["type"] == "websocket.close":
            code = response.get("code", 1000)
            assert isinstance(code, int)
            return False, code

        assert response["type"] == "websocket.accept"
        headers = response.get("headers", [])
        assert isinstance(headers, list)
        self.response_headers = cast("list[tuple[bytes, bytes]]", headers)
        subprotocol = response.get("subprotocol")
        assert subprotocol is None or isinstance(subprotocol, str)
        return True, subprotocol

    async def send_to(
        self,
        *,
        text_data: str | None = None,
        bytes_data: bytes | None = None,
    ) -> None:
        """Send one WebSocket data frame.

        Requires exactly one payload type and translates it into the matching ASGI receive event.
        Supplying no payload or both payload forms is rejected before application delivery.

        Arguments:
            text_data: Text frame payload.
            bytes_data: Binary frame payload.

        Returns:
            None.

        Raises:
            AssertionError: If zero or two payloads are supplied.
        """
        assert (text_data is None) != (bytes_data is None)
        if text_data is not None:
            await self.send_input({"type": "websocket.receive", "text": text_data})
            return

        await self.send_input({"type": "websocket.receive", "bytes": bytes_data})

    async def send_json_to(self, data: object) -> None:
        """Send one JSON value as a text frame.

        Serializes the value with the standard JSON encoder before using the text-frame path.
        The application therefore observes the same frame shape as a network client.

        Arguments:
            data: JSON-serializable value.

        Returns:
            None.

        Raises:
            TypeError: If the value is not JSON serializable.
        """
        await self.send_to(text_data=json.dumps(data))

    async def receive_from(self, timeout: float = 1) -> str | bytes:
        """Receive one WebSocket data frame.

        Requires exactly one text or binary payload and returns it in its native Python type.
        Close and malformed send events fail at this protocol boundary.

        Arguments:
            timeout: Seconds to wait for the frame.

        Returns:
            Text or binary payload sent by the application.

        Raises:
            AssertionError: If the application sends a non-data or malformed event.
            TimeoutError: If no frame arrives before the deadline.
        """
        response = cast("dict[str, object]", await self.receive_output(timeout))
        assert response["type"] == "websocket.send"
        text = response.get("text")
        binary = response.get("bytes")
        assert (text is None) != (binary is None)
        if text is not None:
            assert isinstance(text, str)
            return text

        assert isinstance(binary, bytes)
        return binary

    async def receive_json_from(self, timeout: float = 1) -> Any:
        """Receive and decode one JSON text frame.

        Reads one data frame, requires text, and parses the contained JSON value.
        Binary frames and malformed JSON remain explicit test failures.

        Arguments:
            timeout: Seconds to wait for the frame.

        Returns:
            Decoded JSON value.

        Raises:
            AssertionError: If the application sends a binary frame.
            json.JSONDecodeError: If the text is not valid JSON.
            TimeoutError: If no frame arrives before the deadline.
        """
        payload = await self.receive_from(timeout)
        assert isinstance(payload, str)
        return json.loads(payload)

    async def disconnect(self, code: int = 1000, timeout: float = 1) -> None:
        """Disconnect the WebSocket and await application shutdown.

        Sends the requested close code through the ASGI receive channel, then waits for the
        application task to finish or be cancelled.
        The shutdown path matches a client-initiated WebSocket disconnect.

        Arguments:
            code: WebSocket close code presented to the application.
            timeout: Seconds to wait for application shutdown.

        Returns:
            None.

        Raises:
            TimeoutError: If the application does not stop before the deadline.
        """
        await self.send_input({"type": "websocket.disconnect", "code": code})
        await self.wait(timeout)
