"""Integration tests for the cache and its separation from task results.

Covers the round trip, expiry, the key prefix, and the proof that clearing a cache reaches only its
own logical database, because the framework's clear operation flushes an entire database.
"""

import os
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import pytest
import redis
from django.conf import settings
from django.core.cache import cache, caches
from django.test import override_settings

from config.cache import ResilientRedisCache
from config.settings.base import CACHE_KEY_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.test import Client


def _alias(name: str) -> dict[str, Any]:
    """Read one cache alias as a mapping.

    Narrows the settings value, which the type stubs describe only as an object, so the helpers
    below can read the address and the options out of it.

    Arguments:
        name: Cache alias to read.

    Returns:
        The alias configuration.

    Raises:
        None.
    """
    return cast("dict[str, Any]", settings.CACHES[name])


def _configured(key: str) -> str:
    """Read one cache setting as text.

    Narrows the settings value, which the type stubs describe only as an object, so each test can
    parse the address without repeating the same narrowing.

    Arguments:
        key: Setting name to read from the default cache.

    Returns:
        The configured value as text.

    Raises:
        None.
    """
    return str(_alias("default")[key])


def _options() -> dict[str, Any]:
    """Read the cache connection options.

    Narrows the options mapping so a test can build a second client with the same credentials and
    timeouts the application uses.

    Arguments:
        None.

    Returns:
        The configured connection options.

    Raises:
        None.
    """
    return dict(_alias("default")["OPTIONS"])


EXPIRY_SECONDS = 1
EXPIRY_MARGIN_SECONDS = 1.2
SCRATCH_DATABASE = 15
UNREACHABLE_LOCATION = "redis://127.0.0.1:1/0"


def _address() -> tuple[str, int, int]:
    """Read the cache instance's address and logical database.

    Splits the configured location once so each test names the instance the same way, rather than
    re-parsing a URL in several places.

    Arguments:
        None.

    Returns:
        The host, the port, and the logical database index.

    Raises:
        None.
    """
    parts = urlsplit(_configured("LOCATION"))

    return parts.hostname or "", parts.port or 0, int(parts.path.lstrip("/") or 0)


def _results_database() -> int:
    """Read the logical database Celery stores results in.

    Parses the address the environment gives Celery rather than a separate setting, so this cannot
    pass while the result backend is pointed at a database the cache would flush.

    Arguments:
        None.

    Returns:
        The logical database index the result backend uses.

    Raises:
        None.
    """
    return int(urlsplit(os.environ["CELERY_RESULT_BACKEND"]).path.lstrip("/") or 0)


def _client(database: int) -> redis.Redis:
    """Open a direct client on one logical database.

    Talks to the instance without the framework in between, so a test can see which database a key
    landed in and what survived a flush.

    Arguments:
        database: Logical database index to open.

    Returns:
        A client bound to that database.

    Raises:
        None.
    """
    host, port, _ = _address()

    return redis.Redis(
        host=host,
        port=port,
        password=_options()["password"],
        db=database,
    )


@pytest.fixture
def scratch_cache() -> Iterator[ResilientRedisCache]:
    """Provide a cache on a logical database nothing else uses.

    Gives the flush test a database of its own, because clearing the shared cache would flush every
    other parallel worker's entries and every logged-in session with them.

    Arguments:
        None.

    Yields:
        A cache bound to the scratch database.

    Raises:
        None.
    """
    host, port, _ = _address()
    scratch = ResilientRedisCache(
        f"redis://{host}:{port}/{SCRATCH_DATABASE}",
        {"OPTIONS": _options()},
    )

    yield scratch

    scratch.clear()


@pytest.mark.integration
@pytest.mark.services("valkey-cache")
def test_a_value_round_trips_through_the_cache(worker_namespace: str) -> None:
    """Store and read back one value.

    Confirms the configured instance answers a write and a read, which is the whole contract the
    application depends on and the one an unreachable instance would silently fail.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.

    Returns:
        None.

    Raises:
        AssertionError: If the value does not come back.
    """
    key = f"{worker_namespace}-round-trip"
    cache.set(key, {"value": 41})

    assert cache.get(key) == {"value": 41}

    cache.delete(key)


@pytest.mark.integration
@pytest.mark.services("valkey-cache")
def test_an_entry_expires(worker_namespace: str) -> None:
    """Let an entry go when its time is up.

    Confirms a timeout is honoured by the instance rather than by the application, because an entry
    that never expires is a leak the eviction policy would eventually have to clean up.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.

    Returns:
        None.

    Raises:
        AssertionError: If the entry survives its timeout.
    """
    key = f"{worker_namespace}-expiring"
    cache.set(key, "transient", EXPIRY_SECONDS)

    assert cache.get(key) == "transient"

    time.sleep(EXPIRY_MARGIN_SECONDS)

    assert cache.get(key) is None


@pytest.mark.integration
@pytest.mark.services("valkey-cache")
def test_every_key_carries_the_project_prefix(worker_namespace: str) -> None:
    """Make every entry attributable.

    Confirms the key the instance actually stores carries the project's prefix, so an operator
    reading the instance can tell this application's entries from anything else sharing it.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.

    Returns:
        None.

    Raises:
        AssertionError: If the stored key carries no prefix or is absent.
    """
    key = f"{worker_namespace}-prefixed"
    cache.set(key, "value")

    stored_name = cache.make_key(key)
    _, _, database = _address()

    assert stored_name.startswith(f"{CACHE_KEY_PREFIX}:")
    assert _client(database).exists(stored_name) == 1

    cache.delete(key)


@pytest.mark.integration
@pytest.mark.services("valkey-cache")
def test_clearing_a_cache_leaves_task_results_intact(
    worker_namespace: str,
    scratch_cache: ResilientRedisCache,
) -> None:
    """Keep task results out of a cache clear's reach.

    Confirms a clear flushes only its own logical database: the framework's clear issues a whole
    database flush, so sharing an index with the result backend would let a routine cache clear
    destroy every pending task result.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel workers from colliding.
        scratch_cache: Cache on a logical database reserved for this test.

    Returns:
        None.

    Raises:
        AssertionError: If the result key does not survive the clear.
    """
    results = _client(_results_database())
    result_key = f"celery-task-meta-{worker_namespace}"
    results.set(result_key, "pending")
    scratch_cache.set(f"{worker_namespace}-doomed", "value")

    scratch_cache.clear()

    assert scratch_cache.get(f"{worker_namespace}-doomed") is None
    assert results.get(result_key) == b"pending"

    results.delete(result_key)


@pytest.mark.integration
@pytest.mark.services("valkey-cache")
def test_the_cache_and_the_result_backend_use_different_databases() -> None:
    """Reserve a logical database for each purpose.

    Confirms the cache and the address Celery is actually given differ, which is the configuration
    the separation above depends on and the one a later ticket could quietly undo.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If both are configured on one logical database.
    """
    _, _, cache_database = _address()

    assert cache_database != _results_database()


@pytest.mark.integration
@pytest.mark.services("valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"])
def test_a_page_still_serves_when_the_cache_is_unreachable(client: Client) -> None:
    """Serve a page without the cache behind it.

    Confirms a request that reads through an unreachable cache returns an ordinary response, which
    is the difference between a cache outage costing latency and costing the whole page.

    Arguments:
        client: Test client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the request returns a server error.
    """
    unreachable = {
        "default": {
            "BACKEND": "config.cache.ResilientRedisCache",
            "LOCATION": UNREACHABLE_LOCATION,
            "KEY_PREFIX": CACHE_KEY_PREFIX,
        },
        "sessions": _alias("sessions"),
    }

    with override_settings(CACHES=unreachable):
        caches["default"].set("probe", "value")

        assert caches["default"].get("probe", "fallback") == "fallback"

        response = client.get("/health/")

    assert response.status_code != HTTPStatus.INTERNAL_SERVER_ERROR
