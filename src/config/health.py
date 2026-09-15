"""Health-check request correlation.

Runs synchronous dependency checks in executor contexts copied from the HTTP request, so logs from
those checks carry the same request identifier returned to the caller.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext, suppress
from contextvars import copy_context
from threading import BoundedSemaphore
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast, override

from django.db import connections
from health_check.checks import Database
from health_check.exceptions import ServiceUnavailable
from health_check.views import HealthCheckView
from psycopg import AsyncConnection
from psycopg import Error as PsycopgError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractContextManager
    from typing import Protocol

    from django.http import HttpResponse
    from django.views import View
    from health_check.base import HealthCheck

    class DatabaseHealthCheck(Protocol):
        """Describe the database alias required by the check function.

        Inherits from ``Protocol`` and defines the structural contract shared by the dynamically
        derived health check without importing untyped third-party inheritance into runtime type
        analysis.

        Attributes:
            alias: Django database alias to probe.

        Members:
            None.
        """

        alias: str


Arguments = ParamSpec("Arguments")
Result = TypeVar("Result")
HEALTH_CHECK_WORKERS = 5
HEALTH_CHECK_STATEMENT_TIMEOUT_MILLISECONDS = 5000
HEALTH_CHECK_CLIENT_TIMEOUT_SECONDS = 6.0


class HealthCheckCapacityError(RuntimeError):
    """Report exhausted health-check execution capacity.

    Inherits from ``RuntimeError`` and signals that all bounded worker slots remain occupied, so
    callers can fail promptly rather than entering an unbounded executor queue.

    Attributes:
        None beyond those the base exception defines.

    Members:
        None.
    """


class ContextPreservingThreadPoolExecutor(ThreadPoolExecutor):
    """Submit work with a copy of the caller's context.

    Inherits from ``ThreadPoolExecutor`` and wraps each callable with ``Context.run``, preserving
    request-local values when an asynchronous view delegates blocking dependency checks.

    Attributes:
        None beyond those the base executor defines.

    Members:
        submit: Schedule a callable inside the submitting context.
    """

    def __init__(self, *, max_workers: int) -> None:
        """Create an executor with matching worker and admission limits.

        Allocates one admission slot per worker, preventing cancelled requests from building an
        unbounded queue behind dependency checks that have not yet returned.

        Arguments:
            max_workers: Maximum concurrent health checks.

        Returns:
            None.

        Raises:
            ValueError: If max_workers is not positive.
        """
        super().__init__(max_workers=max_workers)
        self._admission = BoundedSemaphore(max_workers)

    @override
    def submit(
        self,
        fn: Callable[Arguments, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Future[Result]:
        """Schedule a callable inside the submitting context.

        Captures the context at submission time rather than executor construction, so one view
        instance can safely serve requests carrying different identifiers.

        Arguments:
            fn: Callable to execute.
            *args: Positional arguments for the callable.
            **kwargs: Keyword arguments for the callable.

        Returns:
            Future representing the submitted work or immediate capacity failure.

        Raises:
            RuntimeError: If the executor has already shut down.
        """
        if not self._admission.acquire(blocking=False):
            failed: Future[Result] = Future()
            failed.set_exception(
                HealthCheckCapacityError("health-check execution capacity exhausted")
            )

            return failed

        context = copy_context()
        future = super().submit(lambda: context.run(fn, *args, **kwargs))
        future.add_done_callback(lambda _future: self._admission.release())

        return future


health_check_executor = ContextPreservingThreadPoolExecutor(max_workers=HEALTH_CHECK_WORKERS)


def context_preserving_executor(
    _view: object,
) -> AbstractContextManager[ContextPreservingThreadPoolExecutor]:
    """Provide the context-preserving executor.

    Yields the process-wide bounded executor without transferring ownership to the request, so a
    cancelled health check never synchronously joins worker threads on the event loop.

    Arguments:
        _view: Health view instance invoking the method.

    Returns:
        Context manager yielding the executor.

    Raises:
        None.
    """
    return nullcontext(health_check_executor)


async def _execute_database_probe(
    connection: AsyncConnection[tuple[object, ...]],
) -> tuple[object, ...] | None:
    """Execute one PostgreSQL readiness query on an open asynchronous connection.

    Applies the server-side timeout before the readiness statement while the deadline supervisor
    retains ownership of connection closure.

    Arguments:
        connection: Disposable psycopg connection owned by the caller.

    Returns:
        First row returned by the readiness query.

    Raises:
        PsycopgError: If connection or query execution fails.
    """
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            [str(HEALTH_CHECK_STATEMENT_TIMEOUT_MILLISECONDS)],
        )
        await cursor.execute("SELECT 1")
        return await cursor.fetchone()


async def _execute_database_probe_with_deadline(
    connection_parameters: dict[str, object],
) -> tuple[object, ...] | None:
    """Bound connection, validation, and query network I/O with one client deadline.

    Cancels and closes the disposable asynchronous connection when any phase exceeds the deadline,
    returning the bounded health executor worker to service subsequent requests.

    Arguments:
        connection_parameters: Django-derived psycopg connection arguments.

    Returns:
        First row returned by the readiness query.

    Raises:
        TimeoutError: If the complete database probe exceeds the client deadline.
        PsycopgError: If connection or query execution fails.
    """
    connect = cast(
        "Callable[..., Awaitable[AsyncConnection[tuple[object, ...]]]]",
        AsyncConnection.connect,
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    connection = await asyncio.wait_for(
        connect(**connection_parameters),
        timeout=HEALTH_CHECK_CLIENT_TIMEOUT_SECONDS,
    )
    probe = asyncio.create_task(_execute_database_probe(connection))
    remaining = max(
        0.0,
        HEALTH_CHECK_CLIENT_TIMEOUT_SECONDS - (loop.time() - started),
    )

    try:
        completed, _pending = await asyncio.wait(
            {probe},
            timeout=remaining,
        )

        if completed:
            return probe.result()

        await connection.close()
        probe.cancel()
        with suppress(asyncio.CancelledError, OSError, PsycopgError):
            await probe

        message = "database health check exceeded its client deadline"
        raise TimeoutError(message)
    finally:
        await connection.close()


def _validate_database_pool(database_connection: object) -> None:
    """Validate capacity in Django's configured connection pool.

    Reads the pool's in-memory capacity counters without invoking its synchronous connection-health
    callback, leaving transport validation to the separately deadline-bound asynchronous probe.

    Arguments:
        database_connection: Django PostgreSQL connection wrapper carrying the configured pool.

    Returns:
        None.

    Raises:
        ServiceUnavailable: If pooling is absent or every configured connection is checked out.
    """
    pool = getattr(database_connection, "pool", None)
    if pool is None:
        message = "Database health check requires the configured connection pool"
        raise ServiceUnavailable(message)

    statistics = pool.get_stats()
    available = int(statistics.get("pool_available", 0))
    size = int(statistics.get("pool_size", 0))
    maximum = int(statistics.get("pool_max", pool.max_size))

    if available == 0 and size >= maximum:
        message = "Database health check found the connection pool exhausted"
        raise ServiceUnavailable(message)


def run_timed_database_check(check: DatabaseHealthCheck) -> None:
    """Execute the database health query with server and client deadlines.

    Validates ORM pool capacity without transport I/O, then opens a disposable asynchronous
    connection and bounds its complete lifecycle so stalled network I/O cannot retain an executor
    slot indefinitely.

    Arguments:
        check: Database health-check instance carrying the configured alias.

    Returns:
        None.

    Raises:
        ServiceUnavailable: If the probe times out, fails, or returns an unexpected result.
    """
    database_connection = connections[check.alias]
    _validate_database_pool(database_connection)
    connection_parameters = cast(
        "dict[str, object]",
        database_connection.get_connection_params(),
    )
    connection_parameters.pop("cursor_factory", None)
    try:
        result = asyncio.run(
            _execute_database_probe_with_deadline(connection_parameters),
            loop_factory=asyncio.SelectorEventLoop,
        )
    except (TimeoutError, OSError, PsycopgError) as error:
        message = "Database health check failed"
        raise ServiceUnavailable(message) from error

    if result != (1,):
        message = "Database health check returned an unexpected result"
        raise ServiceUnavailable(message)


TimedDatabaseHealthCheck = cast(
    "type[HealthCheck]",
    type(
        "TimedDatabaseHealthCheck",
        (Database,),
        {
            "__doc__": (
                "Check database readiness with a bounded statement deadline.\n\n"
                "Inherits from the standard database health check and executes the probe inside a "
                "transaction-local timeout so stalled work releases its executor slot."
            ),
            "run": run_timed_database_check,
        },
    ),
)


CorrelatedHealthCheckView = cast(
    "type[View[HttpResponse]]",
    type(
        "CorrelatedHealthCheckView",
        (HealthCheckView,),
        {
            "__doc__": (
                "Run health checks without losing request context.\n\n"
                "Inherits from HealthCheckView and supplies a context-preserving executor for its "
                "synchronous checks, so dependency log records retain the request identifier."
            ),
            "get_executor": context_preserving_executor,
        },
    ),
)
