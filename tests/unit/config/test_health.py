"""Unit tests for health-check request correlation.

Verifies executor work inherits request-local state and the health view selects that executor, so
dependency logs can be correlated with the response that triggered them.
"""

import asyncio
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from health_check.exceptions import ServiceUnavailable
from psycopg import Error as PsycopgError

from config.health import (
    HEALTH_CHECK_STATEMENT_TIMEOUT_MILLISECONDS,
    ContextPreservingThreadPoolExecutor,
    CorrelatedHealthCheckView,
    HealthCheckCapacityError,
    _execute_database_probe,
    _validate_database_pool,
    context_preserving_executor,
    health_check_executor,
    run_timed_database_check,
)
from config.logs import NO_REQUEST_ID, request_identifier

if TYPE_CHECKING:
    from concurrent.futures import Future

    from pytest_mock import MockerFixture

BLOCKING_CHECK_SECONDS = 0.5
CANCELLATION_SETTLE_SECONDS = 0.05
MAXIMUM_CANCELLATION_SECONDS = 0.2
PROBE_TIMEOUT_SECONDS = 0.01


@pytest.mark.unit
def test_executor_work_inherits_the_submitting_context() -> None:
    """Carry the request identifier into one executor job.

    Sets a request-local value before submission and reads it in the worker, proving the health
    check's thread boundary does not replace correlation with the outside-request sentinel.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the worker loses the context.
    """
    token = request_identifier.set("request-1234")
    try:
        with ContextPreservingThreadPoolExecutor(max_workers=1) as executor:
            result: Future[str] = executor.submit(request_identifier.get)

        assert result.result() == "request-1234"
    finally:
        request_identifier.reset(token)

    assert request_identifier.get() == NO_REQUEST_ID


@pytest.mark.unit
def test_health_view_selects_the_context_preserving_executor() -> None:
    """Use the executor that retains request-local logging fields.

    Confirms the configured view overrides the upstream default executor, which drops context when
    it runs synchronous checks from the asynchronous health endpoint.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the view returns another executor.
    """
    assert CorrelatedHealthCheckView.__dict__["get_executor"] is context_preserving_executor

    with context_preserving_executor(object()) as executor:
        assert isinstance(executor, ContextPreservingThreadPoolExecutor)
        assert executor is health_check_executor


@pytest.mark.unit
def test_executor_rejects_work_when_all_worker_slots_are_occupied() -> None:
    """Reject health checks instead of building an unbounded queue.

    Occupies the executor's only worker and confirms another submission fails immediately, then
    proves completing the original check releases admission for the next request.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If saturation queues work or the slot is not released.
    """
    release = threading.Event()

    with ContextPreservingThreadPoolExecutor(max_workers=1) as executor:
        occupied = executor.submit(release.wait, BLOCKING_CHECK_SECONDS)
        rejected = executor.submit(lambda: "queued")

        with pytest.raises(HealthCheckCapacityError):
            rejected.result()

        release.set()
        assert occupied.result() is True
        assert executor.submit(lambda: "accepted").result() == "accepted"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_database_probe_sets_a_server_deadline(
    mocker: MockerFixture,
) -> None:
    """Set the PostgreSQL deadline before executing the readiness query.

    Replaces the disposable psycopg connection while exercising the production probe, proving its
    server-side timeout complements the outer client deadline.

    Arguments:
        mocker: Fixture replacing the psycopg connection factory.

    Returns:
        None.

    Raises:
        AssertionError: If the probe omits or reorders its statements.
    """
    connection = AsyncMock()
    cursor_context = AsyncMock()
    cursor = AsyncMock()
    connection.cursor = Mock(return_value=cursor_context)
    cursor_context.__aenter__.return_value = cursor
    cursor.fetchone.return_value = (1,)

    result = await _execute_database_probe(connection)

    assert cursor.execute.await_args_list == [
        mocker.call(
            "SELECT set_config('statement_timeout', %s, true)",
            [str(HEALTH_CHECK_STATEMENT_TIMEOUT_MILLISECONDS)],
        ),
        mocker.call("SELECT 1"),
    ]
    assert result == (1,)


@pytest.mark.unit
def test_database_health_check_applies_a_client_deadline(
    mocker: MockerFixture,
) -> None:
    """Bound the complete database probe and accept its expected result.

    Replaces the asynchronous probe while retaining the production deadline wrapper, proving
    Django-derived parameters reach a cancellable disposable connection.

    Arguments:
        mocker: Fixture replacing database transaction and connection collaborators.

    Returns:
        None.

    Raises:
        AssertionError: If the deadline does not wrap the disposable probe.
    """
    connections = mocker.patch("config.health.connections")
    connection = connections.__getitem__.return_value
    connection.pool.get_stats.return_value = {
        "pool_available": 1,
        "pool_size": 2,
        "pool_max": 8,
    }
    connection_parameters = {"dbname": "localforge"}
    connection.get_connection_params.return_value = connection_parameters
    probe = mocker.patch(
        "config.health._execute_database_probe_with_deadline",
        autospec=True,
        return_value=(1,),
    )

    run_timed_database_check(SimpleNamespace(alias="default"))

    connection.get_connection_params.assert_called_once_with()
    probe.assert_awaited_once_with(connection_parameters)


@pytest.mark.unit
def test_database_health_check_requires_the_configured_pool() -> None:
    """Reject a database backend without the required connection pool.

    Preserves the platform invariant that readiness covers the same bounded checkout path used by
    ORM requests rather than silently degrading to the independent transport probe.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a backend without pooling is accepted.
    """
    with pytest.raises(ServiceUnavailable, match="configured connection pool"):
        _validate_database_pool(SimpleNamespace(pool=None))


@pytest.mark.unit
def test_database_pool_capacity_check_performs_no_transport_io(
    mocker: MockerFixture,
) -> None:
    """Inspect configured pool capacity without checking out a connection.

    Supplies a pool whose checkout would stall, proving readiness leaves transport validation to
    the separately deadline-bound disposable psycopg probe.

    Arguments:
        mocker: Fixture constructing the configured pool collaborator.

    Returns:
        None.

    Raises:
        AssertionError: If capacity validation invokes the pool health callback.
    """
    pool = mocker.Mock()
    pool.max_size = 8
    pool.get_stats.return_value = {
        "pool_available": 1,
        "pool_size": 2,
        "pool_max": 8,
    }

    _validate_database_pool(SimpleNamespace(pool=pool))

    pool.getconn.assert_not_called()
    pool.putconn.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["database", "result"])
def test_database_health_check_reports_bounded_failures(
    mocker: MockerFixture,
    failure: str,
) -> None:
    """Convert database and result failures into safe health responses.

    Exercises both failure exits without retaining driver diagnostics, ensuring the endpoint reports
    dependency unavailability rather than raising or exposing database values.

    Arguments:
        mocker: Fixture replacing database transaction and connection collaborators.
        failure: Failure path to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If either failure does not become service unavailability.
    """
    connections = mocker.patch("config.health.connections")
    connection = connections.__getitem__.return_value
    connection.pool.get_stats.return_value = {
        "pool_available": 1,
        "pool_size": 2,
        "pool_max": 8,
    }
    connection_parameters = {"dbname": "localforge"}
    connection.get_connection_params.return_value = connection_parameters

    if failure == "database":
        mocker.patch(
            "config.health._execute_database_probe_with_deadline",
            autospec=True,
            side_effect=PsycopgError("driver diagnostic"),
        )
    else:
        mocker.patch(
            "config.health._execute_database_probe_with_deadline",
            autospec=True,
            return_value=(0,),
        )

    with pytest.raises(ServiceUnavailable, match="Database health check"):
        run_timed_database_check(SimpleNamespace(alias="default"))

    connection.get_connection_params.assert_called_once_with()


@pytest.mark.unit
def test_database_deadline_releases_executor_capacity(
    mocker: MockerFixture,
) -> None:
    """Release bounded executor capacity after a stalled database probe.

    Makes the cancellable probe exceed a short client deadline, then submits another job through
    the same one-worker executor to prove timeout cleanup returned its admission slot.

    Arguments:
        mocker: Fixture replacing the database probe and deadline.

    Returns:
        None.

    Raises:
        AssertionError: If timeout leaves the worker or admission slot occupied.
    """
    cancelled = threading.Event()
    connections = mocker.patch("config.health.connections")
    connection = connections.__getitem__.return_value
    connection.pool.get_stats.return_value = {
        "pool_available": 1,
        "pool_size": 2,
        "pool_max": 8,
    }
    connection.get_connection_params.return_value = {"dbname": "localforge"}
    database_connection = AsyncMock()
    connect = mocker.patch(
        "config.health.AsyncConnection.connect",
        new_callable=AsyncMock,
        return_value=database_connection,
    )

    async def stall_probe(_parameters: dict[str, object]) -> tuple[object, ...] | None:
        """Wait until the client deadline cancels the probe.

        Records coroutine cleanup so the test distinguishes active cancellation from a detached
        operation that continues holding resources.

        Arguments:
            _parameters: Unused connection parameters.

        Returns:
            Never returns normally.

        Raises:
            asyncio.CancelledError: When the client deadline expires.
        """
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

        return None

    mocker.patch("config.health._execute_database_probe", side_effect=stall_probe)
    mocker.patch(
        "config.health.HEALTH_CHECK_CLIENT_TIMEOUT_SECONDS",
        PROBE_TIMEOUT_SECONDS,
    )

    with ContextPreservingThreadPoolExecutor(max_workers=1) as executor:
        stalled = executor.submit(
            run_timed_database_check,
            SimpleNamespace(alias="default"),
        )

        with pytest.raises(ServiceUnavailable, match="Database health check"):
            stalled.result(timeout=1)

        assert cancelled.wait(MAXIMUM_CANCELLATION_SECONDS)
        assert executor.submit(lambda: "accepted").result(timeout=1) == "accepted"

    connect.assert_awaited_once_with(dbname="localforge")
    assert database_connection.close.await_count >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelling_a_health_request_does_not_block_the_event_loop() -> None:
    """Leave the event loop responsive while abandoned synchronous work finishes.

    Cancels a coroutine waiting on a blocking executor job and measures cancellation latency,
    proving request cleanup does not synchronously shut down and join the shared worker pool.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If cancellation waits for the blocking worker.
    """
    release = threading.Event()
    loop = asyncio.get_running_loop()

    async def wait_for_dependency() -> None:
        """Wait for one simulated synchronous dependency check.

        Submits a bounded blocking wait through the exact context manager the health view uses,
        keeping the cancellation behavior representative of the configured endpoint.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: If the simulated request is cancelled.
        """
        with context_preserving_executor(object()) as executor:
            await loop.run_in_executor(executor, release.wait, BLOCKING_CHECK_SECONDS)

    task = asyncio.create_task(wait_for_dependency())
    await asyncio.sleep(CANCELLATION_SETTLE_SECONDS)
    started = loop.time()
    task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()

    assert loop.time() - started < MAXIMUM_CANCELLATION_SECONDS
