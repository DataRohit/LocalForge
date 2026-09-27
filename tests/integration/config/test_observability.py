"""Integration tests for application observability.

Exercises request and database metrics through Django, verifies correlation across response and log
records, and proves submitted credentials do not enter the structured application stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
import struct
import threading
import time
from contextvars import Context
from http import HTTPStatus
from importlib import import_module
from io import StringIO
from types import AsyncGeneratorType, GeneratorType, SimpleNamespace
from typing import TYPE_CHECKING, NoReturn, cast

import pytest
from asgiref.sync import sync_to_async
from celery.app.task import Context as CeleryContext
from celery.app.trace import FAILURE, TraceInfo
from celery.app.trace import logger as celery_trace_logger
from django.core.handlers.asgi import ASGIHandler
from django.db import DatabaseError, connection
from django.http import HttpResponse, StreamingHttpResponse
from django.test import AsyncClient, override_settings
from django.urls import path
from health_check.exceptions import ServiceUnavailable

from config.health import (
    ContextPreservingThreadPoolExecutor,
    run_timed_database_check,
)
from config.logs import (
    INVALID_HTTP_METHOD,
    NO_REQUEST_ID,
    REQUEST_ID_EXCEPTION_ATTRIBUTE,
    REQUEST_ID_HEADER,
    QueryRedactionFilter,
    RequestContextFilter,
    StructuredFormatter,
    finalize_streaming_asgi,
    request_identifier,
)

STREAM_CLOSE_SETTLE_SECONDS = 0.1
STREAM_CLEANUP_TEST_TIMEOUT_SECONDS = 5
TRANSPORT_DELAY_SECONDS = 0.05
DATABASE_STALL_TIMEOUT_SECONDS = 0.1
DATABASE_STALL_TEST_TIMEOUT_SECONDS = 5
PrometheusBeforeMiddleware = import_module(
    "django_prometheus.middleware"
).PrometheusBeforeMiddleware

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator

    from asgiref.typing import (
        ASGI3Application,
        ASGIReceiveEvent,
        ASGISendEvent,
        HTTPScope,
    )
    from django.db.backends.postgresql.base import DatabaseWrapper
    from django.http import HttpRequest
    from django.test import Client
    from pytest_mock import MockerFixture


class AsynchronousIteratorWithoutClose:
    """Yield one chunk and fail without optional asynchronous cleanup.

    Implements only the asynchronous-iterator protocol Django requires, deliberately omitting
    ``aclose`` so stream failure handling cannot assume an async-generator extension.

    Attributes:
        yielded: Whether the successful first chunk has already been returned.

    Members:
        __aiter__: Return this iterator.
        __anext__: Yield once before raising the controlled source failure.
    """

    def __init__(self) -> None:
        """Create an iterator positioned before its first chunk.

        Records only whether the first body value has been emitted.
        Starts in the not-yet-yielded state.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.yielded = False

    def __aiter__(self) -> AsynchronousIteratorWithoutClose:
        """Return this asynchronous iterator.

        Supports Django's required iterator protocol without adding cleanup extensions.
        Preserves the current yield state.

        Arguments:
            None.

        Returns:
            This iterator.

        Raises:
            None.
        """
        return self

    async def __anext__(self) -> bytes:
        """Yield one body chunk before raising the source failure.

        Exercises failure propagation after transport has started while no ``aclose`` member is
        available.

        Arguments:
            None.

        Returns:
            First response body chunk.

        Raises:
            ValueError: After the first chunk has been yielded.
        """
        if not self.yielded:
            self.yielded = True
            return b"without-close"

        message = "source failure without close"
        raise ValueError(message)


class OneShotAsynchronousCloser:
    """Yield one chunk and reject duplicate asynchronous cleanup.

    Implements Django's asynchronous iterator contract with a non-idempotent optional close
    extension, exposing duplicate resource release attempts at the real ASGI lifecycle boundary.

    Attributes:
        yielded: Whether the response chunk has been returned.
        close_count: Number of asynchronous close invocations.

    Members:
        __aiter__: Return this iterator.
        __anext__: Yield one response body chunk.
        aclose: Close once and reject duplicate cleanup.
    """

    def __init__(self) -> None:
        """Create an open iterator before its only response chunk.

        Initializes iteration and cleanup counters independently so assertions can distinguish body
        progress from duplicate resource release.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.yielded = False
        self.close_count = 0

    def __aiter__(self) -> OneShotAsynchronousCloser:
        """Return this asynchronous iterator.

        Keeps Django and the observability wrapper bound to the same non-idempotent cleanup object.
        Introduces no additional generator boundary or cleanup owner.

        Arguments:
            None.

        Returns:
            This asynchronous iterator.

        Raises:
            None.
        """
        return self

    async def __anext__(self) -> bytes:
        """Yield one body chunk and then finish.

        Provides enough progress for first-body transport cancellation to close the suspended
        iterator through the production response lifecycle.

        Arguments:
            None.

        Returns:
            The only response body chunk.

        Raises:
            StopAsyncIteration: After the first body chunk.
        """
        if self.yielded:
            raise StopAsyncIteration

        self.yielded = True
        return b"one-shot"

    async def aclose(self) -> None:
        """Close the iterator exactly once.

        Raises on a second invocation so duplicate cleanup cannot be hidden by an idempotent async
        generator implementation.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            RuntimeError: If cleanup is invoked more than once.
        """
        self.close_count += 1
        if self.close_count > 1:
            message = "asynchronous source closed twice"
            raise RuntimeError(message)


class StalledPostgresPeer:
    """Emulate a PostgreSQL peer that stalls after connection setup.

    Completes the minimum startup protocol psycopg requires, then withholds every query response
    until the client forcibly closes its transport.

    Attributes:
        listener: Loopback TCP listener.
        port: Ephemeral published port.
        query_received: Signal that the client began its readiness query.
        client_closed: Signal that forced closure reached the peer.
        errors: Unexpected server-thread failures.
        thread: Background protocol-serving thread.

    Members:
        close: Release the listener and join the server thread.
    """

    def __init__(self) -> None:
        """Create a loopback PostgreSQL protocol listener.

        Binds an ephemeral port and starts a daemon thread that performs the controlled handshake
        and stalled-query behavior.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            OSError: If the loopback listener cannot be created.
        """
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = cast("tuple[str, int]", self.listener.getsockname())[1]
        self.query_received = threading.Event()
        self.client_closed = threading.Event()
        self.errors: list[BaseException] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @staticmethod
    def _receive_exact(connection: socket.socket, length: int) -> bytes:
        """Receive exactly one protocol segment.

        Repeats bounded socket reads until the requested startup message length is available or the
        client disconnects.

        Arguments:
            connection: Accepted client socket.
            length: Number of bytes required.

        Returns:
            Complete protocol segment.

        Raises:
            ConnectionError: If the client closes before the segment completes.
            OSError: If socket receive fails.
        """
        received = bytearray()

        while len(received) < length:
            part = connection.recv(length - len(received))
            if not part:
                message = "PostgreSQL peer closed during startup"
                raise ConnectionError(message)
            received.extend(part)

        return bytes(received)

    @staticmethod
    def _send_message(connection: socket.socket, code: bytes, payload: bytes) -> None:
        """Send one PostgreSQL backend protocol message.

        Prefixes the payload with its message code and network-order length.
        Writes the complete frame in one socket operation.

        Arguments:
            connection: Accepted client socket.
            code: One-byte PostgreSQL message type.
            payload: Message body.

        Returns:
            None.

        Raises:
            OSError: If socket send fails.
        """
        connection.sendall(code + struct.pack("!I", len(payload) + 4) + payload)

    def _serve(self) -> None:
        """Complete startup and stall the first query response.

        Accepts optional SSL negotiation, sends authentication and ready messages, then records the
        first frontend query bytes and waits for forced client closure.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None. Unexpected failures are retained in ``errors`` for the test.
        """
        try:
            connection, _address = self.listener.accept()
            with connection:
                connection.settimeout(DATABASE_STALL_TEST_TIMEOUT_SECONDS)
                length = struct.unpack("!I", self._receive_exact(connection, 4))[0]
                startup = self._receive_exact(connection, length - 4)

                if startup == struct.pack("!I", 80877103):
                    connection.sendall(b"N")
                    length = struct.unpack("!I", self._receive_exact(connection, 4))[0]
                    self._receive_exact(connection, length - 4)

                self._send_message(connection, b"R", struct.pack("!I", 0))
                self._send_message(connection, b"S", b"server_version\x0018.6\x00")
                self._send_message(connection, b"S", b"client_encoding\x00UTF8\x00")
                self._send_message(connection, b"S", b"standard_conforming_strings\x00on\x00")
                self._send_message(connection, b"K", struct.pack("!II", 1, 1))
                self._send_message(connection, b"Z", b"I")

                if connection.recv(4096):
                    self.query_received.set()

                while connection.recv(4096):
                    pass

                self.client_closed.set()
        except (ConnectionError, OSError, TimeoutError, struct.error) as error:
            self.errors.append(error)

    def close(self) -> None:
        """Release the listener and finish the protocol thread.

        Closes the accepting socket and joins briefly so test teardown cannot leave a background
        peer running.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.listener.close()
        self.thread.join(timeout=1)


def _raise_metric_probe(_request: HttpRequest) -> NoReturn:
    """Raise the exception-path metric probe.

    Provides a test-only view that exercises middleware exception hooks without adding an
    application route to the fixed public surface.

    Arguments:
        _request: Request dispatched to the probe.

    Returns:
        Never returns.

    Raises:
        ValueError: Always, to invoke middleware exception processing.
    """
    message = "metric exception probe"
    raise ValueError(message)


def _synchronous_stream_body(*, fail: bool) -> Iterator[bytes]:
    """Yield a synchronous body and optionally fail.

    Emits a log from inside body iteration before returning one chunk, then raises when the
    requested mode exercises streaming failure finalization.

    Arguments:
        fail: Whether to raise after the first chunk.

    Returns:
        Iterator yielding one response body chunk.

    Yields:
        One synchronous response body chunk.

    Raises:
        ValueError: If failure mode is selected.
    """
    try:
        logging.getLogger("localforge.stream").info(
            "synchronous stream body",
            extra={"body_request_id": request_identifier.get()},
        )
        yield b"synchronous"
        if fail:
            message = "synchronous stream failure"
            raise ValueError(message)
    finally:
        logging.getLogger("localforge.stream").info(
            "synchronous stream cleanup",
            extra={"body_request_id": request_identifier.get()},
        )


async def _asynchronous_stream_body(*, fail: bool) -> AsyncIterator[bytes]:
    """Yield an asynchronous body and optionally fail.

    Emits a log from inside body iteration before returning one chunk, then raises when the
    requested mode exercises streaming failure finalization.

    Arguments:
        fail: Whether to raise after the first chunk.

    Returns:
        Asynchronous iterator yielding one response body chunk.

    Yields:
        One asynchronous response body chunk.

    Raises:
        ValueError: If failure mode is selected.
    """
    try:
        logging.getLogger("localforge.stream").info(
            "asynchronous stream body",
            extra={"body_request_id": request_identifier.get()},
        )
        yield b"asynchronous"
        if fail:
            message = "asynchronous stream failure"
            raise ValueError(message)
    finally:
        logging.getLogger("localforge.stream").info(
            "asynchronous stream cleanup",
            extra={"body_request_id": request_identifier.get()},
        )


def _stream_metric_probe(request: HttpRequest) -> StreamingHttpResponse:
    """Return the selected test-only streaming response.

    Chooses synchronous or asynchronous body iteration and optional failure from the requested
    mode, exercising all streaming correlation finalization paths without adding a public route.

    Arguments:
        request: Request selecting the stream mode.

    Returns:
        Streaming response wrapping the selected body iterator.

    Raises:
        None.
    """
    mode = request.GET["mode"]
    fail = mode.endswith("failure")
    content = (
        _asynchronous_stream_body(fail=fail)
        if mode.startswith("asynchronous")
        else _synchronous_stream_body(fail=fail)
    )

    return StreamingHttpResponse(content, content_type="text/plain")


async def _preadvanced_stream_metric_probe(_request: HttpRequest) -> StreamingHttpResponse:
    """Return an asynchronous stream already advanced by its view.

    Initializes one source chunk before response construction, reproducing views that validate an
    upstream stream before sending headers.

    Arguments:
        _request: Request dispatched to the probe.

    Returns:
        Streaming response wrapping the suspended original source.

    Raises:
        StopAsyncIteration: If the source produces no initialization chunk.
    """
    source = _asynchronous_stream_body(fail=False)
    await anext(source)

    return StreamingHttpResponse(source, content_type="text/plain")


def _database_metric_probe(_request: HttpRequest) -> HttpResponse:
    """Execute one instrumented Django database query.

    Supplies test-only request-path coverage for database metrics and correlation without coupling
    those assertions to the health check's disposable psycopg connection.

    Arguments:
        _request: Request dispatched to the probe.

    Returns:
        Successful empty response after the database round trip.

    Raises:
        DatabaseError: If the readiness query fails.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()

    return HttpResponse(status=HTTPStatus.NO_CONTENT)


def _ordinary_metric_probe(_request: HttpRequest) -> HttpResponse:
    """Return an ordinary response for transport lifecycle tests.

    Avoids dependency work so receive-side disconnect timing can be exercised independently of
    database scheduling.

    Arguments:
        _request: Request dispatched to the probe.

    Returns:
        Successful ordinary response.

    Raises:
        None.
    """
    return HttpResponse(b"ordinary", content_type="text/plain")


urlpatterns = [
    path("database-probe/", _database_metric_probe, name="database-probe"),
    path("metric-failure/", _raise_metric_probe, name="metric-failure"),
    path("ordinary/", _ordinary_metric_probe, name="ordinary"),
    path("preadvanced-stream/", _preadvanced_stream_metric_probe, name="preadvanced-stream"),
    path("stream/", _stream_metric_probe, name="stream"),
]


def _asgi_http_scope(path_value: str, query_string: bytes = b"") -> HTTPScope:
    """Build an HTTP scope for real-handler transport tests.

    Supplies the complete fixed ASGI metadata shared by correlation, disconnect, and early
    rejection regressions.

    Arguments:
        path_value: Request path exposed to Django.
        query_string: Raw query bytes supplied before request construction.

    Returns:
        HTTP scope suitable for Django's ASGI handler.

    Raises:
        None.
    """
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.5"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path_value,
        "raw_path": path_value.encode(),
        "query_string": query_string,
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
        "extensions": {},
    }


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_metrics_expose_request_status_latency_and_database_instrumentation(
    client: Client,
) -> None:
    """Expose the application metrics Prometheus and Grafana consume.

    Executes a database-backed health request before scraping the endpoint, then confirms request,
    status, latency, and database metric families are all present in the exported document.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If instrumentation is missing or the endpoint fails.
    """
    assert client.get("/health/").status_code == HTTPStatus.OK
    with override_settings(ROOT_URLCONF=__name__):
        assert client.get("/database-probe/").status_code == HTTPStatus.NO_CONTENT

    with override_settings(ROOT_URLCONF="config.metrics_urls"):
        response = client.get("/metrics")
    metrics = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert "django_http_requests_total_by_method_total" in metrics
    assert "django_http_responses_total_by_status_total" in metrics
    assert "django_http_requests_latency_seconds_by_view_method_bucket" in metrics
    assert "django_db_execute_total" in metrics
    assert "localforge_http_responses_total" in metrics
    assert "localforge_http_request_duration_seconds_bucket" in metrics


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(transaction=True)
def test_database_exception_diagnostics_exclude_bound_values() -> None:
    """Remove database-provided values from exception diagnostics.

    Produces a real PostgreSQL cast failure containing a bound marker, then proves structured
    exception rendering keeps safe failure structure without the message or statement excerpt.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the bound marker survives exception formatting.
    """
    bound_marker = "database-diagnostic-marker"

    with pytest.raises(DatabaseError) as caught, connection.cursor() as cursor:
        cursor.execute("SELECT %s::integer", [bound_marker])

    error = caught.value
    record = logging.LogRecord(
        "database.exception",
        logging.ERROR,
        __file__,
        1,
        "database operation failed",
        (),
        (type(error), error, error.__traceback__),
    )
    payload = json.loads(StructuredFormatter().format(record))

    assert bound_marker not in json.dumps(payload)
    assert "database operation failed" in payload["exception"]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(transaction=True)
def test_celery_database_failure_diagnostics_exclude_bound_values() -> None:
    """Remove database values from Celery failure records.

    Produces a real PostgreSQL diagnostic and sends it through Celery's task failure logger,
    proving the interpolated message and copied context cannot bypass structured redaction.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any Celery diagnostic copy retains the bound marker.
    """
    bound_marker = "celery-database-diagnostic-marker"

    with pytest.raises(DatabaseError) as caught, connection.cursor() as cursor:
        cursor.execute("SELECT %s::integer", [bound_marker])

    error = caught.value
    exception_info_factory = cast(
        "Callable[..., object]",
        import_module("billiard.einfo").ExceptionInfo,
    )
    exception_info = exception_info_factory(
        exc_info=(type(error), error, error.__traceback__),
    )
    request = CeleryContext(
        id="observability-probe",
        hostname="localforge-test",
        args=(),
        kwargs={},
    )
    task = SimpleNamespace(name="observability.probe", throws=())
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    celery_trace_logger.addHandler(handler)

    try:
        trace_info = TraceInfo(FAILURE, error)
        log_error = cast(
            "Callable[[TraceInfo, object, object, object], None]",
            vars(TraceInfo)["_log_error"],
        )
        log_error(trace_info, task, request, exception_info)
    finally:
        celery_trace_logger.removeHandler(handler)

    payload = json.loads(stream.getvalue())

    assert bound_marker not in json.dumps(payload)
    assert "database operation failed" in payload["exception"]
    assert "SQLSTATE 22P02" in payload["exception"]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.timeout(DATABASE_STALL_TEST_TIMEOUT_SECONDS)
def test_database_deadline_aborts_stalled_real_driver_transport(
    mocker: MockerFixture,
) -> None:
    """Abort a real psycopg transport that withholds query responses.

    Runs the production health check against a protocol peer that completes connection setup but
    never answers the query, proving forced closure returns bounded executor capacity.

    Arguments:
        mocker: Fixture replacing Django's connection parameters and client deadline.

    Returns:
        None.

    Raises:
        AssertionError: If the transport remains open or executor capacity is not recovered.
    """
    peer = StalledPostgresPeer()
    connections = mocker.patch("config.health.connections")
    health_connection = connections.__getitem__.return_value
    health_connection.pool.get_stats.return_value = {
        "pool_available": 1,
        "pool_size": 2,
        "pool_max": 8,
    }
    health_connection.get_connection_params.return_value = {
        "host": "127.0.0.1",
        "port": peer.port,
        "dbname": "localforge",
        "user": "localforge",
        "connect_timeout": 1,
        "sslmode": "prefer",
    }
    mocker.patch(
        "config.health.HEALTH_CHECK_CLIENT_TIMEOUT_SECONDS",
        DATABASE_STALL_TIMEOUT_SECONDS,
    )

    try:
        with ContextPreservingThreadPoolExecutor(max_workers=1) as executor:
            started = time.perf_counter()
            stalled = executor.submit(
                run_timed_database_check,
                SimpleNamespace(alias="default"),
            )

            with pytest.raises(ServiceUnavailable, match="Database health check"):
                stalled.result(timeout=1)

            assert time.perf_counter() - started < 1
            assert peer.query_received.wait(1)
            assert peer.client_closed.wait(1)
            assert executor.submit(lambda: "accepted").result(timeout=1) == "accepted"
    finally:
        peer.close()

    assert not peer.errors


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(transaction=True)
def test_database_health_check_fails_when_the_django_pool_is_exhausted() -> None:
    """Reject readiness while the configured Django connection pool is exhausted.

    Retains every pooled connection while PostgreSQL remains independently reachable, proving the
    health check validates the application's actual ORM checkout path.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an exhausted application pool is reported as healthy.
    """
    connection.ensure_connection()
    connection.close()
    pool = cast("DatabaseWrapper", connection).pool
    assert pool is not None
    retained = [pool.getconn(timeout=1) for _ in range(pool.max_size)]

    try:
        with pytest.raises(ServiceUnavailable, match="Database health check"):
            run_timed_database_check(SimpleNamespace(alias="default"))
    finally:
        for retained_connection in retained:
            pool.putconn(retained_connection)


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_outer_metrics_record_final_redirect_and_rejection_statuses(client: Client) -> None:
    """Measure final responses after URL and middleware processing.

    Exercises a slash redirect and a disallowed host before scraping the custom outer-boundary
    metrics, proving dashboard counters represent the statuses returned to callers.

    Arguments:
        client: Django test client supplied by the framework.

    Returns:
        None.

    Raises:
        AssertionError: If either final status is absent from the exported metrics.
    """
    redirect = client.get("/admin")

    with override_settings(ALLOWED_HOSTS=["allowed.test"]):
        rejected = client.get("/health/", headers={"host": "blocked.test"})

    invalid_method = client.generic("CUSTOM-PROBE", "/missing/")
    client.raise_request_exception = False

    with override_settings(ROOT_URLCONF=__name__):
        failed_method = client.generic("CUSTOM-FAILURE", "/metric-failure/")

    with override_settings(ROOT_URLCONF="config.metrics_urls"):
        response = client.get("/metrics")
    metrics = response.content.decode()

    assert redirect.status_code == HTTPStatus.MOVED_PERMANENTLY
    assert rejected.status_code == HTTPStatus.BAD_REQUEST
    assert invalid_method.status_code == HTTPStatus.NOT_FOUND
    assert failed_method.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert (
        'localforge_http_responses_total{method="GET",status="301",view="<unresolved>"}' in metrics
    )
    assert (
        'localforge_http_responses_total{method="GET",status="400",view="<unresolved>"}' in metrics
    )
    assert (
        f'localforge_http_responses_total{{method="{INVALID_HTTP_METHOD}",'
        'status="404",view="<unresolved>"}' in metrics
    )
    assert "CUSTOM-PROBE" not in metrics
    assert "CUSTOM-FAILURE" not in metrics
    assert f'method="{INVALID_HTTP_METHOD}",view="metric-failure"' in metrics


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.parametrize("mode", ["synchronous-success", "synchronous-failure"])
def test_synchronous_stream_logs_remain_correlated_until_body_completion(
    client: Client,
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    """Correlate synchronous streaming body logs and finalization.

    Consumes successful and failing synchronous iterators, proving completion waits for the body
    and failure records retain the response identifier.

    Arguments:
        client: Django test client supplied by the framework.
        caplog: Fixture collecting records emitted during the request.
        mode: Stream completion behavior to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If body or finalization records lose correlation.
        ValueError: Contained when the selected iterator fails.
    """
    with (
        override_settings(ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO),
    ):
        response = client.get("/stream/", {"mode": mode})
        streaming_response = cast("StreamingHttpResponse", cast("object", response))
        content = cast("Iterator[bytes]", streaming_response.streaming_content)
        caught_error: ValueError | None = None

        if mode.endswith("failure"):
            with pytest.raises(ValueError, match="synchronous stream failure") as caught:
                b"".join(content)
            caught_error = caught.value
        else:
            assert b"".join(content) == b"synchronous"

    response_identifier = response.headers[REQUEST_ID_HEADER]
    body_record = next(record for record in caplog.records if record.name == "localforge.stream")
    final_record = next(
        record
        for record in caplog.records
        if record.name == "localforge.request"
        and record.getMessage() in {"request completed", "request stream failed"}
    )

    assert body_record.__dict__["request_id"] == response_identifier
    assert body_record.__dict__["body_request_id"] == response_identifier
    assert final_record.__dict__["request_id"] == response_identifier
    assert final_record.getMessage() == (
        "request stream failed" if mode.endswith("failure") else "request completed"
    )
    assert (
        sum(
            record.name == "localforge.request"
            and record.getMessage() in {"request completed", "request stream failed"}
            for record in caplog.records
        )
        == 1
    )
    assert request_identifier.get() == NO_REQUEST_ID
    if caught_error is not None:
        assert getattr(caught_error, REQUEST_ID_EXCEPTION_ATTRIBUTE, None) == response_identifier


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_asynchronous_iterator_without_close_preserves_source_failure(
    mocker: MockerFixture,
) -> None:
    """Preserve failure from an asynchronous iterator without ``aclose``.

    Replaces the async-generator body with Django's minimum accepted iterator contract, proving
    cleanup capability detection never masks its original exception.

    Arguments:
        mocker: Fixture replacing the asynchronous stream factory.

    Returns:
        None.

    Raises:
        AssertionError: If the original source failure is replaced.
    """
    source = AsynchronousIteratorWithoutClose()
    mocker.patch(
        f"{__name__}._asynchronous_stream_body",
        return_value=source,
    )
    client = AsyncClient()

    with override_settings(ROOT_URLCONF=__name__):
        response = await client.get("/stream/", {"mode": "asynchronous-failure"})
        streaming_response = cast("StreamingHttpResponse", cast("object", response))
        content = cast("AsyncIterator[bytes]", streaming_response.streaming_content)

        with pytest.raises(ValueError, match="source failure without close"):
            async for _chunk in content:
                pass


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_interrupted_custom_asynchronous_source_closes_once(
    mocker: MockerFixture,
) -> None:
    """Close a non-idempotent asynchronous source exactly once.

    Cancels first-body delivery through Django's real ASGI handler, proving the correlation wrapper
    and outer resource lifecycle share one source-closure operation.

    Arguments:
        mocker: Fixture replacing the asynchronous stream factory.

    Returns:
        None.

    Raises:
        AssertionError: If the optional close extension is invoked more than once.
    """
    request_received = False
    source = OneShotAsynchronousCloser()
    mocker.patch(
        f"{__name__}._asynchronous_stream_body",
        return_value=source,
    )

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and block the disconnect listener.

        Leaves first-body cancellation as the only interruption path so cleanup ownership remains
        deterministic.

        Arguments:
            None.

        Returns:
            Empty request body before blocking.

        Raises:
            asyncio.CancelledError: When response completion cancels the listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Cancel first-body delivery after accepting response headers.

        Starts source iteration before interruption, exercising both correlation-generator and
        outer-lifecycle cleanup paths.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: On the first response body event.
        """
        if message["type"] == "http.response.body":
            raise asyncio.CancelledError

    with override_settings(ROOT_URLCONF=__name__):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        await handler(
            _asgi_http_scope("/stream/", b"mode=asynchronous-success"),
            receive,
            send,
        )

    assert source.close_count == 1


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "synchronous-success",
        "synchronous-failure",
        "asynchronous-success",
        "asynchronous-failure",
    ],
)
async def test_asgi_stream_logs_remain_correlated_until_body_completion(
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    """Correlate ASGI streaming body logs and finalization.

    Consumes successful and failing synchronous and asynchronous iterators through ``AsyncClient``,
    proving completion waits for the body and failure records retain the response identifier.

    Arguments:
        caplog: Fixture collecting records emitted during the request.
        mode: Stream completion behavior to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If body or finalization records lose correlation.
        ValueError: Contained when the selected iterator fails.
    """
    client = AsyncClient()

    with (
        override_settings(ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO),
    ):
        response = await client.get("/stream/", {"mode": mode})
        streaming_response = cast("StreamingHttpResponse", cast("object", response))
        content = cast("AsyncIterator[bytes]", streaming_response.streaming_content)
        caught_error: ValueError | None = None
        protocol = "asynchronous" if mode.startswith("asynchronous") else "synchronous"

        if mode.endswith("failure"):
            with pytest.raises(ValueError, match=f"{protocol} stream failure") as caught:
                async for _chunk in content:
                    pass
            caught_error = caught.value
        else:
            chunks = [chunk async for chunk in content]
            assert b"".join(chunks) == protocol.encode()

    response_identifier = response.headers[REQUEST_ID_HEADER]
    body_record = next(record for record in caplog.records if record.name == "localforge.stream")
    final_record = next(
        record
        for record in caplog.records
        if record.name == "localforge.request"
        and record.getMessage() in {"request completed", "request stream failed"}
    )

    assert body_record.__dict__["request_id"] == response_identifier
    assert body_record.__dict__["body_request_id"] == response_identifier
    assert final_record.__dict__["request_id"] == response_identifier
    assert final_record.getMessage() == (
        "request stream failed" if mode.endswith("failure") else "request completed"
    )
    assert (
        sum(
            record.name == "localforge.request"
            and record.getMessage() in {"request completed", "request stream failed"}
            for record in caplog.records
        )
        == 1
    )
    assert request_identifier.get() == NO_REQUEST_ID
    if caught_error is not None:
        assert getattr(caught_error, REQUEST_ID_EXCEPTION_ATTRIBUTE, None) == response_identifier


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["synchronous-success", "asynchronous-success"])
@pytest.mark.parametrize(
    "disconnect_event",
    ["success", "response-start", "first-body", "final-body"],
)
async def test_stream_transport_completion_is_finalized(
    caplog: pytest.LogCaptureFixture,
    mode: str,
    disconnect_event: str,
) -> None:
    """Finalize successful and disconnected stream delivery.

    Runs Django's real ASGI handler with successful or cancelled sends, proving terminal records
    represent the transport outcome around body iteration and transmission.

    Arguments:
        caplog: Fixture collecting records emitted during the request.
        mode: Stream iterator protocol selected by the test-only view.
        disconnect_event: ASGI response event that simulates transport cancellation.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup omits or duplicates the terminal request record.
    """
    request_received = False
    response_identifier: str | None = None
    body_events = 0

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and wait for handler cancellation.

        Returns the empty request body once, then blocks so request processing wins the handler's
        disconnect race and controls response cleanup.

        Arguments:
            None.

        Returns:
            One ASGI request event before blocking.

        Raises:
            asyncio.CancelledError: When the completed response cancels the disconnect listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Cancel transport delivery as soon as headers are available.

        Captures the generated request identifier before simulating a client disconnect that
        interrupts response transmission at the selected boundary.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: Always for the response-start event.
        """
        nonlocal body_events, response_identifier

        if message["type"] == "http.response.start":
            headers = {name.lower(): value for name, value in message["headers"]}
            response_identifier = headers[b"x-request-id"].decode()

            if disconnect_event == "response-start":
                raise asyncio.CancelledError

        if message["type"] == "http.response.body":
            body_events += 1

        if disconnect_event == "first-body" and body_events == 1:
            raise asyncio.CancelledError

        if (
            disconnect_event == "final-body"
            and message["type"] == "http.response.body"
            and not message.get("more_body", False)
        ):
            raise asyncio.CancelledError

    scope = _asgi_http_scope("/stream/", f"mode={mode}".encode())

    with (
        override_settings(ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO),
    ):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        await handler(scope, receive, send)
        await asyncio.sleep(STREAM_CLOSE_SETTLE_SECONDS)

    terminal_records = [
        record
        for record in caplog.records
        if record.name == "localforge.request"
        and record.getMessage() in {"request completed", "request stream failed"}
    ]

    assert response_identifier is not None
    assert len(terminal_records) == 1
    assert terminal_records[0].getMessage() == (
        "request completed" if disconnect_event == "success" else "request stream failed"
    )
    assert terminal_records[0].__dict__["request_id"] == response_identifier
    if disconnect_event != "response-start":
        cleanup_record = next(
            record
            for record in caplog.records
            if record.name == "localforge.stream" and record.getMessage().endswith("stream cleanup")
        )
        assert cleanup_record.__dict__["request_id"] == response_identifier
        assert cleanup_record.__dict__["body_request_id"] == response_identifier
    assert request_identifier.get() == NO_REQUEST_ID


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["synchronous-success", "asynchronous-success"])
async def test_receive_disconnect_finalizes_stream_failure(
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    """Finalize a stream when the receiver observes client disconnect.

    Lets outbound sends return normally while Django's listener consumes ``http.disconnect`` after
    the first body, reproducing server transports that do not raise on later sends.

    Arguments:
        caplog: Fixture collecting records emitted during the request.
        mode: Stream iterator protocol selected by the test-only view.

    Returns:
        None.

    Raises:
        AssertionError: If disconnect becomes success or cleanup loses correlation.
    """
    request_received = False
    first_body_observed = asyncio.Event()
    response_identifier: str | None = None

    async def receive() -> ASGIReceiveEvent:
        """Provide the request and disconnect after first-body delivery.

        Coordinates the inbound disconnect with response progress so source iteration has begun
        before Django cancels the response task.

        Arguments:
            None.

        Returns:
            Initial request body or client disconnect.

        Raises:
            None.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await first_body_observed.wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Accept response events while recording first-body progress.

        Models Uvicorn's non-raising send behavior after protocol disconnect while preserving
        response events unchanged.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            None.
        """
        nonlocal response_identifier

        if message["type"] == "http.response.start":
            headers = {name.lower(): value for name, value in message["headers"]}
            response_identifier = headers[b"x-request-id"].decode()

        if message["type"] == "http.response.body":
            first_body_observed.set()
            await asyncio.sleep(0)

    with (
        override_settings(ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO),
    ):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        await handler(
            _asgi_http_scope("/stream/", f"mode={mode}".encode()),
            receive,
            send,
        )

    terminal = next(
        record
        for record in caplog.records
        if record.name == "localforge.request"
        and record.getMessage() in {"request completed", "request stream failed"}
    )
    cleanup = next(
        record
        for record in caplog.records
        if record.name == "localforge.stream" and record.getMessage().endswith("stream cleanup")
    )

    assert response_identifier is not None
    assert terminal.getMessage() == "request stream failed"
    assert terminal.__dict__["request_id"] == response_identifier
    assert cleanup.__dict__["request_id"] == response_identifier
    assert cleanup.__dict__["body_request_id"] == response_identifier


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path_value", "query_string"),
    [
        ("/ordinary/", b""),
        ("/stream/", b"mode=asynchronous-success"),
    ],
)
async def test_terminal_send_success_beats_ready_post_response_disconnect(
    caplog: pytest.LogCaptureFixture,
    path_value: str,
    query_string: bytes,
) -> None:
    """Record success when transport accepts the terminal body.

    Models a short-lived client whose disconnect becomes readable immediately after a synchronous
    terminal send, and treats the accepted terminal event as completed delivery.

    Arguments:
        caplog: Fixture collecting records emitted during the request.
        path_value: Test-only ordinary or streaming response path.
        query_string: Query selecting asynchronous streaming when required.

    Returns:
        None.

    Raises:
        AssertionError: If an orderly post-response disconnect becomes a false stream failure.
    """
    request_received = False
    terminal_body_seen = asyncio.Event()

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event followed by a ready disconnect.

        Waits only until the non-yielding sender receives the terminal body, then exposes the
        disconnect without requiring any additional transport suspension.

        Arguments:
            None.

        Returns:
            Initial request body or client disconnect.

        Raises:
            None.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await terminal_body_seen.wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Accept response events without an asynchronous scheduling point.

        Marks terminal delivery synchronously and returns immediately, matching Uvicorn after its
        underlying transport is already disconnected.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            None.
        """
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            terminal_body_seen.set()

    with override_settings(ROOT_URLCONF=__name__), caplog.at_level(logging.INFO):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        await handler(_asgi_http_scope(path_value, query_string), receive, send)

    terminal = next(
        record
        for record in caplog.records
        if record.name == "localforge.request"
        and record.getMessage() in {"request completed", "request stream failed"}
    )

    assert terminal.getMessage() == "request completed"


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["synchronous-success", "asynchronous-success"])
async def test_receive_disconnect_before_first_send_closes_retained_source(
    mocker: MockerFixture,
    mode: str,
) -> None:
    """Close a wrapped stream when disconnect wins before the first response event.

    Pauses outer response middleware after request-context wrapping, reproducing child-task
    cancellation before the observer sees a send event or receives its finalizer.

    Arguments:
        mocker: Fixture pausing outer response middleware and retaining the source.
        mode: Stream iterator protocol selected by the test-only view.

    Returns:
        None.

    Raises:
        AssertionError: If cancellation leaves the original source open.
    """
    middleware_entered = threading.Event()
    disconnect_returned = threading.Event()
    request_received = False
    original_process_response = cast(
        "Callable[[object, HttpRequest, HttpResponse], HttpResponse]",
        PrometheusBeforeMiddleware.process_response,
    )

    if mode.startswith("asynchronous"):
        source: AsyncGeneratorType[bytes, None] | GeneratorType[bytes, None, None] = cast(
            "AsyncGeneratorType[bytes, None]",
            _asynchronous_stream_body(fail=False),
        )
        mocker.patch(
            f"{__name__}._asynchronous_stream_body",
            return_value=source,
        )
    else:
        source = cast(
            "GeneratorType[bytes, None, None]",
            _synchronous_stream_body(fail=False),
        )
        mocker.patch(
            f"{__name__}._synchronous_stream_body",
            return_value=source,
        )

    def pause_process_response(
        middleware: object,
        request: HttpRequest,
        response: HttpResponse,
    ) -> HttpResponse:
        """Pause after the stream finalizer exists but before response sending.

        Lets the receive listener win Django's task race while the response and retained source
        remain owned by the cancelled request-processing child task.

        Arguments:
            middleware: Active bounded Prometheus middleware instance.
            request: Request whose response is being finalized.
            response: Wrapped response not yet sent to the transport.

        Returns:
            Response returned by the original middleware hook.

        Raises:
            AssertionError: If the disconnect listener does not run promptly.
        """
        result = original_process_response(middleware, request, response)
        middleware_entered.set()
        assert disconnect_returned.wait(1)
        return result

    mocker.patch.object(
        PrometheusBeforeMiddleware,
        "process_response",
        pause_process_response,
    )

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and disconnect during response middleware.

        Waits until stream wrapping has completed in the child request task, then returns a
        disconnect before the first outbound response event.

        Arguments:
            None.

        Returns:
            Initial request body or client disconnect.

        Raises:
            None.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.to_thread(middleware_entered.wait)
        disconnect_returned.set()
        return {"type": "http.disconnect"}

    async def send(_message: ASGISendEvent) -> None:
        """Reject any unexpected response event.

        Proves disconnect cancellation wins before Django starts transport delivery.
        Makes any accidental send fail the regression immediately.

        Arguments:
            _message: Unexpected ASGI response event.

        Returns:
            None.

        Raises:
            AssertionError: Always, because no response event should be sent.
        """
        message_text = "disconnect should precede response sending"
        raise AssertionError(message_text)

    try:
        with override_settings(ROOT_URLCONF=__name__):
            handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
            await handler(
                _asgi_http_scope("/stream/", f"mode={mode}".encode()),
                receive,
                send,
            )

        if isinstance(source, AsyncGeneratorType):
            assert source.ag_frame is None
        else:
            assert source.gi_frame is None
    finally:
        if isinstance(source, AsyncGeneratorType):
            await source.aclose()
        else:
            source.close()


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_header_failure_cleanup_can_use_thread_sensitive_executor(
    mocker: MockerFixture,
) -> None:
    """Complete asynchronous source cleanup through Django's occupied executor.

    Reproduces a pre-advanced stream whose finalizer requires thread-sensitive synchronous work,
    proving response cleanup schedules back to the event loop without deadlocking that executor.

    Arguments:
        mocker: Fixture replacing the stream factory with the retained source.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup does not complete before the bounded request deadline.
        TimeoutError: If synchronous response cleanup deadlocks the thread-sensitive executor.
    """
    cleanup_finished = threading.Event()
    request_received = False

    async def source_content() -> AsyncIterator[bytes]:
        """Yield two chunks and use thread-sensitive work during cleanup.

        Suspends after the view consumes the first chunk, then records deterministic closure from
        a synchronous callback requiring Django's thread-sensitive executor.

        Arguments:
            None.

        Returns:
            Asynchronous iterator yielding response chunks.

        Yields:
            Two response body chunks.

        Raises:
            None.
        """
        try:
            yield b"initial"
            yield b"remaining"
        finally:
            await sync_to_async(cleanup_finished.set, thread_sensitive=True)()

    source = cast("AsyncGeneratorType[bytes, None]", source_content())
    mocker.patch(
        f"{__name__}._asynchronous_stream_body",
        return_value=source,
    )

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and block the disconnect listener.

        Keeps inbound transport connected so response-start cancellation exclusively triggers
        response cleanup.

        Arguments:
            None.

        Returns:
            Empty request body before blocking.

        Raises:
            asyncio.CancelledError: When response completion cancels the listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Reject response headers before body-wrapper iteration.

        Forces Django's synchronous response closer to schedule asynchronous generator cleanup
        while the generator finalizer needs the same thread-sensitive executor.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: When response headers are emitted.
        """
        if message["type"] == "http.response.start":
            raise asyncio.CancelledError

    with override_settings(ROOT_URLCONF=__name__):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        async with asyncio.timeout(STREAM_CLEANUP_TEST_TIMEOUT_SECONDS):
            await handler(_asgi_http_scope("/preadvanced-stream/"), receive, send)

    assert cleanup_finished.is_set()
    assert source.ag_frame is None


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_asynchronous_cleanup_failure_preserves_request_identifier(
    mocker: MockerFixture,
) -> None:
    """Correlate a source-cleanup failure escaping the ASGI lifecycle.

    Rejects response headers for a pre-advanced source whose finalizer raises, proving the outer
    cleanup boundary attaches the request identifier before restoring request context.

    Arguments:
        mocker: Fixture replacing the stream factory with the retained source.

    Returns:
        None.

    Raises:
        AssertionError: If the cleanup exception loses its request identifier.
        RuntimeError: Contained when asynchronous source cleanup fails.
    """
    request_received = False
    response_identifier: str | None = None

    async def source_content() -> AsyncIterator[bytes]:
        """Yield two chunks and fail during asynchronous cleanup.

        Suspends after the view consumes its first value, then raises only when response cleanup
        closes the retained source.

        Arguments:
            None.

        Returns:
            Asynchronous iterator yielding response chunks.

        Yields:
            Two response body chunks.

        Raises:
            RuntimeError: When the retained source is closed.
        """
        try:
            yield b"initial"
            yield b"remaining"
        finally:
            message_text = "asynchronous cleanup failure"
            raise RuntimeError(message_text)

    source = cast("AsyncGeneratorType[bytes, None]", source_content())
    mocker.patch(
        f"{__name__}._asynchronous_stream_body",
        return_value=source,
    )

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and block the disconnect listener.

        Keeps inbound transport connected so response-start cancellation triggers source cleanup.
        Returns no disconnect before the retained source is closed.

        Arguments:
            None.

        Returns:
            Empty request body before blocking.

        Raises:
            asyncio.CancelledError: When response completion cancels the listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Reject response headers after capturing request correlation.

        Prevents body iteration while retaining the response identifier needed to validate the
        cleanup failure after the request context is restored.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: When response headers are emitted.
        """
        nonlocal response_identifier

        if message["type"] == "http.response.start":
            headers = {name.lower(): value for name, value in message["headers"]}
            response_identifier = headers[b"x-request-id"].decode()
            raise asyncio.CancelledError

    with override_settings(ROOT_URLCONF=__name__):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        with pytest.raises(RuntimeError, match="asynchronous cleanup failure") as caught:
            await handler(_asgi_http_scope("/preadvanced-stream/"), receive, send)

    assert response_identifier is not None
    assert getattr(caught.value, REQUEST_ID_EXCEPTION_ATTRIBUTE, None) == response_identifier
    escaped_record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="cleanup escaped",
        args=(),
        exc_info=(RuntimeError, caught.value, caught.value.__traceback__),
    )
    assert RequestContextFilter().filter(escaped_record) is True
    assert escaped_record.__dict__["request_id"] == response_identifier
    assert source.ag_frame is None


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["synchronous-success", "asynchronous-success"])
@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
async def test_transport_error_closes_retained_stream_source(
    caplog: pytest.LogCaptureFixture,
    mocker: MockerFixture,
    mode: str,
    error_type: type[Exception],
) -> None:
    """Close retained stream sources after non-cancellation transport errors.

    Raises from first-body delivery through Django's real handler, proving the outer response
    lifecycle owns synchronous and asynchronous cleanup on every send failure.

    Arguments:
        caplog: Fixture collecting records emitted during the request.
        mocker: Fixture replacing the selected stream factory.
        mode: Stream iterator protocol selected by the test-only view.
        error_type: Non-cancellation transport exception to raise.

    Returns:
        None.

    Raises:
        AssertionError: If the source remains suspended or cleanup loses correlation.
    """
    request_received = False
    response_identifier: str | None = None
    if mode.startswith("asynchronous"):
        source: AsyncGeneratorType[bytes, None] | GeneratorType[bytes, None, None] = cast(
            "AsyncGeneratorType[bytes, None]",
            _asynchronous_stream_body(fail=False),
        )
        mocker.patch(
            f"{__name__}._asynchronous_stream_body",
            return_value=source,
        )
    else:
        source = cast(
            "GeneratorType[bytes, None, None]",
            _synchronous_stream_body(fail=False),
        )
        mocker.patch(
            f"{__name__}._synchronous_stream_body",
            return_value=source,
        )

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and block the disconnect listener.

        Keeps the receive side connected so the configured outbound exception exclusively controls
        response failure.

        Arguments:
            None.

        Returns:
            Empty request body before blocking.

        Raises:
            asyncio.CancelledError: When response completion cancels the listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Raise the selected non-cancellation transport failure.

        Captures the response identifier before rejecting the first body event.
        Leaves response-header delivery successful.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            Exception: Selected transport failure on first-body delivery.
        """
        nonlocal response_identifier

        if message["type"] == "http.response.start":
            headers = {name.lower(): value for name, value in message["headers"]}
            response_identifier = headers[b"x-request-id"].decode()

        if message["type"] == "http.response.body":
            message_text = "transport failure"
            raise error_type(message_text)

    with override_settings(ROOT_URLCONF=__name__), caplog.at_level(logging.INFO):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        with pytest.raises(error_type, match="transport failure") as caught:
            await handler(
                _asgi_http_scope("/stream/", f"mode={mode}".encode()),
                receive,
                send,
            )

    cleanup = next(
        record
        for record in caplog.records
        if record.name == "localforge.stream" and record.getMessage().endswith("stream cleanup")
    )

    assert response_identifier is not None
    assert getattr(caught.value, REQUEST_ID_EXCEPTION_ATTRIBUTE, None) == response_identifier
    escaped_record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="transport escaped",
        args=(),
        exc_info=(error_type, caught.value, caught.value.__traceback__),
    )
    assert RequestContextFilter().filter(escaped_record) is True
    assert escaped_record.__dict__["request_id"] == response_identifier
    assert cleanup.__dict__["request_id"] == response_identifier
    assert cleanup.__dict__["body_request_id"] == response_identifier
    if isinstance(source, AsyncGeneratorType):
        assert source.ag_frame is None
    else:
        assert source.gi_frame is None


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_header_failure_closes_preadvanced_asynchronous_source(
    caplog: pytest.LogCaptureFixture,
    mocker: MockerFixture,
) -> None:
    """Close a pre-advanced source when response headers cannot be delivered.

    Retains a generator the view has already advanced, proving cleanup targets the original source
    even when the correlation wrapper never begins iteration.

    Arguments:
        caplog: Fixture collecting records emitted during the request.
        mocker: Fixture replacing the stream factory with the retained source.

    Returns:
        None.

    Raises:
        AssertionError: If header failure leaves the original source suspended.
    """
    request_received = False
    response_identifier: str | None = None
    source = cast("AsyncGeneratorType[bytes, None]", _asynchronous_stream_body(fail=False))
    mocker.patch(
        f"{__name__}._asynchronous_stream_body",
        return_value=source,
    )

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and block the disconnect listener.

        Keeps inbound transport connected so response-start cancellation owns the failure path.
        Returns no disconnect before header delivery.

        Arguments:
            None.

        Returns:
            Empty request body before blocking.

        Raises:
            asyncio.CancelledError: When response completion cancels the listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Reject response headers after capturing their identifier.

        Prevents body-wrapper iteration while leaving the original pre-advanced source suspended
        until response cleanup runs.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: When response headers are emitted.
        """
        nonlocal response_identifier

        if message["type"] == "http.response.start":
            headers = {name.lower(): value for name, value in message["headers"]}
            response_identifier = headers[b"x-request-id"].decode()
            raise asyncio.CancelledError

    with (
        override_settings(ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO),
    ):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        await handler(_asgi_http_scope("/preadvanced-stream/"), receive, send)

    cleanup = next(
        record
        for record in caplog.records
        if record.name == "localforge.stream"
        and record.getMessage() == "asynchronous stream cleanup"
    )

    assert response_identifier is not None
    assert source.ag_frame is None
    assert cleanup.__dict__["request_id"] == response_identifier
    assert cleanup.__dict__["body_request_id"] == response_identifier


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_ordinary_response_waits_for_transport_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Finalize an ordinary response after delayed body delivery fails.

    Runs Django's real ASGI handler with a non-streaming response and delayed terminal send,
    proving latency includes transmission time and cancellation cannot retain a success record.

    Arguments:
        caplog: Fixture collecting records emitted during the request.

    Returns:
        None.

    Raises:
        AssertionError: If completion precedes transport delivery or reports success.
    """
    request_received = False
    response_identifier: str | None = None

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and block the disconnect listener.

        Allows response processing to control cancellation through the outbound transport without
        introducing an inbound disconnect.

        Arguments:
            None.

        Returns:
            Empty request body before blocking.

        Raises:
            asyncio.CancelledError: When response completion cancels the listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Delay and reject terminal body delivery.

        Captures the generated identifier from response headers before simulating a transport that
        fails only after measurable transmission latency.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: After the terminal response body delay.
        """
        nonlocal response_identifier

        if message["type"] == "http.response.start":
            headers = {name.lower(): value for name, value in message["headers"]}
            response_identifier = headers[b"x-request-id"].decode()

        if message["type"] == "http.response.body" and not message.get("more_body", False):
            await asyncio.sleep(TRANSPORT_DELAY_SECONDS)
            raise asyncio.CancelledError

    with (
        override_settings(ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO),
    ):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        await handler(_asgi_http_scope("/missing/"), receive, send)

    terminal = next(
        record
        for record in caplog.records
        if record.name == "localforge.request"
        and record.getMessage() in {"request completed", "request stream failed"}
    )

    assert response_identifier is not None
    assert terminal.getMessage() == "request stream failed"
    assert terminal.__dict__["request_id"] == response_identifier
    assert terminal.__dict__["duration_seconds"] >= TRANSPORT_DELAY_SECONDS


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_pre_middleware_rejection_is_correlated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Correlate a request rejected before Django constructs ``HttpRequest``.

    Supplies invalid query bytes through the real ASGI handler, proving the outer boundary injects
    an identifier and records the resulting response without middleware participation.

    Arguments:
        caplog: Fixture collecting records emitted during the request.

    Returns:
        None.

    Raises:
        AssertionError: If the rejection lacks matching response and log correlation.
    """
    request_received = False
    events: list[ASGISendEvent] = []

    async def receive() -> ASGIReceiveEvent:
        """Provide the invalid request body and block afterward.

        Keeps the disconnect listener inactive so only request construction determines the
        response outcome.

        Arguments:
            None.

        Returns:
            Empty request body before blocking.

        Raises:
            asyncio.CancelledError: When response completion cancels the listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Collect one rejection response event.

        Preserves the event stream for status, header, and terminal-body assertions without
        altering transport behavior.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            None.
        """
        events.append(message)

    with caplog.at_level(logging.INFO):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        await handler(_asgi_http_scope("/health/", b"bad=\xff"), receive, send)

    start = next(event for event in events if event["type"] == "http.response.start")
    headers = {name.lower(): value for name, value in start["headers"]}
    response_identifier = headers[b"x-request-id"].decode()
    terminal = next(record for record in caplog.records if record.name == "localforge.request")

    assert start["status"] == HTTPStatus.BAD_REQUEST
    assert response_identifier
    assert terminal.__dict__["request_id"] == response_identifier
    assert terminal.__dict__["http_status"] == HTTPStatus.BAD_REQUEST
    assert terminal.getMessage() == "request completed"


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_retained_asynchronous_source_closes_on_disconnect(
    caplog: pytest.LogCaptureFixture,
    mocker: MockerFixture,
) -> None:
    """Close the original asynchronous source while another reference retains it.

    Cancels real-handler body delivery and keeps a direct source reference, proving response
    cleanup explicitly owns generator closure rather than relying on garbage collection.

    Arguments:
        caplog: Fixture collecting records emitted during the request.
        mocker: Fixture replacing the stream factory with the retained source.

    Returns:
        None.

    Raises:
        AssertionError: If source cleanup remains suspended or loses correlation.
    """
    request_received = False
    response_identifier: str | None = None
    source = cast("AsyncGeneratorType[bytes, None]", _asynchronous_stream_body(fail=False))
    mocker.patch(
        f"{__name__}._asynchronous_stream_body",
        return_value=source,
    )

    async def receive() -> ASGIReceiveEvent:
        """Provide one request event and block the disconnect listener.

        Leaves cancellation under the outbound sender while retaining the original source and
        keeping inbound transport state connected.

        Arguments:
            None.

        Returns:
            Empty request body before blocking.

        Raises:
            asyncio.CancelledError: When response completion cancels the listener.
        """
        nonlocal request_received

        if not request_received:
            request_received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Reject the first body event after capturing correlation.

        Reproduces a disconnect after source iteration starts without releasing the test's direct
        generator reference.

        Arguments:
            message: ASGI response event emitted by Django.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: When the first response body is emitted.
        """
        nonlocal response_identifier

        if message["type"] == "http.response.start":
            headers = {name.lower(): value for name, value in message["headers"]}
            response_identifier = headers[b"x-request-id"].decode()

        if message["type"] == "http.response.body":
            raise asyncio.CancelledError

    with (
        override_settings(ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO),
    ):
        handler = finalize_streaming_asgi(cast("ASGI3Application", ASGIHandler()))
        await handler(
            _asgi_http_scope("/stream/", b"mode=asynchronous-success"),
            receive,
            send,
        )

    cleanup = next(
        record
        for record in caplog.records
        if record.name == "localforge.stream"
        and record.getMessage() == "asynchronous stream cleanup"
    )

    assert response_identifier is not None
    assert source.ag_frame is None
    assert cleanup.__dict__["request_id"] == response_identifier
    assert cleanup.__dict__["body_request_id"] == response_identifier


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.asyncio
async def test_asynchronous_stream_closes_safely_from_another_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Finalize an interrupted asynchronous body from another context.

    Starts iteration in the request task and closes the suspended generator in a fresh task context,
    reproducing server disconnect cleanup without resetting a foreign context-variable token.

    Arguments:
        caplog: Fixture collecting records emitted during the request.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup raises, leaks context, or loses correlation.
    """
    client = AsyncClient()

    with (
        override_settings(ROOT_URLCONF=__name__),
        caplog.at_level(logging.INFO),
    ):
        response = await client.get("/stream/", {"mode": "asynchronous-success"})
        streaming_response = cast("StreamingHttpResponse", cast("object", response))
        content = cast("AsyncGenerator[bytes]", streaming_response.streaming_content)
        assert await anext(content) == b"asynchronous"
        assert request_identifier.get() == NO_REQUEST_ID
        close_task = Context().run(asyncio.create_task, content.aclose())
        await close_task
        await asyncio.to_thread(streaming_response.close)
        await asyncio.sleep(STREAM_CLOSE_SETTLE_SECONDS)

    response_identifier = response.headers[REQUEST_ID_HEADER]
    final_record = next(
        record
        for record in caplog.records
        if record.name == "localforge.request" and record.getMessage() == "request stream failed"
    )

    assert final_record.__dict__["request_id"] == response_identifier
    assert request_identifier.get() == NO_REQUEST_ID


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_request_identifier_correlates_response_and_logs(
    client: Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return the request identifier attached to application logs.

    Makes one request and compares its response header with the completion record, proving a value
    reported by a user can select the corresponding structured log entry.

    Arguments:
        client: Django test client supplied by the framework.
        caplog: Fixture collecting records emitted during the request.

    Returns:
        None.

    Raises:
        AssertionError: If the identifier is absent or differs between surfaces.
    """
    query_stream = StringIO()
    query_handler = logging.StreamHandler(query_stream)
    query_handler.addFilter(RequestContextFilter())
    query_handler.addFilter(QueryRedactionFilter())
    query_handler.setFormatter(StructuredFormatter())
    query_logger = logging.getLogger("django.db.backends")
    original_handlers = query_logger.handlers
    original_level = query_logger.level
    original_propagate = query_logger.propagate

    query_logger.handlers = [query_handler]
    query_logger.setLevel(logging.DEBUG)
    query_logger.propagate = False

    try:
        with (
            override_settings(DEBUG=True),
            override_settings(ROOT_URLCONF=__name__),
            caplog.at_level(logging.INFO, logger="localforge.request"),
        ):
            response = client.get("/database-probe/")
    finally:
        query_logger.handlers = original_handlers
        query_logger.setLevel(original_level)
        query_logger.propagate = original_propagate

    completion = next(record for record in caplog.records if record.name == "localforge.request")
    query_records = [
        json.loads(line) for line in query_stream.getvalue().splitlines() if line.strip()
    ]
    response_identifier = response.headers[REQUEST_ID_HEADER]

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert response_identifier == completion.__dict__["request_id"]
    assert query_records
    assert {record["request_id"] for record in query_records} == {response_identifier}
    assert completion.__dict__["http_method"] == "GET"
    assert completion.__dict__["http_path"] == "/database-probe/"
    assert completion.__dict__["http_status"] == HTTPStatus.NO_CONTENT


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_request_identifier_reaches_django_not_found_records(
    client: Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Correlate framework not-found records after inner request middleware returns.

    Requests a missing route and compares Django's informational response record with the returned
    header, proving public scanner normalization retains request correlation.

    Arguments:
        client: Django test client supplied by the framework.
        caplog: Fixture collecting records emitted during the request.

    Returns:
        None.

    Raises:
        AssertionError: If the framework record loses its response identifier or safe severity.
    """
    with caplog.at_level(logging.INFO, logger="django.request"):
        response = client.get("/missing/")

    not_found = next(record for record in caplog.records if record.name == "django.request")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert not_found.levelno == logging.INFO
    assert not_found.__dict__["request_id"] == response.headers[REQUEST_ID_HEADER]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_login_attempt_logs_no_password(
    client: Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep a submitted login password out of every structured record.

    Posts a distinctive credential to the real administration login view and formats all records
    emitted during that request, asserting the submitted value never reaches the retained stream.

    Arguments:
        client: Django test client supplied by the framework.
        caplog: Fixture collecting records emitted during the request.

    Returns:
        None.

    Raises:
        AssertionError: If the submitted password appears in any formatted record.
    """
    submitted_password = secrets.token_urlsafe(24)

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/admin/login/",
            {"password": submitted_password, "username": "missing-user"},
        )

    rendered = "\n".join(StructuredFormatter().format(record) for record in caplog.records)

    assert response.status_code == HTTPStatus.OK
    assert submitted_password not in rendered
    assert all(json.loads(line) for line in rendered.splitlines())
