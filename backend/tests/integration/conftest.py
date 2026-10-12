"""Shared fixtures and guards for the integration layer.

Requires every integration test to name the services it needs, so a failure names the service that
was unavailable rather than surfacing as an unexplained connection error. Also keeps tests that rely
on a one-minute throttle window from straddling a window boundary.
"""

import inspect
import re
import time
from functools import cache
from typing import Protocol

import pytest

SERVICES_MARKER = "services"
API_RUNTIME_MARKER = "api_runtime"
API_RUNTIME_EXEMPT_MARKER = "api_runtime_exempt"
TEST_GENERAL_THROTTLE_RATE = "1000000/day"
CACHE_SERVICE = "valkey-cache"
MINUTE_WINDOW_SECONDS = 60
WINDOW_GUARD_MARGIN_SECONDS = 15.0
WINDOW_GUARD_SETTLE_SECONDS = 0.25
MINUTE_RATE_LITERAL = re.compile(r'"\d+/minute"')


class MutableThrottleSettings(Protocol):
    """Describe the settings values the integration harness raises.

    Inherits from ``Protocol`` and exposes only the three general rates adjusted for parallel test
    isolation, leaving every authoritative ticket-owned security rate unchanged.

    Attributes:
        API_BOUNDARY_ADDRESS_THROTTLE_RATE: Broad pre-framework source rate.
        API_AUTHENTICATION_THROTTLE_RATE: Aggregate authentication and recovery rate.
        API_AUTHENTICATED_READ_THROTTLE_RATE: Authenticated read rate.
        API_ANONYMOUS_THROTTLE_RATE: Anonymous API rate.

    Members:
        None.
    """

    API_BOUNDARY_ADDRESS_THROTTLE_RATE: str
    API_AUTHENTICATION_THROTTLE_RATE: str
    API_AUTHENTICATED_READ_THROTTLE_RATE: str
    API_ANONYMOUS_THROTTLE_RATE: str


@pytest.fixture(autouse=True)
def isolate_general_api_throttle_scopes(settings: MutableThrottleSettings) -> None:
    """Keep unrelated integration requests outside shared general scope limits.

    Raises only the broad cache-backed test defaults so parallel tests sharing loopback addresses
    cannot consume one another's budgets; focused scope tests override these values explicitly.

    Arguments:
        settings: Pytest-Django settings fixture.

    Returns:
        None.
    """
    settings.API_BOUNDARY_ADDRESS_THROTTLE_RATE = TEST_GENERAL_THROTTLE_RATE
    settings.API_AUTHENTICATION_THROTTLE_RATE = TEST_GENERAL_THROTTLE_RATE
    settings.API_AUTHENTICATED_READ_THROTTLE_RATE = TEST_GENERAL_THROTTLE_RATE
    settings.API_ANONYMOUS_THROTTLE_RATE = TEST_GENERAL_THROTTLE_RATE


def seconds_to_window_end(now: float, window_seconds: int = MINUTE_WINDOW_SECONDS) -> float:
    """Measure the time left in the epoch-aligned fixed window that contains an instant.

    The throttle counts requests in windows aligned to the Unix epoch, so two requests only share a
    budget when no window boundary falls between them.

    Arguments:
        now: Seconds since the Unix epoch.
        window_seconds: Positive fixed-window length.

    Returns:
        Seconds until the next window boundary.
    """
    return window_seconds - (now % window_seconds)


@cache
def names_minute_rate(source: str) -> bool:
    """Report whether test source declares a count-per-minute throttle rate literal.

    Matches quoted rates such as ``"1/minute"``, so only tests that exercise a one-minute window
    pay for the boundary guard.

    Arguments:
        source: Source text of one test function.

    Returns:
        Whether the source contains a quoted per-minute rate.
    """
    return MINUTE_RATE_LITERAL.search(source) is not None


def window_boundary_delay(source: str, now: float) -> float:
    """Compute how long a test must wait so its throttle requests share one window.

    A test that names a per-minute rate waits out the last seconds of the current window; every
    other test starts immediately.

    Arguments:
        source: Source text of the test function.
        now: Seconds since the Unix epoch.

    Returns:
        Seconds to sleep before the test starts, or zero.
    """
    if not names_minute_rate(source):
        return 0.0
    remaining = seconds_to_window_end(now)
    if remaining < WINDOW_GUARD_MARGIN_SECONDS:
        return remaining + WINDOW_GUARD_SETTLE_SECONDS
    return 0.0


@pytest.fixture(autouse=True)
def keep_minute_throttle_tests_inside_one_window(request: pytest.FixtureRequest) -> None:
    """Start per-minute throttle tests with room before the next window boundary.

    A test that fires two requests and expects the second to be throttled fails when the boundary
    falls between them. Waiting out the last seconds of a window before such a test starts makes
    that impossible without fixing or weakening any limit.

    Arguments:
        request: Pytest fixture request describing the running test.

    Returns:
        None.
    """
    if request.function is not None:
        time.sleep(window_boundary_delay(inspect.getsource(request.function), time.time()))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Require a usable services declaration from every integration test.

    Inspects every collected test, which pytest hands to this hook whatever tree it came from, and
    fails the run when an integration test names no service, because an undeclared dependency is
    what makes an integration failure hard to attribute.

    Arguments:
        items: Tests collected for this run.

    Returns:
        None.

    Raises:
        pytest.UsageError: If any integration test names no service.
    """
    undeclared: list[str] = []
    missing_cache: list[str] = []

    for item in items:
        if item.get_closest_marker("integration") is None:
            continue

        marker = item.get_closest_marker(SERVICES_MARKER)
        named = marker is not None and bool(marker.args)
        usable = named and all(
            isinstance(name, str) and name.strip()
            for name in marker.args  # type: ignore[union-attr]
        )

        if not usable:
            undeclared.append(item.nodeid)
            continue

        assert marker is not None
        services = {name.strip() for name in marker.args if isinstance(name, str)}
        api_runtime = item.get_closest_marker(API_RUNTIME_MARKER) is not None
        api_runtime_exempt = item.get_closest_marker(API_RUNTIME_EXEMPT_MARKER) is not None
        if api_runtime and not api_runtime_exempt and CACHE_SERVICE not in services:
            missing_cache.append(item.nodeid)

    if undeclared or missing_cache:
        sections: list[str] = []
        if undeclared:
            listed = "\n".join(f"  {nodeid}" for nodeid in undeclared)
            sections.append(f"every integration test must name the services it needs:\n{listed}")
        if missing_cache:
            listed = "\n".join(f"  {nodeid}" for nodeid in missing_cache)
            sections.append(
                f"tests marked {API_RUNTIME_MARKER} for the fixed /api/v1/ boundary must name "
                f"{CACHE_SERVICE}:\n{listed}"
            )
        message = "\n".join(sections)
        raise pytest.UsageError(message)
