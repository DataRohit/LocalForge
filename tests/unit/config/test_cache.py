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
