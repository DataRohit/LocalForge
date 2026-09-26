"""Unit tests for health-check request correlation.

Verifies executor work inherits request-local state and the health view selects that executor, so
dependency logs can be correlated with the response that triggered them.
"""

import asyncio
import secrets
import smtplib
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from health_check.exceptions import ServiceUnavailable
from psycopg import Error as PsycopgError

import config.health as health_module
from config.health import (
    HEALTH_CHECK_STATEMENT_TIMEOUT_MILLISECONDS,
    BrokerReadinessCheck,
    ContextPreservingThreadPoolExecutor,
    HealthCheckCapacityError,
    MailReadinessCheck,
    ObjectStorageReadinessCheck,
    ReadinessView,
    RedisReadinessCheck,
    _execute_database_probe,
    _run_readiness_check,
    _validate_database_pool,
    context_preserving_executor,
    readiness_get,
    run_timed_database_check,
)
from config.logs import NO_REQUEST_ID, request_identifier

if TYPE_CHECKING:
    from concurrent.futures import Future

    from pytest_mock import MockerFixture

BLOCKING_CHECK_SECONDS = 0.5
CANCELLATION_SETTLE_SECONDS = 0.05
MAXIMUM_CANCELLATION_SECONDS = 0.2
REFRESHED_MAIL_READINESS_TIME = 162.0
PROBE_TIMEOUT_SECONDS = 0.01
READINESS_SETTLE_TURNS = 100


class SynchronousReadinessProbe:
    """Provide one successful synchronous readiness operation.

    Supplies the concrete protocol shape needed to test executor admission without introducing a
    dependency transport or an untyped dynamic object.

    Attributes:
        None.

    Members:
        run: Complete one synchronous readiness operation.
    """

    def run(self) -> None:
        """Complete one synchronous readiness operation.

        Performs no work because executor admission, rather than dependency behavior, is the
        subject of the test that uses this probe.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """


class BlockingAsyncReadinessProbe:
    """Hold one asynchronous readiness operation until released.

    Inherits nothing and records startup and completion so exceptional collection can prove whether
    sibling work finished before a programming defect propagated.

    Attributes:
        started: Event set when the probe begins.
        release: Event controlling completion.
        completed: Whether the probe returned normally.

    Members:
        run: Wait for release and mark completion.
    """

    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        """Bind synchronization events to one probe.

        Stores caller-owned events without starting asynchronous work.
        Each test receives independent completion state.

        Arguments:
            started: Event set on probe entry.
            release: Event allowing the probe to finish.

        Returns:
            None.
        """
        self.started = started
        self.release = release
        self.completed = False

    async def run(self) -> None:
        """Wait until the test releases the probe.

        Marks completion only after the release event, providing an exact observation of sibling
        lifetime when another probe raises.

        Arguments:
            None.

        Returns:
            None.
        """
        self.started.set()
        await self.release.wait()
        for _turn in range(READINESS_SETTLE_TURNS):
            await asyncio.sleep(0)
        self.completed = True


class FailingAsyncReadinessProbe:
    """Raise one programming defect after its sibling starts.

    Inherits nothing and coordinates the exceptional path so collection order is deterministic
    rather than scheduler-dependent.

    Attributes:
        sibling_started: Event proving concurrent sibling execution began.
        message: Programming-defect text raised by the probe.

    Members:
        run: Raise after sibling startup.
    """

    def __init__(
        self,
        sibling_started: asyncio.Event,
        sibling_release: asyncio.Event,
        message: str,
    ) -> None:
        """Store the sibling-start event.

        Retains only the synchronization boundary needed for deterministic failure ordering.
        The probe carries no dependency state.

        Arguments:
            sibling_started: Event set by the blocking probe.
            sibling_release: Event allowing the sibling to settle.
            message: Programming-defect text to raise.

        Returns:
            None.
        """
        self.sibling_started = sibling_started
        self.sibling_release = sibling_release
        self.message = message

    async def run(self) -> None:
        """Raise after concurrent sibling startup.

        Waits for the sibling so the failure always occurs with unfinished readiness work active.
        The exception represents an unexpected programming defect rather than dependency loss.

        Arguments:
            None.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always after sibling startup.
        """
        await self.sibling_started.wait()
        self.sibling_release.set()
        raise RuntimeError(self.message)


@pytest.mark.unit
def test_readiness_view_finishes_siblings_before_propagating_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finish concurrent probes before exposing one unexpected exception.

    Reproduces the suite-worker shutdown race without real services and requires collection to own
    every sibling through completion before the programming defect escapes.

    Arguments:
        monkeypatch: Fixture replacing the readiness inventory.

    Returns:
        None.

    Raises:
        AssertionError: If the defect propagates while sibling work remains active.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    blocking = BlockingAsyncReadinessProbe(started, release)
    failing = FailingAsyncReadinessProbe(
        started,
        release,
        "readiness programming defect",
    )
    monkeypatch.setattr(
        "config.health._readiness_checks",
        lambda: (("blocking", blocking), ("failing", failing)),
    )
    request = SimpleNamespace(
        user=SimpleNamespace(is_authenticated=False, is_staff=False),
    )
    with pytest.raises(RuntimeError, match="readiness programming defect"):
        readiness_get(object(), request)
    completed_when_raised = blocking.completed

    assert completed_when_raised is True


@pytest.mark.unit
def test_readiness_view_raises_first_unexpected_failure_in_inventory_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve deterministic exception order after every probe completes.

    Supplies two concurrent programming defects and requires the first registered check to govern
    the propagated failure.

    Arguments:
        monkeypatch: Fixture replacing the readiness inventory.

    Returns:
        None.

    Raises:
        AssertionError: If completion order replaces inventory order.
    """
    started = asyncio.Event()
    started.set()
    release = asyncio.Event()
    first = FailingAsyncReadinessProbe(started, release, "first readiness defect")
    second = FailingAsyncReadinessProbe(started, release, "second readiness defect")
    monkeypatch.setattr(
        "config.health._readiness_checks",
        lambda: (("first", first), ("second", second)),
    )
    request = SimpleNamespace(
        user=SimpleNamespace(is_authenticated=False, is_staff=False),
    )

    with pytest.raises(RuntimeError, match="first readiness defect"):
        readiness_get(object(), request)


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
def test_health_view_is_public_and_json_only() -> None:
    """Expose readiness publicly through one machine-readable renderer.

    Confirms load balancers need no authentication while browser-oriented renderers remain absent
    and optional staff authentication can reveal only the view's bounded detail.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If authentication, permission, or renderer policy changes.
    """
    view_attributes = vars(ReadinessView)

    assert view_attributes["authentication_classes"][0].__name__ == "SessionAuthentication"
    assert view_attributes["permission_classes"][0].__name__ == "AllowAny"
    assert view_attributes["renderer_classes"][0].__name__ == "JSONRenderer"


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
async def test_readiness_reports_saturated_executor_capacity() -> None:
    """Convert saturated synchronous probe capacity into unavailable state.

    Occupies the only worker before running another synchronous check, proving overload degrades
    readiness rather than escaping as an application error or entering an unbounded queue.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If capacity exhaustion is not represented as dependency unavailability.
    """
    release = threading.Event()

    with ContextPreservingThreadPoolExecutor(max_workers=1) as executor:
        occupied = executor.submit(release.wait, BLOCKING_CHECK_SECONDS)
        result = await _run_readiness_check(
            SynchronousReadinessProbe(),
            executor,
        )
        release.set()
        occupied.result()

    assert isinstance(result.error, ServiceUnavailable)
    assert isinstance(result.error.__cause__, HealthCheckCapacityError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_valkey_readiness_rejects_an_unsuccessful_ping(
    mocker: MockerFixture,
) -> None:
    """Reject a Valkey client that returns a false ping result.

    Replaces transport work while exercising the production result validation and deterministic
    client cleanup path.

    Arguments:
        mocker: Fixture replacing the Redis client factory.

    Returns:
        None.

    Raises:
        AssertionError: If a false ping is accepted or the client remains open.
    """
    client = AsyncMock()
    client.ping.return_value = False
    client.aclose.side_effect = TimeoutError
    client_factory = mocker.patch("config.health.Redis.from_url", return_value=client)
    check = RedisReadinessCheck(location="redis://cache.invalid:6379/0", password=None)

    with pytest.raises(ServiceUnavailable, match="unexpected result"):
        await check.run()

    client_factory.assert_called_once()
    client.aclose.assert_awaited_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_broker_readiness_contains_transport_failure(
    mocker: MockerFixture,
) -> None:
    """Convert broker transport failure into service unavailability.

    Replaces the dynamic AMQP connector with a refused transport, proving raw broker diagnostics do
    not escape the readiness boundary.

    Arguments:
        mocker: Fixture replacing the AMQP module import.

    Returns:
        None.

    Raises:
        AssertionError: If transport failure is not converted.
    """
    connect = AsyncMock(side_effect=OSError("broker unavailable"))
    mocker.patch(
        "config.health.import_module",
        return_value=SimpleNamespace(connect=connect),
    )
    check = BrokerReadinessCheck(url="******broker.invalid:5672/vhost")

    with pytest.raises(ServiceUnavailable, match="Broker readiness check"):
        await check.run()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_broker_readiness_contains_cleanup_failure(
    mocker: MockerFixture,
) -> None:
    """Convert broker cleanup failure into service unavailability.

    Completes the plain AMQP handshake before close fails, proving connection cleanup cannot escape
    the readiness boundary or leave a reconnecting robust client behind.

    Arguments:
        mocker: Fixture replacing the AMQP module import.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup failure is not converted.
    """
    connection = AsyncMock()
    connection.close.side_effect = OSError("broker cleanup unavailable")
    connect = AsyncMock(return_value=connection)
    mocker.patch(
        "config.health.import_module",
        return_value=SimpleNamespace(connect=connect),
    )
    check = BrokerReadinessCheck(url="******broker.invalid:5672/vhost")

    with pytest.raises(ServiceUnavailable, match="Broker readiness cleanup"):
        await check.run()


@pytest.mark.unit
def test_object_storage_readiness_contains_transport_failure(
    mocker: MockerFixture,
) -> None:
    """Convert object-storage transport failure into service unavailability.

    Uses a mocked metadata client so the unit layer exercises error containment and deterministic
    client closure without network access.

    Arguments:
        mocker: Fixture replacing the S3 client factory.

    Returns:
        None.

    Raises:
        AssertionError: If transport failure escapes or the client remains open.
    """
    client = Mock()
    client.head_bucket.side_effect = OSError("storage unavailable")
    mocker.patch("config.health.boto3.client", return_value=client)
    check = ObjectStorageReadinessCheck(
        endpoint_url="http://storage.invalid",
        access_key=secrets.token_hex(8),
        secret_key=secrets.token_hex(8),
        bucket_name="media",
        region_name="us-east-1",
    )

    with pytest.raises(ServiceUnavailable, match="Object-storage readiness check"):
        check.run()

    client.close.assert_called_once_with()


@pytest.mark.unit
def test_mail_readiness_contains_transport_failure(
    mocker: MockerFixture,
) -> None:
    """Convert mail transport failure into service unavailability.

    Replaces the backend connection with an SMTP refusal, proving no diagnostic escapes and the
    connection is still closed after failure.

    Arguments:
        mocker: Fixture replacing the Django mail connection factory.

    Returns:
        None.

    Raises:
        AssertionError: If SMTP failure escapes or the connection remains open.
    """
    connections = (Mock(), Mock())
    for connection in connections:
        connection.open.side_effect = smtplib.SMTPException("mail unavailable")
        connection.close.side_effect = smtplib.SMTPException("mail cleanup unavailable")
    mocker.patch.object(health_module.mail_readiness_state, "success_at", None)
    get_connection = mocker.patch("config.health.get_connection", side_effect=connections)
    check = MailReadinessCheck(backend="django.core.mail.backends.smtp.EmailBackend")

    with pytest.raises(ServiceUnavailable, match="Mail readiness check"):
        check.run()

    assert get_connection.call_count == health_module.HEALTH_CHECK_MAIL_ATTEMPTS
    for connection in connections:
        connection.close.assert_called_once_with()


@pytest.mark.unit
def test_mail_readiness_retries_one_transient_transport_failure(
    mocker: MockerFixture,
) -> None:
    """Recover from one transient SMTP connection failure.

    Fails the first connection and accepts the second, proving a brief external relay error does not
    remove an otherwise healthy application from the load balancer.

    Arguments:
        mocker: Fixture replacing the Django mail connection factory.

    Returns:
        None.

    Raises:
        AssertionError: If the transient failure escapes or either connection remains open.
    """
    failed = Mock()
    failed.open.side_effect = smtplib.SMTPException("mail unavailable")
    recovered = Mock()
    recovered.open.return_value = True
    mocker.patch.object(health_module.mail_readiness_state, "success_at", None)
    get_connection = mocker.patch(
        "config.health.get_connection",
        side_effect=(failed, recovered),
    )
    check = MailReadinessCheck(backend="django.core.mail.backends.smtp.EmailBackend")

    check.run()

    assert get_connection.call_count == health_module.HEALTH_CHECK_MAIL_ATTEMPTS
    failed.close.assert_called_once_with()
    recovered.close.assert_called_once_with()


@pytest.mark.unit
def test_mail_readiness_retries_one_transient_cleanup_failure(
    mocker: MockerFixture,
) -> None:
    """Recover from one transient SMTP cleanup failure.

    Opens both connections successfully while the first close fails, proving cleanup errors receive
    the same bounded retry as connection errors before readiness fails.

    Arguments:
        mocker: Fixture replacing the Django mail connection factory.

    Returns:
        None.

    Raises:
        AssertionError: If the cleanup failure escapes or the retry does not close.
    """
    failed = Mock()
    failed.open.return_value = True
    failed.close.side_effect = smtplib.SMTPException("mail cleanup unavailable")
    recovered = Mock()
    recovered.open.return_value = True
    mocker.patch.object(health_module.mail_readiness_state, "success_at", None)
    get_connection = mocker.patch(
        "config.health.get_connection",
        side_effect=(failed, recovered),
    )
    check = MailReadinessCheck(backend="django.core.mail.backends.smtp.EmailBackend")

    check.run()

    assert get_connection.call_count == health_module.HEALTH_CHECK_MAIL_ATTEMPTS
    failed.close.assert_called_once_with()
    recovered.close.assert_called_once_with()


@pytest.mark.unit
def test_mail_readiness_reuses_one_recent_success(
    mocker: MockerFixture,
) -> None:
    """Reuse a successful SMTP probe within its bounded lifetime.

    Keeps frequent Docker and Traefik polling from opening a new external SMTP connection while
    still requiring periodic live transport verification.

    Arguments:
        mocker: Fixture controlling the monotonic clock and mail factory.

    Returns:
        None.

    Raises:
        AssertionError: If a cached success still reaches SMTP.
    """
    mocker.patch.object(health_module.mail_readiness_state, "success_at", 100.0)
    mocker.patch("config.health.time.monotonic", return_value=120.0)
    get_connection = mocker.patch("config.health.get_connection")
    check = MailReadinessCheck(backend="django.core.mail.backends.smtp.EmailBackend")

    check.run()

    get_connection.assert_not_called()


@pytest.mark.unit
def test_mail_readiness_reuses_success_completed_before_lock_acquisition(
    mocker: MockerFixture,
) -> None:
    """Reuse SMTP evidence completed between the optimistic check and lock acquisition.

    Updates cached state while the lock is acquired and requires the waiting check to return without
    opening a duplicate connection.

    Arguments:
        mocker: Fixture controlling cached state, locking, time, and the mail factory.

    Returns:
        None.

    Raises:
        AssertionError: If the second cache check reaches SMTP or leaves the lock held.
    """
    lock = Mock()

    def acquire(*, blocking: bool) -> bool:
        """Record completed SMTP evidence while granting the lock.

        Simulates another probe finishing after the optimistic cache read but before this caller
        owns the lock.

        Arguments:
            blocking: Whether lock acquisition may wait.

        Returns:
            True after recording recent evidence.

        Raises:
            AssertionError: If production requests blocking acquisition.
        """
        assert not blocking
        health_module.mail_readiness_state.success_at = 90.0
        return True

    lock.acquire.side_effect = acquire
    mocker.patch.object(health_module.mail_readiness_state, "lock", lock)
    mocker.patch.object(health_module.mail_readiness_state, "success_at", None)
    mocker.patch("config.health.time.monotonic", return_value=100.0)
    get_connection = mocker.patch("config.health.get_connection")
    check = MailReadinessCheck(backend="django.core.mail.backends.smtp.EmailBackend")

    check.run()

    get_connection.assert_not_called()
    lock.release.assert_called_once_with()


@pytest.mark.unit
def test_mail_readiness_rejects_overlapping_uncached_probe(
    mocker: MockerFixture,
) -> None:
    """Reject an overlapping SMTP probe without retaining another executor worker.

    Holds the process-wide probe lock without a cached success and requires the second check to
    fail before opening a connection.

    Arguments:
        mocker: Fixture controlling cached state and the mail factory.

    Returns:
        None.

    Raises:
        AssertionError: If the overlapping check waits or reaches SMTP.
    """
    mocker.patch.object(health_module.mail_readiness_state, "success_at", None)
    get_connection = mocker.patch("config.health.get_connection")
    check = MailReadinessCheck(backend="django.core.mail.backends.smtp.EmailBackend")
    health_module.mail_readiness_state.lock.acquire()

    try:
        with pytest.raises(ServiceUnavailable, match="already running"):
            check.run()
    finally:
        health_module.mail_readiness_state.lock.release()

    get_connection.assert_not_called()


@pytest.mark.unit
def test_mail_readiness_refreshes_an_expired_success(
    mocker: MockerFixture,
) -> None:
    """Refresh SMTP readiness after the success lifetime expires.

    Advances the monotonic clock beyond the cache lifetime and requires a live connection before
    the new success timestamp is recorded.

    Arguments:
        mocker: Fixture controlling the monotonic clock and mail factory.

    Returns:
        None.

    Raises:
        AssertionError: If stale success bypasses SMTP or the refreshed time is not retained.
    """
    connection = Mock()
    connection.open.return_value = True
    mocker.patch.object(health_module.mail_readiness_state, "success_at", 100.0)
    mocker.patch(
        "config.health.time.monotonic",
        side_effect=(161.0, 161.0, REFRESHED_MAIL_READINESS_TIME),
    )
    get_connection = mocker.patch("config.health.get_connection", return_value=connection)
    check = MailReadinessCheck(backend="django.core.mail.backends.smtp.EmailBackend")

    check.run()

    get_connection.assert_called_once_with(
        check.backend,
        fail_silently=False,
        timeout=2.0,
    )
    connection.close.assert_called_once_with()
    assert health_module.mail_readiness_state.success_at == REFRESHED_MAIL_READINESS_TIME


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
