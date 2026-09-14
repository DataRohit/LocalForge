"""Shared fixtures for the whole test suite.

Provides the per-worker namespace every externally allocated name is built from, so two workers
running the same test in parallel cannot collide on a bucket, a queue, or a cache key.
"""

import os

import pytest

SERIAL_GROUP = "serial"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Pin every serial test to one worker.

    Stamps each test marked serial with a shared distribution group, so the marker actually
    serialises rather than documenting an intention the parallel runner ignores.

    Arguments:
        items: Tests collected for this run.

    Returns:
        None.

    Raises:
        None.
    """
    for item in items:
        if item.get_closest_marker(SERIAL_GROUP) is not None:
            item.add_marker(pytest.mark.xdist_group(SERIAL_GROUP))


@pytest.fixture(scope="session")
def worker_id() -> str:
    """Name the worker this test is running on.

    Reads the identifier the parallel runner exports, falling back to a fixed name when the suite
    runs in one process, so a fixture can namespace a resource without knowing how it was invoked.

    Arguments:
        None.

    Returns:
        The worker identifier, such as ``gw0``, or ``main`` in a serial run.

    Raises:
        None.
    """
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


@pytest.fixture(scope="session")
def worker_namespace(worker_id: str) -> str:
    """Build the prefix every externally allocated name carries.

    Combines the project name with the worker identifier, so a bucket, queue, group, or cache key
    created by one worker cannot be seen, mutated, or deleted by another running at the same time.

    Arguments:
        worker_id: Identifier of the worker this test runs on.

    Returns:
        The prefix to put in front of any externally allocated name.

    Raises:
        None.
    """
    return f"localforge-test-{worker_id}"
