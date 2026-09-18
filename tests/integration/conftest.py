"""Shared fixtures and guards for the integration layer.

Requires every integration test to name the services it needs, so a failure names the service that
was unavailable rather than surfacing as an unexplained connection error.
"""

from typing import Protocol

import pytest

SERVICES_MARKER = "services"
API_RUNTIME_MARKER = "api_runtime"
API_RUNTIME_EXEMPT_MARKER = "api_runtime_exempt"
TEST_GENERAL_THROTTLE_RATE = "1000000/day"
CACHE_SERVICE = "valkey-cache"


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
