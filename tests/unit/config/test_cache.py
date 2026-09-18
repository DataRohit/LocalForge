"""Unit tests for the cache that survives losing its instance.

Covers every operation the application uses when the client refuses, substituting the client rather
than the framework's methods so the real backend code path runs above a genuine client error.
"""

import logging
from typing import Any, cast

import pytest
from django.conf import settings
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from config.cache import ResilientRedisCache

UNREACHABLE = "redis://127.0.0.1:1/0"
MULTI_KEY_COUNT = 2


class RefusingClient:
    """Cache client that refuses every operation.

    Inherits nothing; the framework's cache reaches its client through one attribute, so anything
    providing these methods stands in for it. Every call raises the error the real client raises
    when it cannot reach the instance.

    Attributes:
        None.

    Members:
        refuse: Raise the client library's connection error.
    """

    def refuse(self, *_args: Any, **_keywords: Any) -> Any:  # noqa: ANN401
        """Raise the client library's connection error.

        Stands in for every client operation, so each framework method above it fails exactly where
        a real outage makes it fail rather than where a test chose to intervene.

        Arguments:
            *_args: Positional arguments the caller supplied.
            **_keywords: Keyword arguments the caller supplied.

        Returns:
            Never returns.

        Raises:
            RedisConnectionError: Always.
        """
        message = "probe: instance unreachable"
        raise RedisConnectionError(message)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Refuse any operation the framework reaches for.

        Answers every attribute lookup with the refusing call, so a framework method this test does
        not know about still fails as an outage rather than as a missing attribute.

        Arguments:
            name: Operation the framework asked for.

        Returns:
            The refusing call.

        Raises:
            None.
        """
        del name

        return self.refuse


class ScriptClient:
    """Capture one atomic script invocation and return a prepared result.

    Inherits nothing and exposes only the redis-py eval surface used by the cache adapter, retaining
    the complete argument sequence for assertions.

    Attributes:
        result: Two-item script result returned to the adapter.
        arguments: Last positional eval arguments observed.

    Members:
        eval: Record and answer one script execution.
    """

    def __init__(self, result: list[int]) -> None:
        """Initialize one prepared script result.

        Stores the result the raw client will return and starts with no observed eval invocation,
        allowing each test to inspect exactly one adapter call.

        Arguments:
            result: Admission flag and retry delay returned by eval.

        Returns:
            None.
        """
        self.result = result
        self.arguments: tuple[object, ...] = ()

    def eval(self, *arguments: object) -> list[int]:
        """Record and return one script result.

        Captures the script and every key or policy argument without interpreting them, then returns
        the prepared Valkey-shaped two-item response.

        Arguments:
            *arguments: Script, key count, prepared keys, and policy arguments.

        Returns:
            Prepared two-item script result.
        """
        self.arguments = arguments

        return self.result


class ScriptBackend:
    """Expose one prepared script client through Django's cache-client seam.

    Inherits nothing and records the key and write intent supplied while selecting the raw client,
    isolating Django's server-selection boundary from script behavior.

    Attributes:
        client: Prepared eval-capable client.
        selection: Last key and write intent observed.

    Members:
        get_client: Return the prepared client.
    """

    def __init__(self, client: ScriptClient) -> None:
        """Initialize one raw client selection.

        Retains the eval-capable client and starts without an observed server-selection call so the
        test can distinguish selection from execution.

        Arguments:
            client: Eval-capable client returned for every selection.

        Returns:
            None.
        """
        self.client = client
        self.selection: tuple[str | None, bool] | None = None

    def get_client(self, key: str | None = None, *, write: bool = False) -> ScriptClient:
        """Return the prepared script client and capture routing intent.

        Records the first prepared key and write flag exactly as Django's adapter supplies them,
        then returns the client used for the atomic eval.

        Arguments:
            key: Prepared first key used for server selection.
            write: Whether the caller requests a writable connection.

        Returns:
            Prepared script client.
        """
        self.selection = (key, write)

        return self.client


@pytest.fixture
def refusing_cache() -> ResilientRedisCache:
    """Build a cache whose client refuses everything.

    Substitutes the client the framework's cache reaches through, so each framework method runs for
    real and fails where a real outage makes it fail.

    Arguments:
        None.

    Returns:
        A cache whose every operation reaches a refusing client.

    Raises:
        None.
    """
    cache = ResilientRedisCache(UNREACHABLE, {})
    cache.__dict__["_cache"] = RefusingClient()

    return cache


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_result", "expected"),
    [
        ([1, 0], (True, 0)),
        ([0, 7], (False, 7)),
    ],
)
def test_atomic_fixed_window_admission_uses_one_multi_key_script(
    raw_result: list[int],
    expected: tuple[bool, int],
) -> None:
    """Execute one fixed-window decision for every supplied dimension.

    Substitutes only the raw Redis client and verifies the adapter prepares both namespaced keys,
    requests a writable connection, and supplies the coordinated time in one eval invocation.

    Arguments:
        raw_result: Script result returned by the prepared client.
        expected: Parsed admission and retry result.

    Returns:
        None.

    Raises:
        AssertionError: If dimensions are split across calls or policy arguments drift.
    """
    client = ScriptClient(raw_result)
    backend = ScriptBackend(client)
    cache = ResilientRedisCache(UNREACHABLE, {"KEY_PREFIX": "unit"})
    cache.__dict__["_cache"] = backend

    observed = cache.atomic_fixed_window_admit(
        ("scope:account", "scope:composite"),
        limit=3,
        window_seconds=60,
        now_milliseconds=1_800_000_000_250,
    )

    assert observed == expected
    assert backend.selection is not None
    selected_key, write = backend.selection
    assert selected_key is not None
    assert "scope:account" in selected_key
    assert write is True
    assert client.arguments[1] == MULTI_KEY_COUNT
    assert "scope:account" in str(client.arguments[2])
    assert "scope:composite" in str(client.arguments[3])
    assert client.arguments[-3:] == (3, 60_000, 1_800_000_000_250)


@pytest.mark.unit
def test_atomic_fixed_window_admission_uses_server_time_in_production() -> None:
    """Leave production fixed-window time to Valkey.

    Calls the adapter without a test override and verifies the empty final script argument selects
    the server TIME branch rather than a process wall clock.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If production passes a process-derived timestamp.
    """
    client = ScriptClient([1, 0])
    cache = ResilientRedisCache(UNREACHABLE, {})
    cache.__dict__["_cache"] = ScriptBackend(client)

    assert cache.atomic_fixed_window_admit(
        ("scope:address",),
        limit=2,
        window_seconds=1,
    ) == (True, 0)
    assert client.arguments[-1] == ""


@pytest.mark.unit
def test_atomic_fixed_window_admission_fails_open_when_cache_refuses(
    refusing_cache: ResilientRedisCache,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return no decision when Valkey is unavailable.

    Exercises the raw-script outage path and verifies callers receive the sentinel required by the
    documented general-throttle fail-open policy while the outage remains visible in logs.

    Arguments:
        refusing_cache: Cache whose client refuses every operation.
        caplog: Log capture fixture supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the client error escapes or goes unlogged.
    """
    with caplog.at_level(logging.WARNING):
        observed = refusing_cache.atomic_fixed_window_admit(
            ("scope:address",),
            limit=1,
            window_seconds=60,
        )

    assert observed is None
    assert "unavailable" in caplog.text


@pytest.mark.unit
def test_the_guard_catches_the_errors_the_client_raises() -> None:
    """Catch what the client library actually raises.

    Confirms the connection and timeout errors both sit under the class the guard catches, so the
    protection cannot be quietly defeated by the library reorganising its exceptions.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either error falls outside the caught hierarchy.
    """
    assert issubclass(RedisConnectionError, RedisError)
    assert issubclass(RedisTimeoutError, RedisError)


@pytest.mark.unit
def test_a_read_from_an_unreachable_cache_is_a_miss(
    refusing_cache: ResilientRedisCache,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Serve the default when the cache is gone.

    Confirms a refused read returns the caller's default rather than raising, which is what turns a
    cache outage into a slower page instead of a server error, and that the outage is logged.

    Arguments:
        refusing_cache: Cache whose client refuses every operation.
        caplog: Log capture fixture supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the read raises or the outage goes unlogged.
    """
    with caplog.at_level(logging.WARNING):
        assert refusing_cache.get("probe", "fallback") == "fallback"

    assert "unavailable" in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "arguments", "expected"),
    [
        ("get", ("probe",), None),
        ("get_many", (["probe"],), {}),
        ("set", ("probe", "value"), None),
        ("set_many", ({"probe": "value"},), ["probe"]),
        ("add", ("probe", "value"), False),
        ("touch", ("probe",), False),
        ("delete", ("probe",), False),
        ("delete_many", (["probe"],), None),
        ("has_key", ("probe",), False),
    ],
)
def test_every_operation_degrades_rather_than_raising(
    refusing_cache: ResilientRedisCache,
    method: str,
    arguments: tuple[Any, ...],
    expected: Any,  # noqa: ANN401
) -> None:
    """Survive an outage on every path the application uses.

    Confirms each operation answers with its empty result when the client refuses, because one
    unguarded call is enough to turn an outage back into a server error.

    Arguments:
        refusing_cache: Cache whose client refuses every operation.
        method: Cache operation under test.
        arguments: Arguments to call it with.
        expected: The answer the degraded path must give.

    Returns:
        None.

    Raises:
        AssertionError: If the operation raises or answers differently.
    """
    assert getattr(refusing_cache, method)(*arguments) == expected


@pytest.mark.unit
def test_a_counter_on_an_unreachable_cache_reads_as_absent(
    refusing_cache: ResilientRedisCache,
) -> None:
    """Treat an unreadable counter as one that was never set.

    Confirms the counter path raises the absent-key error the base cache raises, so a caller that
    already handles a missing counter needs no separate branch for an outage.

    Arguments:
        refusing_cache: Cache whose client refuses every operation.

    Returns:
        None.

    Raises:
        AssertionError: If the client error escapes instead.
    """
    with pytest.raises(ValueError, match="not found"):
        refusing_cache.incr("probe")


@pytest.mark.unit
def test_clearing_an_unreachable_cache_still_reports_failure(
    refusing_cache: ResilientRedisCache,
) -> None:
    """Tell an operator a clear did not happen.

    Confirms the administrative path is left to raise, because an operator clearing a cache needs
    to know the instance never received the instruction.

    Arguments:
        refusing_cache: Cache whose client refuses every operation.

    Returns:
        None.

    Raises:
        AssertionError: If the failure is swallowed.
    """
    with pytest.raises(RedisError):
        refusing_cache.clear()


@pytest.mark.unit
def test_sessions_do_not_use_the_degrading_cache() -> None:
    """Keep session writes loud.

    Confirms sessions are pointed at a separate strict alias, because a silently discarded session
    write is a lost identity, and the framework's session creation treats a refused write as a key
    collision it retries thousands of times.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If sessions share the degrading alias.
    """
    assert settings.SESSION_CACHE_ALIAS == "sessions"
    assert (
        str(cast("dict[str, Any]", settings.CACHES["sessions"])["BACKEND"])
        == "django.core.cache.backends.redis.RedisCache"
    )
    assert (
        str(cast("dict[str, Any]", settings.CACHES["default"])["BACKEND"])
        == "config.cache.ResilientRedisCache"
    )
