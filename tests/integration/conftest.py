"""Shared fixtures and guards for the integration layer.

Requires every integration test to name the services it needs, so a failure names the service that
was unavailable rather than surfacing as an unexplained connection error.
"""

import pytest

SERVICES_MARKER = "services"


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

    if undeclared:
        listed = "\n".join(f"  {nodeid}" for nodeid in undeclared)
        message = f"every integration test must name the services it needs:\n{listed}"
        raise pytest.UsageError(message)
