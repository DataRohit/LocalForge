"""Application readiness checks.

Checks every required dependency with bounded work and returns stable machine-readable readiness,
while preserving request correlation across synchronous probe workers.
"""

from __future__ import annotations

import asyncio
import inspect
import smtplib
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext, suppress
from contextvars import copy_context
from dataclasses import dataclass, field
from http import HTTPStatus
from importlib import import_module
from threading import BoundedSemaphore
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast, override

import boto3
from asgiref.sync import async_to_sync
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.mail import get_connection
from django.db import connections
from django.http import JsonResponse
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from health_check.checks import Database
from health_check.exceptions import ServiceUnavailable
from psycopg import AsyncConnection
from psycopg import Error as PsycopgError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from config.api import ErrorCode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from concurrent.futures import Executor
    from contextlib import AbstractContextManager
    from typing import Protocol

    from aio_pika.abc import AbstractConnection
    from django.http import HttpResponseBase
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

    class ReadinessCheck(Protocol):
        """Describe one synchronous or asynchronous dependency probe.

        Inherits from ``Protocol`` and defines only the callable operation the readiness coordinator
        needs. Untyped third-party checks and first-party probes share one typed execution path.

        Attributes:
            None.

        Members:
            run: Validate one dependency or raise service unavailability.
        """

        def run(self) -> object:
            """Validate one dependency.

            Accepts no arguments because each probe captures its own configuration.
            Returns an awaitable only when the probe performs asynchronous transport work.

            Arguments:
                None.

            Returns:
                Optional awaitable for asynchronous probes.

            Raises:
                ServiceUnavailable: If the dependency is unavailable.
            """
            ...

    class ReadinessUser(Protocol):
        """Describe the staff flags used for optional diagnostics.

        Inherits from ``Protocol`` and exposes only authentication and staff authorization state.
        Keeps the health view independent of the concrete custom user model.

        Attributes:
            is_authenticated: Whether the caller has an authenticated session.
            is_staff: Whether the caller may see bounded diagnostic detail.

        Members:
            None.
        """

        is_authenticated: bool
        is_staff: bool

    class ReadinessRequest(Protocol):
        """Describe the authenticated identity exposed by a health request.

        Inherits from ``Protocol`` and exposes only the user property consumed by readiness.
        Keeps the view callable independent of REST framework's untyped request implementation.

        Attributes:
            user: Caller identity resolved by session authentication.

        Members:
            None.
        """

        user: ReadinessUser


Arguments = ParamSpec("Arguments")
Result = TypeVar("Result")
HEALTH_CHECK_WORKERS = 5
HEALTH_CHECK_STATEMENT_TIMEOUT_MILLISECONDS = 5000
HEALTH_CHECK_CLIENT_TIMEOUT_SECONDS = 6.0
HEALTH_CHECK_NETWORK_TIMEOUT_SECONDS = 2.0
HEALTH_CHECK_STORAGE_CONNECT_TIMEOUT_SECONDS = 1
HEALTH_CHECK_STORAGE_READ_TIMEOUT_SECONDS = 2
AMQP_EXCEPTION = cast(
    "type[Exception]",
    import_module("aio_pika.exceptions").AMQPException,
)
APIView = import_module("rest_framework.views").APIView
SessionAuthentication = import_module("rest_framework.authentication").SessionAuthentication
AllowAny = import_module("rest_framework.permissions").AllowAny
JSONRenderer = import_module("rest_framework.renderers").JSONRenderer


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


@dataclass(repr=False)
class RedisReadinessCheck:
    """Check one configured Valkey-backed service.

    Inherits from ``HealthCheck`` and creates a short-lived Redis client with bounded connect and
    command timeouts, covering both cache and channel-layer instances without exposing addresses.

    Attributes:
        location: Redis URL without embedded credentials.
        password: Service credential excluded from representations.

    Members:
        run: Ping the configured service and close the client.
    """

    location: str = field(repr=False)
    password: str | None = field(repr=False)

    async def run(self) -> None:
        """Ping the configured Valkey service.

        Creates one client per readiness request so a failed or cancelled probe cannot retain
        connection state across later load-balancer polls.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            ServiceUnavailable: If connection, authentication, timeout, or ping fails.
        """
        client = Redis.from_url(
            self.location,
            password=self.password,
            socket_connect_timeout=HEALTH_CHECK_NETWORK_TIMEOUT_SECONDS,
            socket_timeout=HEALTH_CHECK_NETWORK_TIMEOUT_SECONDS,
        )
        failure: ServiceUnavailable | None = None
        try:
            async with asyncio.timeout(HEALTH_CHECK_NETWORK_TIMEOUT_SECONDS):
                if not await client.ping():
                    failure = ServiceUnavailable(
                        "Valkey readiness check returned an unexpected result"
                    )
        except (OSError, TimeoutError, RedisError) as error:
            failure = ServiceUnavailable("Valkey readiness check failed")
            failure.__cause__ = error
        try:
            await client.aclose()
        except (OSError, TimeoutError, RedisError) as error:
            if failure is None:
                failure = ServiceUnavailable("Valkey readiness cleanup failed")
                failure.__cause__ = error

        if failure is not None:
            raise failure


@dataclass(repr=False)
class BrokerReadinessCheck:
    """Check the configured RabbitMQ broker.

    Inherits from ``HealthCheck`` and opens one bounded AMQP connection before closing it, proving
    the application credential and virtual host are accepted without publishing a task.

    Attributes:
        url: Broker URL excluded from representations.

    Members:
        run: Open and close one broker connection.
    """

    url: str = field(repr=False)

    async def run(self) -> None:
        """Open and close a bounded broker connection.

        Uses the lightweight AMQP handshake rather than worker inspection, keeping frequent
        readiness polling independent of task execution and queue enumeration.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            ServiceUnavailable: If the broker cannot complete the handshake within its deadline.
        """
        connect = cast(
            "Callable[..., Awaitable[AbstractConnection]]",
            import_module("aio_pika").connect,
        )
        connection: AbstractConnection | None = None
        failure: ServiceUnavailable | None = None
        try:
            async with asyncio.timeout(HEALTH_CHECK_NETWORK_TIMEOUT_SECONDS):
                connection = await connect(
                    self.url,
                    timeout=HEALTH_CHECK_NETWORK_TIMEOUT_SECONDS,
                )
        except (AMQP_EXCEPTION, OSError, TimeoutError) as error:
            failure = ServiceUnavailable("Broker readiness check failed")
            failure.__cause__ = error

        if connection is not None:
            try:
                await connection.close()
            except (AMQP_EXCEPTION, OSError, TimeoutError) as error:
                failure = ServiceUnavailable("Broker readiness cleanup failed")
                failure.__cause__ = error

        if failure is not None:
            raise failure


@dataclass(repr=False)
class ObjectStorageReadinessCheck:
    """Check the configured object-storage bucket.

    Inherits from ``HealthCheck`` and performs a bounded metadata request, avoiding the write,
    read, and delete cycle that would make frequent load-balancer polling expensive.

    Attributes:
        endpoint_url: S3 endpoint excluded from representations.
        access_key: S3 access key excluded from representations.
        secret_key: S3 secret key excluded from representations.
        bucket_name: Bucket whose metadata is checked.
        region_name: S3 signing region.

    Members:
        run: Request bucket metadata and close the client.
    """

    endpoint_url: str = field(repr=False)
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    bucket_name: str
    region_name: str

    def run(self) -> None:
        """Request metadata for the configured bucket.

        Uses a health-specific client with one attempt and short transport timeouts so a stalled
        object-storage endpoint cannot retain a health worker beyond the polling budget.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            ServiceUnavailable: If the bucket cannot be reached or accessed.
        """
        client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region_name,
            config=Config(
                connect_timeout=HEALTH_CHECK_STORAGE_CONNECT_TIMEOUT_SECONDS,
                read_timeout=HEALTH_CHECK_STORAGE_READ_TIMEOUT_SECONDS,
                retries={"total_max_attempts": 1, "mode": "standard"},
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )
        try:
            client.head_bucket(Bucket=self.bucket_name)
        except (BotoCoreError, ClientError, OSError) as error:
            message = "Object-storage readiness check failed"
            raise ServiceUnavailable(message) from error
        finally:
            client.close()


@dataclass(repr=False)
class MailReadinessCheck:
    """Check the configured email backend.

    Inherits from ``HealthCheck`` and opens then closes the active backend with a short timeout,
    treating the testing environment's in-memory backend as its configured dependency.

    Attributes:
        backend: Configured Django email backend path.

    Members:
        run: Open and close the configured email backend.
    """

    backend: str = field(repr=False)

    def run(self) -> None:
        """Open and close the configured email backend.

        Performs no message delivery, keeping the check side-effect free while still validating
        SMTP connectivity in development and backend construction in testing.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            ServiceUnavailable: If the backend cannot open or close successfully.
        """
        connection = get_connection(
            self.backend,
            fail_silently=False,
            timeout=HEALTH_CHECK_NETWORK_TIMEOUT_SECONDS,
        )
        failure: ServiceUnavailable | None = None
        try:
            connection.open()
        except (OSError, smtplib.SMTPException, ValueError) as error:
            failure = ServiceUnavailable("Mail readiness check failed")
            failure.__cause__ = error
        try:
            connection.close()
        except (OSError, smtplib.SMTPException, ValueError) as error:
            if failure is None:
                failure = ServiceUnavailable("Mail readiness cleanup failed")
                failure.__cause__ = error

        if failure is not None:
            raise failure


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


@dataclass(frozen=True)
class ReadinessResult:
    """Represent one completed dependency probe.

    Stores only the bounded error category and elapsed time needed to build public state and
    authorised diagnostics, never the dependency's configuration or raw exception details.

    Attributes:
        error: Service unavailability, or ``None`` when the probe succeeded.
        time_taken: Probe duration in seconds.

    Members:
        None.
    """

    error: ServiceUnavailable | None
    time_taken: float


def _readiness_checks() -> tuple[tuple[str, ReadinessCheck], ...]:
    """Build the dependency checks from current settings.

    Reads configuration for every request so tests and operational overrides affect probes
    atomically without retaining clients, credentials, or stale endpoints between polls.

    Arguments:
        None.

    Returns:
        Stable names paired with checks for every required dependency.

    Raises:
        KeyError: If required service configuration is absent.
    """
    cache = cast("dict[str, object]", settings.CACHES["default"])
    cache_options = cast("dict[str, object]", cache.get("OPTIONS", {}))
    channel_layer = cast("dict[str, object]", settings.CHANNEL_LAYERS["default"])
    channel_configuration = cast("dict[str, object]", channel_layer["CONFIG"])
    channel_hosts = cast("list[dict[str, object]]", channel_configuration["hosts"])
    channel_host = channel_hosts[0]
    storage = cast("dict[str, object]", settings.STORAGES["default"])
    storage_options = cast("dict[str, object]", storage["OPTIONS"])
    database_check = cast(
        "Callable[..., ReadinessCheck]",
        TimedDatabaseHealthCheck,
    )

    return (
        (
            "database_primary",
            database_check(alias="default"),
        ),
        (
            "database_replica",
            database_check(alias="replica"),
        ),
        (
            "cache",
            RedisReadinessCheck(
                location=cast("str", cache["LOCATION"]),
                password=cast("str | None", cache_options.get("password")),
            ),
        ),
        (
            "channel_layer",
            RedisReadinessCheck(
                location=(
                    f"redis://{cast('str', channel_host['host'])}:"
                    f"{cast('int', channel_host['port'])}/0"
                ),
                password=cast("str", channel_host["password"]),
            ),
        ),
        (
            "broker",
            BrokerReadinessCheck(url=cast("str", settings.CELERY_BROKER_URL)),
        ),
        (
            "object_storage",
            ObjectStorageReadinessCheck(
                endpoint_url=cast("str", storage_options["endpoint_url"]),
                access_key=cast("str", storage_options["access_key"]),
                secret_key=cast("str", storage_options["secret_key"]),
                bucket_name=cast("str", storage_options["bucket_name"]),
                region_name=cast("str", storage_options["region_name"]),
            ),
        ),
        (
            "mail",
            MailReadinessCheck(backend=cast("str", settings.EMAIL_BACKEND)),
        ),
    )


async def _run_readiness_check(
    check: ReadinessCheck,
    executor: Executor | None,
) -> ReadinessResult:
    """Run one synchronous or asynchronous dependency probe.

    Dispatches synchronous checks to the bounded health executor, awaits asynchronous checks on the
    serving loop, and converts expected dependency or capacity failures into stable state.

    Arguments:
        check: Dependency probe to execute.
        executor: Bounded executor for synchronous probes.

    Returns:
        Completed result carrying only availability and duration.

    Raises:
        BaseException: Any programming defect outside expected dependency failures.
    """
    started = time.perf_counter()
    error: ServiceUnavailable | None = None
    run = check.run
    try:
        if inspect.iscoroutinefunction(run):
            await cast("Callable[[], Awaitable[None]]", run)()
        else:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(executor, cast("Callable[[], None]", run))
    except ServiceUnavailable as unavailable:
        error = unavailable
    except HealthCheckCapacityError as unavailable:
        error = ServiceUnavailable("Health-check execution capacity exhausted")
        error.__cause__ = unavailable

    return ReadinessResult(
        error=error,
        time_taken=time.perf_counter() - started,
    )


async def _collect_readiness() -> tuple[tuple[str, ReadinessResult], ...]:
    """Run every dependency check concurrently.

    Uses the bounded context-preserving executor for synchronous checks while asynchronous probes
    remain on the serving loop, minimizing total poll latency without creating unbounded work.

    Arguments:
        None.

    Returns:
        Stable dependency names paired with completed health-check results.

    Raises:
        None.
    """
    checks = _readiness_checks()
    with context_preserving_executor(None) as executor:
        results = await asyncio.gather(
            *(_run_readiness_check(check, executor) for _name, check in checks)
        )

    return tuple((name, result) for (name, _check), result in zip(checks, results, strict=True))


@extend_schema(
    operation_id="health_readiness",
    summary="Check application readiness",
    description=(
        "Reports process liveness and whether every required backing service can support traffic. "
        "Dependency diagnostics are available only to authenticated staff."
    ),
    responses={
        HTTPStatus.OK: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="Every required dependency is working.",
            examples=[
                OpenApiExample(
                    "Ready",
                    value={
                        "status": "ready",
                        "liveness": "alive",
                        "readiness": "ready",
                        "checks": {
                            "database_primary": "working",
                            "database_replica": "working",
                            "cache": "working",
                            "channel_layer": "working",
                            "broker": "working",
                            "object_storage": "working",
                            "mail": "working",
                        },
                    },
                    response_only=True,
                )
            ],
        ),
        HTTPStatus.SERVICE_UNAVAILABLE: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="At least one required dependency is unavailable.",
            examples=[
                OpenApiExample(
                    "Not ready",
                    value={
                        "status": "not_ready",
                        "liveness": "alive",
                        "readiness": "not_ready",
                        "checks": {
                            "database_primary": "working",
                            "database_replica": "working",
                            "cache": "unavailable",
                            "channel_layer": "working",
                            "broker": "working",
                            "object_storage": "working",
                            "mail": "working",
                        },
                    },
                    response_only=True,
                )
            ],
        ),
        HTTPStatus.METHOD_NOT_ALLOWED: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="The health endpoint accepts only safe read methods.",
            examples=[
                OpenApiExample(
                    "Method not allowed",
                    value={
                        "code": ErrorCode.METHOD_NOT_ALLOWED.value,
                        "message": "The requested method is not allowed.",
                        "details": {},
                        "request_id": "00000000-0000-4000-8000-000000000000",
                    },
                    response_only=True,
                )
            ],
        ),
        HTTPStatus.NOT_ACCEPTABLE: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="The requested representation is not available.",
            examples=[
                OpenApiExample(
                    "Not acceptable",
                    value={
                        "code": ErrorCode.NOT_ACCEPTABLE.value,
                        "message": "The requested response format is not available.",
                        "details": {},
                        "request_id": "00000000-0000-4000-8000-000000000000",
                    },
                    response_only=True,
                )
            ],
        ),
        HTTPStatus.INTERNAL_SERVER_ERROR: OpenApiResponse(
            response={"$ref": "#/components/schemas/ErrorEnvelope"},
            description="An unexpected readiness failure was contained and correlated.",
            examples=[
                OpenApiExample(
                    "Internal server error",
                    value={
                        "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                        "message": "An unexpected error occurred.",
                        "details": {},
                        "request_id": "00000000-0000-4000-8000-000000000000",
                    },
                    response_only=True,
                )
            ],
        ),
    },
)
def readiness_get(_view: object, request: ReadinessRequest) -> JsonResponse:
    """Run dependency checks and return aggregate readiness.

    Exposes only stable working states publicly and adds bounded durations plus generic failure
    categories for authenticated staff, never raw exceptions or infrastructure addresses.

    Arguments:
        _view: Dynamic API view instance dispatching the request.
        request: REST framework request carrying optional staff identity.

    Returns:
        JSON readiness response with status 200 or 503.

    Raises:
        None.
    """
    results = async_to_sync(_collect_readiness)()
    checks = {name: "unavailable" if result.error else "working" for name, result in results}
    ready = all(state == "working" for state in checks.values())
    state = "ready" if ready else "not_ready"
    payload: dict[str, object] = {
        "status": state,
        "liveness": "alive",
        "readiness": state,
        "checks": checks,
    }
    user = request.user

    if user.is_authenticated and user.is_staff:
        payload["details"] = {
            name: {
                "status": checks[name],
                "duration_ms": round(result.time_taken * 1000, 3),
                "error": "dependency_unavailable" if result.error else None,
            }
            for name, result in results
        }

    return JsonResponse(
        payload,
        status=HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
    )


ReadinessView = cast(
    "type[View[HttpResponseBase]]",
    type(
        "ReadinessView",
        (APIView,),
        {
            "__doc__": (
                "Report process liveness and dependency readiness.\n\n"
                "Inherits from DRF's APIView with JSON-only rendering, public access, optional "
                "staff detail, and explicit schema responses. Public access is required and "
                "load-balancer probes remain unthrottled so readiness polling cannot lock itself "
                "out."
            ),
            "authentication_classes": [SessionAuthentication],
            "permission_classes": [AllowAny],
            "renderer_classes": [JSONRenderer],
            "schema": AutoSchema(),
            "throttle_classes": [],
            "get": readiness_get,
        },
    ),
)
